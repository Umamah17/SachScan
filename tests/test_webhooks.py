"""Webhook security and behaviour (no network: Graph API and the pipeline are mocked)."""
import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from backend import config, main
from backend.channels import common, whatsapp
from backend.pipeline.schemas import CheckResponse, ClaimResult, Source

client = TestClient(main.app)
SECRET = "test-app-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def wa_payload(*messages, phone_id="PNID"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_id}, "messages": list(messages)}}]}]}


@pytest.fixture
def wa(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "PNID")
    sent, checked = [], []

    async def fake_post(payload):
        sent.append(payload)
        return {}

    async def fake_check(inp, **kw):
        checked.append(inp)
        r = CheckResponse(language="en", overall_summary="This is false.", overall_verdict="false", claims=[
            ClaimResult(claim="Hot water cures COVID", verdict="false", explanation="WHO says no.",
                        sources=[Source(publisher="who.int", url="https://who.int/x", date="2020-03-01")])])
        return r, "❌ *FALSE* formatted reply"

    monkeypatch.setattr(whatsapp, "_post_messages", fake_post)
    monkeypatch.setattr(common, "handle_check", fake_check)
    whatsapp._recent.data.clear()
    return sent, checked


# ---- signature ---------------------------------------------------------------

def test_signature_valid_invalid_missing(wa):
    body = json.dumps(wa_payload()).encode()
    assert whatsapp.verify_signature(body, sign(body))
    assert not whatsapp.verify_signature(body, sign(body, "wrong-secret"))
    assert not whatsapp.verify_signature(body + b" ", sign(body))          # tampered body
    assert not whatsapp.verify_signature(body, None)
    assert not whatsapp.verify_signature(body, "sha1=" + "0" * 40)


def test_post_rejects_bad_signature(wa):
    body = json.dumps(wa_payload({"from": "911", "id": "wamid.X", "type": "text", "text": {"body": "hi"}})).encode()
    assert client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body, "nope")}).status_code == 401
    assert client.post("/webhooks/whatsapp", content=body).status_code == 401
    sent, checked = wa
    assert sent == [] and checked == []


def test_unsigned_requests_rejected_when_secret_missing(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    body = b"{}"
    assert client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body, "")}).status_code == 401


# ---- verification handshake ----------------------------------------------------

def test_verify_handshake(wa):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})
    assert bad.status_code == 403


# ---- message flow ----------------------------------------------------------------

def test_text_message_checked_and_replied_once(wa):
    sent, checked = wa
    msg = {"from": "919876543210", "id": "wamid.TEXT1", "type": "text", "text": {"body": "Hot water cures COVID"}}
    body = json.dumps(wa_payload(msg)).encode()
    r = client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 200
    assert len(checked) == 1
    inp = checked[0]
    assert inp.platform == "whatsapp" and inp.external_user_id == "919876543210" and inp.original_message_id == "wamid.TEXT1"
    replies = [p for p in sent if p.get("type") == "text"]
    assert len(replies) == 1 and replies[0]["context"] == {"message_id": "wamid.TEXT1"}
    assert replies[0]["text"]["body"].startswith("❌")

    # Meta retries the same webhook: no second check, no second reply
    client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert len(checked) == 1 and len([p for p in sent if p.get("type") == "text"]) == 1


def test_help_and_link_commands(wa, monkeypatch):
    sent, checked = wa
    redeemed = []

    async def fake_redeem(platform, ext, code):
        redeemed.append((platform, ext, code))
        return "✅ Linked!"

    monkeypatch.setattr(common, "redeem_link_code", fake_redeem)
    for i, text in enumerate(["help", "link ab12cd"]):
        body = json.dumps(wa_payload({"from": "91999", "id": f"wamid.CMD{i}", "type": "text", "text": {"body": text}})).encode()
        client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    bodies = [p["text"]["body"] for p in sent if p.get("type") == "text"]
    assert "Privacy" in bodies[0]                       # help includes the privacy notice
    assert bodies[1] == "✅ Linked!" and redeemed == [("whatsapp", "91999", "ab12cd")]
    assert checked == []                                # commands are not fact-checked


def test_media_too_large_gets_friendly_reply(wa, monkeypatch):
    sent, checked = wa

    async def big(media_id, limit_mb):
        raise whatsapp.MediaError(f"That file is too large. I can check files up to {limit_mb} MB.")

    monkeypatch.setattr(whatsapp, "download_media", big)
    msg = {"from": "91888", "id": "wamid.DOC", "type": "document", "document": {"id": "m1", "mime_type": "application/pdf", "filename": "x.pdf"}}
    body = json.dumps(wa_payload(msg)).encode()
    client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    texts = [p["text"]["body"] for p in sent if p.get("type") == "text"]
    assert texts and "10 MB" in texts[-1] and checked == []


def test_non_pdf_document_rejected(wa):
    sent, checked = wa
    msg = {"from": "91777", "id": "wamid.DOCX", "type": "document", "document": {"id": "m2", "mime_type": "application/msword"}}
    body = json.dumps(wa_payload(msg)).encode()
    client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert checked == [] and "PDF" in [p for p in sent if p.get("type") == "text"][-1]["text"]["body"]


def test_voice_note_is_downloaded_and_checked(wa, monkeypatch):
    sent, checked = wa

    async def fake_download(media_id, limit_mb):
        assert media_id == "voice1" and limit_mb == config.MAX_MEDIA_MB
        return b"OggS-fake-audio", "audio/ogg; codecs=opus"

    monkeypatch.setattr(whatsapp, "download_media", fake_download)
    msg = {"from": "91666", "id": "wamid.VOICE", "type": "audio", "audio": {"id": "voice1", "mime_type": "audio/ogg; codecs=opus", "voice": True}}
    body = json.dumps(wa_payload(msg)).encode()
    client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert checked[0].content_type == "audio" and checked[0].file_bytes == b"OggS-fake-audio"
    assert any(p.get("status") == "read" for p in sent)  # read receipt + typing indicator


def test_other_phone_number_ignored(wa):
    sent, checked = wa
    msg = {"from": "91555", "id": "wamid.OTHER", "type": "text", "text": {"body": "x"}}
    body = json.dumps(wa_payload(msg, phone_id="SOMEONE_ELSE")).encode()
    client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert checked == [] and sent == []


# ---- shared formatter ------------------------------------------------------------

def test_chat_formatter_limits_to_three_claims():
    from backend.pipeline.formatter import format_chat

    r = CheckResponse(language="en", overall_summary="Mixed.", overall_verdict="partly_supported",
                      claims=[ClaimResult(claim=f"c{i}", verdict="false", explanation="e",
                                          sources=[Source(publisher="pib.gov.in", url=f"https://pib.gov.in/{i}")]) for i in range(5)],
                      scam_signals=["Asks for OTP"])
    text = format_chat(r, "https://app.example/help.html?c=1")
    assert "c0" in text and "c2" in text and "c3" not in text and "(+2 more)" in text
    assert "https://pib.gov.in/0" in text and "Asks for OTP" in text and text.endswith("https://app.example/help.html?c=1")
