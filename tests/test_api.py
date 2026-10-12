"""HTTP API: auth required, persistence wiring, static serving, share target."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api import check as check_api
from backend.api.deps import CurrentUser, get_current_user
from backend.pipeline.schemas import CheckResponse, ClaimResult

client = TestClient(main.app)
USER = CurrentUser(id="00000000-0000-0000-0000-000000000001", email="a@example.com", name="A", avatar_url="", token="tok")


@pytest.fixture
def signed_in(monkeypatch):
    saved = []

    async def fake_check(inp, progress=None):
        if progress:
            await progress({"type": "progress", "step": "extract", "message": "…"})
        return CheckResponse(language="en", overall_summary="Fake.", overall_verdict="false",
                             claims=[ClaimResult(claim="c", verdict="false")])

    async def fake_save(**kw):
        saved.append(kw)
        return "conv-1"

    monkeypatch.setattr(check_api, "check_content", fake_check)
    monkeypatch.setattr(check_api.chatlog, "save_exchange", fake_save)
    main.app.dependency_overrides[get_current_user] = lambda: USER
    yield saved
    main.app.dependency_overrides.clear()


def test_protected_endpoints_need_sign_in():
    for method, path in [("post", "/check"), ("post", "/chat"), ("post", "/api/check/stream"), ("get", "/conversations"),
                         ("delete", "/conversations/x"), ("post", "/link/start")]:
        r = getattr(client, method)(path, json={"text": "hi"}) if method == "post" else getattr(client, method)(path)
        assert r.status_code in (401, 503), (path, r.status_code)


def test_public_endpoints():
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/config").status_code == 200


def test_chat_saves_exchange(signed_in):
    r = client.post("/chat", data={"text": "Bananas cure cancer", "conversation_id": "conv-9"})
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"] == "conv-1" and body["result"]["overall_verdict"] == "false"
    s = signed_in[0]
    assert s["platform"] == "web" and s["user_id"] == USER.id and s["user_token"] == "tok"
    assert s["conversation_id"] == "conv-9" and s["result"].overall_summary == "Fake."


def test_stream_keeps_event_shape(signed_in):
    r = client.post("/api/check/stream", data={"text": "Bananas cure cancer"})
    types = [json.loads(line)["type"] for line in r.text.splitlines()]
    assert types == ["progress", "result", "conversation"]


def test_check_returns_schema(signed_in):
    d = client.post("/check", json={"text": "Bananas cure cancer"}).json()
    assert {"input_type", "language", "extracted_text", "overall_summary", "claims", "scam_signals"} <= d.keys()
    assert d["meta"]["conversation_id"] == "conv-1"


def test_static_files_and_secrets_blocked():
    assert client.get("/help").status_code == 200
    for path in ("/backend/.env", "/backend/config.py", "/supabase/migrations/x.sql", "/.git/config"):
        assert client.get(path).status_code == 404, path


def test_share_target_redirects():
    r = client.post("/share-target", data={"text": "Fwd: something"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/help.html?shared=")
