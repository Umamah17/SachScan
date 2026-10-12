"""WhatsApp channel: official WhatsApp Business Cloud API (our bot number).

Users forward messages TO the bot and it replies. Personal WhatsApp chats can't be read
(end-to-end encrypted), and unofficial libraries (Baileys, whatsapp-web.js) are not used.

GET  /webhooks/whatsapp   verification handshake (hub.mode / hub.verify_token / hub.challenge)
POST /webhooks/whatsapp   X-Hub-Signature-256 verified -> 200 at once -> processed in background

Exactly one reply message per check (service messages are billed per message).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import OrderedDict
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from backend import config
from backend.channels import common
from backend.db import chatlog
from backend.pipeline.core import NormalizedInput

log = logging.getLogger("sachscan.whatsapp")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

GRAPH = "https://graph.facebook.com"
MAX_REPLY = 4096
MB = 1024 * 1024

# media type -> (our content_type, size limit MB)
MEDIA = {
    "image": ("image", lambda: config.MAX_MEDIA_MB),
    "audio": ("audio", lambda: config.MAX_MEDIA_MB),
    "video": ("video", lambda: config.MAX_MEDIA_MB),
    "document": ("pdf", lambda: config.MAX_PDF_MB),
    "sticker": ("image", lambda: config.MAX_MEDIA_MB),
}
HELP_WORDS = {"help", "hi", "hello", "start", "menu", "/help", "/start", "namaste", "नमस्ते", "मदद"}


def configured() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """X-Hub-Signature-256: 'sha256=' + HMAC-SHA256(app secret, raw body)."""
    if not config.WHATSAPP_APP_SECRET or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(config.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


class _RecentIds:
    """Webhook retries arrive within seconds; this catches them before touching the database."""

    def __init__(self, size: int = 2000, ttl: int = 3600):
        self.size, self.ttl, self.data = size, ttl, OrderedDict()

    def seen(self, key: str) -> bool:
        now = time.time()
        while self.data and now - next(iter(self.data.values())) > self.ttl:
            self.data.popitem(last=False)
        if key in self.data:
            return True
        self.data[key] = now
        if len(self.data) > self.size:
            self.data.popitem(last=False)
        return False


_recent = _RecentIds()


# ---------------------------------------------------------------------------
# Graph API calls
# ---------------------------------------------------------------------------

def _auth() -> dict:
    return {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}


async def _post_messages(payload: dict) -> Optional[dict]:
    url = f"{GRAPH}/{config.WHATSAPP_GRAPH_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, headers=_auth(), json={"messaging_product": "whatsapp", **payload})
            if r.status_code < 400:
                return r.json()
            if r.status_code < 500 or attempt == 2:
                log.warning("WhatsApp send failed: HTTP %s %s", r.status_code, r.text[:200])
                return None
        except httpx.HTTPError as e:
            if attempt == 2:
                log.warning("WhatsApp send error: %s", type(e).__name__)
    return None


async def send_text(to: str, body: str, reply_to: Optional[str] = None) -> None:
    payload = {"to": to, "type": "text", "text": {"preview_url": False, "body": body[:MAX_REPLY]}}
    if reply_to:
        payload["context"] = {"message_id": reply_to}
    await _post_messages(payload)


async def mark_read_typing(message_id: str) -> None:
    """Blue ticks + 'typing…' while we check (not a billed message)."""
    await _post_messages({"status": "read", "message_id": message_id, "typing_indicator": {"type": "text"}})


class MediaError(Exception):
    pass


async def download_media(media_id: str, limit_mb: int) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"{GRAPH}/{config.WHATSAPP_GRAPH_VERSION}/{media_id}", headers=_auth())
        if meta.status_code >= 400:
            raise MediaError("Could not fetch that file from WhatsApp. Please send it again.")
        info = meta.json()
        if int(info.get("file_size") or 0) > limit_mb * MB:
            raise MediaError(f"That file is too large. I can check files up to {limit_mb} MB.")
        r = await client.get(info["url"], headers=_auth())
        if r.status_code >= 400:
            raise MediaError("Could not download that file. Please send it again.")
        if len(r.content) > limit_mb * MB:
            raise MediaError(f"That file is too large. I can check files up to {limit_mb} MB.")
        return r.content, info.get("mime_type", "")


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

def extract_messages(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            if value.get("metadata", {}).get("phone_number_id") not in (None, config.WHATSAPP_PHONE_NUMBER_ID):
                continue  # another number on the same app
            out.extend(value.get("messages", []) or [])
    return out


async def handle_message(msg: dict) -> None:
    sender, msg_id, mtype = msg.get("from"), msg.get("id"), msg.get("type")
    if not sender or not msg_id or _recent.seen(msg_id) or await chatlog.already_processed(msg_id):
        return
    log.info("whatsapp message type=%s", mtype)

    # ---- commands -------------------------------------------------------------
    if mtype == "text":
        body = (msg.get("text") or {}).get("body", "").strip()
        if body.lower() in HELP_WORDS:
            await send_text(sender, common.help_text("whatsapp"), reply_to=msg_id)
            return
        code = common.parse_link_command(body)
        if code:
            await send_text(sender, await common.redeem_link_code("whatsapp", sender, code), reply_to=msg_id)
            return

    # ---- build NormalizedInput -------------------------------------------------
    inp = NormalizedInput(platform="whatsapp", external_user_id=sender, original_message_id=msg_id)
    try:
        if mtype == "text":
            inp.text = (msg.get("text") or {}).get("body", "")
            inp.content_type = "url" if inp.text.strip().startswith(("http://", "https://")) and " " not in inp.text.strip() else "text"
        elif mtype in MEDIA:
            media = msg.get(mtype) or {}
            ctype, limit = MEDIA[mtype]
            mime = media.get("mime_type", "")
            if mtype == "document" and "pdf" not in mime.lower():
                await send_text(sender, "📄 I can check PDF documents, images, voice notes, videos, links and text. That file type isn't supported.", reply_to=msg_id)
                return
            await mark_read_typing(msg_id)
            data, mime2 = await download_media(media.get("id", ""), limit())
            inp.content_type, inp.file_bytes, inp.mime_type = ctype, data, mime2 or mime
            inp.file_name = media.get("filename", "") or f"whatsapp-{mtype}"
            inp.text = media.get("caption", "") or ""
        elif mtype == "interactive" or mtype == "button":
            await send_text(sender, common.help_text("whatsapp"), reply_to=msg_id)
            return
        else:
            await send_text(sender, "I can check text, links, screenshots, voice notes, PDFs and videos. Send *help* for details.", reply_to=msg_id)
            return
    except MediaError as e:
        await send_text(sender, f"⚠️ {e}", reply_to=msg_id)
        return

    if mtype == "text":
        await mark_read_typing(msg_id)
    _, reply = await common.handle_check(inp, bold="*", limit=MAX_REPLY)
    await send_text(sender, reply, reply_to=msg_id)


async def process_payload(payload: dict) -> None:
    for msg in extract_messages(payload):
        try:
            await handle_message(msg)
        except Exception:
            log.exception("whatsapp message handling failed")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
async def verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and hmac.compare_digest(hub_verify_token, config.WHATSAPP_VERIFY_TOKEN):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(403, "Verification failed")


@router.post("")
async def receive(request: Request, background: BackgroundTasks, x_hub_signature_256: Optional[str] = Header(None)):
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")
    if not configured():
        return {"ok": True}  # signed but we cannot reply yet; acknowledge so Meta doesn't retry
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": True}
    background.add_task(process_payload, payload)  # respond 200 immediately
    return {"ok": True}
