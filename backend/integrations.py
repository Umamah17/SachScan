"""Messaging gateways shown on integrations.html.

Live:     Web / PWA share target (main.py), Telegram bot (webhook), Discord app (interactions).
Planned:  WhatsApp tipline (paid Business API; out of scope for now per project plan),
          Gmail, X, Reddit, Instagram, Threads.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

import config
from extract import ExtractionError
from pipeline import run_check
from schemas import CheckResponse

log = logging.getLogger("sachscan.integrations")
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

VERDICT_ICON = {"verified": "✅", "false": "❌", "outdated": "🕰️", "partly_supported": "🔷", "cannot_be_confirmed": "❔"}
VERDICT_LABEL = {
    "en": {"verified": "Verified", "false": "False", "outdated": "Outdated", "partly_supported": "Partly supported", "cannot_be_confirmed": "Cannot be confirmed"},
    "hi": {"verified": "सही पाया गया", "false": "गलत", "outdated": "पुरानी जानकारी", "partly_supported": "आंशिक रूप से सही", "cannot_be_confirmed": "पुष्टि नहीं हो सकी"},
    "mr": {"verified": "खरे आढळले", "false": "खोटे", "outdated": "जुनी माहिती", "partly_supported": "अंशतः खरे", "cannot_be_confirmed": "पुष्टी होऊ शकली नाही"},
}


def format_plain(result: CheckResponse, limit: int = 3900) -> str:
    labels = VERDICT_LABEL.get(result.language, VERDICT_LABEL["en"])
    lines = [f"{VERDICT_ICON[result.overall_verdict]} {labels[result.overall_verdict].upper()}", "", result.overall_summary, ""]
    for i, c in enumerate(result.claims, 1):
        lines.append(f"{i}. {VERDICT_ICON[c.verdict]} {labels[c.verdict]}: {c.claim}")
        if c.explanation:
            lines.append(f"   {c.explanation}")
        for s in c.sources[:2]:
            lines.append(f"   🔗 {s.publisher or s.title}{' (' + s.date + ')' if s.date else ''}: {s.url}")
        lines.append("")
    if result.scam_signals:
        lines.append("⚠️ " + " • ".join(result.scam_signals[:4]))
        lines.append("")
    if result.share_safe_reply:
        lines.append("💬 " + result.share_safe_reply)
    text = "\n".join(lines).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def check_to_result(**kwargs) -> tuple[Optional[CheckResponse], str]:
    try:
        async for ev in run_check(**kwargs):
            if ev["type"] == "result":
                return ev["data"], ""
            if ev["type"] == "error":
                return None, ev["message"]
    except ExtractionError as e:
        return None, str(e)
    except Exception:
        log.exception("integration check failed")
        return None, "Sorry, something went wrong while checking. Please try again."
    return None, "No result."


# ---------------------------------------------------------------------------
# Status (for the integrations page / health dashboards)
# ---------------------------------------------------------------------------

def _links(site: str) -> dict[str, dict]:
    """Where each integrations.html card takes the user.

    A configured account (.env) is opened directly. Otherwise the app opens with a ready-made
    share message pointing to the SachScan checker. Placeholder handles printed on the cards are
    never linked, since they may belong to someone else."""
    q = quote
    share = f"Got a suspicious WhatsApp forward? Check it with SachScan before sharing: {site}/help.html"
    c = config
    return {
        "whatsapp": {"url": f"https://wa.me/{c.WHATSAPP_NUMBER}?text={q('Hi SachScan, please check this forward:')}", "mode": "account"}
        if c.WHATSAPP_NUMBER else {"url": f"https://wa.me/?text={q(share)}", "mode": "share"},
        "telegram": {"url": f"https://t.me/{c.TELEGRAM_BOT_USERNAME}", "mode": "account"}
        if c.TELEGRAM_BOT_USERNAME else {"url": f"https://t.me/share/url?url={q(site + '/help.html')}&text={q('Check forwards with SachScan before sharing')}", "mode": "share"},
        "discord": {"url": c.DISCORD_INVITE_URL, "mode": "account"} if c.DISCORD_INVITE_URL
        else {"url": f"https://discord.com/oauth2/authorize?client_id={c.DISCORD_APPLICATION_ID}&scope=applications.commands", "mode": "account"}
        if c.DISCORD_APPLICATION_ID else {"url": "https://discord.com/app", "mode": "app"},
        "gmail": {"url": f"https://mail.google.com/mail/?view=cm&fs=1&to={q(c.CONTACT_EMAIL)}&su={q('Please verify this message')}&body={q('Paste or forward the suspicious message here:')}", "mode": "account"}
        if c.CONTACT_EMAIL else {"url": f"https://mail.google.com/mail/?view=cm&fs=1&su={q('Check forwards with SachScan')}&body={q(share)}", "mode": "share"},
        "x": {"url": f"https://x.com/{c.X_HANDLE}", "mode": "account"} if c.X_HANDLE
        else {"url": f"https://x.com/intent/post?text={q(share)}", "mode": "share"},
        "reddit": {"url": f"https://www.reddit.com/r/{c.REDDIT_COMMUNITY}/", "mode": "account"} if c.REDDIT_COMMUNITY
        else {"url": f"https://www.reddit.com/submit?url={q(site + '/help.html')}&title={q('SachScan: check WhatsApp forwards against trusted sources')}", "mode": "share"},
        "instagram": {"url": f"https://www.instagram.com/{c.INSTAGRAM_HANDLE}/", "mode": "account"} if c.INSTAGRAM_HANDLE
        else {"url": "https://www.instagram.com/", "mode": "app"},
        "threads": {"url": f"https://www.threads.net/@{c.THREADS_HANDLE}", "mode": "account"} if c.THREADS_HANDLE
        else {"url": f"https://www.threads.net/intent/post?text={q(share)}", "mode": "share"},
    }


@router.get("")
async def list_integrations(request: Request):
    site = config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    links = _links(site)
    status = {
        "telegram": "live" if config.TELEGRAM_BOT_TOKEN else "not_configured",
        "discord": "live" if config.DISCORD_PUBLIC_KEY else "not_configured",
        "whatsapp": "share_target",
    }
    names = {
        "whatsapp": "WhatsApp Sentinel", "discord": "Discord Bot", "telegram": "Telegram Bot", "gmail": "Gmail Sentinel",
        "x": "X Sentinel", "reddit": "Reddit Sentinel", "instagram": "Instagram Sentinel", "threads": "Threads Sentinel",
    }
    items = [{"id": "web", "name": "Web & PWA Share Target", "status": "live", "link": f"{site}/help.html"}]
    for app_id, name in names.items():
        items.append({"id": app_id, "name": name, "status": status.get(app_id, "share_link"), "link": links[app_id]["url"], "link_mode": links[app_id]["mode"]})
    return {"integrations": items}


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

TG_API = "https://api.telegram.org"


async def _tg(method: str, **payload):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{TG_API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}", json=payload)
        return r.json()


async def _tg_download(file_id: str) -> bytes:
    info = await _tg("getFile", file_id=file_id)
    path = info.get("result", {}).get("file_path")
    if not path:
        raise ExtractionError("Telegram did not return the file (bots can download files up to 20 MB).")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{TG_API}/file/bot{config.TELEGRAM_BOT_TOKEN}/{path}")
        r.raise_for_status()
        return r.content


async def _handle_telegram(message: dict) -> None:
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    if text.strip().startswith("/start") or text.strip().startswith("/help"):
        await _tg("sendMessage", chat_id=chat_id, text="Namaste! 🙏 Forward me any message, screenshot, voice note, PDF or link and I will check it against trusted sources (PIB Fact Check, *.gov.in, WHO, fact-checkers).")
        return
    kwargs: dict = {"text": text}
    try:
        if message.get("photo"):
            kwargs.update(file_bytes=await _tg_download(message["photo"][-1]["file_id"]), file_mime="image/jpeg", file_name="photo.jpg")
        elif message.get("voice") or message.get("audio"):
            media = message.get("voice") or message.get("audio")
            kwargs.update(file_bytes=await _tg_download(media["file_id"]), file_mime=media.get("mime_type", "audio/ogg"), file_name="voice.ogg")
        elif message.get("video") or message.get("video_note"):
            media = message.get("video") or message.get("video_note")
            kwargs.update(file_bytes=await _tg_download(media["file_id"]), file_mime=media.get("mime_type", "video/mp4"), file_name="video.mp4")
        elif message.get("document"):
            doc = message["document"]
            kwargs.update(file_bytes=await _tg_download(doc["file_id"]), file_mime=doc.get("mime_type", ""), file_name=doc.get("file_name", ""))
    except (ExtractionError, httpx.HTTPError) as e:
        await _tg("sendMessage", chat_id=chat_id, text=f"⚠️ {e}")
        return
    if not kwargs.get("file_bytes") and not text.strip():
        return
    await _tg("sendChatAction", chat_id=chat_id, action="typing")
    pending = await _tg("sendMessage", chat_id=chat_id, text="🔎 Checking against trusted sources… (5–15 s)", reply_to_message_id=message.get("message_id"))
    result, error = await check_to_result(**kwargs)
    reply = format_plain(result) if result else f"⚠️ {error}"
    msg_id = (pending.get("result") or {}).get("message_id")
    if msg_id:
        await _tg("editMessageText", chat_id=chat_id, message_id=msg_id, text=reply, disable_web_page_preview=True)
    else:
        await _tg("sendMessage", chat_id=chat_id, text=reply, disable_web_page_preview=True)


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, background: BackgroundTasks, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    if not config.TELEGRAM_BOT_TOKEN:
        raise HTTPException(404, "Telegram bot is not configured.")
    if config.TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "Bad secret token.")
    update = await request.json()
    message = update.get("message") or update.get("channel_post")
    if message:
        background.add_task(_handle_telegram, message)
    return {"ok": True}  # answer fast; Telegram retries slow webhooks


@router.post("/telegram/setup")
async def telegram_setup(request: Request):
    """Registers the webhook with Telegram. Only callable from the server machine itself."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Run this from the server machine (localhost).")
    if not config.TELEGRAM_BOT_TOKEN or not config.PUBLIC_BASE_URL:
        raise HTTPException(400, "Set TELEGRAM_BOT_TOKEN and PUBLIC_BASE_URL (your https ngrok/cloudflare URL) in backend/.env first.")
    payload = {"url": f"{config.PUBLIC_BASE_URL}/api/integrations/telegram/webhook", "allowed_updates": ["message", "channel_post"]}
    if config.TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = config.TELEGRAM_WEBHOOK_SECRET
    return await _tg("setWebhook", **payload)


# ---------------------------------------------------------------------------
# Discord (HTTP interactions: /check slash command + "Check with SachScan" message command)
# ---------------------------------------------------------------------------

def _discord_verify(signature: str, timestamp: str, body: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(config.DISCORD_PUBLIC_KEY))
        key.verify(bytes.fromhex(signature), timestamp.encode() + body)
        return True
    except (InvalidSignature, ValueError):
        return False


async def _discord_followup(app_id: str, token: str, text: str, attachment_url: Optional[str], content_type: str, filename: str) -> None:
    kwargs: dict = {"text": text}
    if attachment_url:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(attachment_url)
                r.raise_for_status()
                kwargs.update(file_bytes=r.content, file_mime=content_type, file_name=filename)
        except httpx.HTTPError:
            pass
    result, error = await check_to_result(**kwargs)
    reply = format_plain(result, limit=1990) if result else f"⚠️ {error}"
    async with httpx.AsyncClient(timeout=30) as client:
        await client.patch(f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original", json={"content": reply, "flags": 4})


@router.post("/discord/interactions")
async def discord_interactions(
    request: Request,
    background: BackgroundTasks,
    x_signature_ed25519: str = Header(""),
    x_signature_timestamp: str = Header(""),
):
    if not config.DISCORD_PUBLIC_KEY:
        raise HTTPException(404, "Discord app is not configured.")
    body = await request.body()
    if not _discord_verify(x_signature_ed25519, x_signature_timestamp, body):
        raise HTTPException(401, "invalid request signature")
    data = await request.json()
    if data.get("type") == 1:  # PING
        return {"type": 1}
    if data.get("type") != 2:
        return {"type": 4, "data": {"content": "Unsupported interaction."}}

    cmd = data.get("data", {})
    text, att_url, att_type, att_name = "", None, "", ""
    if cmd.get("type") == 3:  # message context-menu command
        msg = (cmd.get("resolved", {}).get("messages") or {}).get(cmd.get("target_id"), {})
        text = msg.get("content", "")
        atts = msg.get("attachments") or []
        if atts:
            att_url, att_type, att_name = atts[0].get("url"), atts[0].get("content_type", ""), atts[0].get("filename", "")
    else:
        for opt in cmd.get("options", []) or []:
            if opt.get("name") in ("text", "message", "claim"):
                text = str(opt.get("value", ""))
            elif opt.get("type") == 11:  # attachment option
                att = (cmd.get("resolved", {}).get("attachments") or {}).get(opt.get("value"), {})
                att_url, att_type, att_name = att.get("url"), att.get("content_type", ""), att.get("filename", "")
    if not text.strip() and not att_url:
        return {"type": 4, "data": {"content": "Give me some text, a link or an attachment to check.", "flags": 64}}

    app_id = data.get("application_id") or config.DISCORD_APPLICATION_ID
    background.add_task(_discord_followup, app_id, data["token"], text, att_url, att_type, att_name)
    return JSONResponse({"type": 5})  # deferred: "SachScan is thinking…"
