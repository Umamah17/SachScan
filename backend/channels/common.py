"""Helpers shared by every bot channel: account linking, rate limits, privacy notice, and the
standard "check + save + format" flow. Channels convert their input to NormalizedInput and call
`handle_check`; they never touch the pipeline internals."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend import config, limits
from backend.db import chatlog
from backend.db import supabase as db
from backend.pipeline.core import CheckError, NormalizedInput, check_content
from backend.pipeline.formatter import format_chat
from backend.pipeline.schemas import CheckResponse

log = logging.getLogger("sachscan.channels")

LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
LINK_CODE_TTL_MIN = 10
LINK_RE = re.compile(r"^\s*/?link\s+([A-Za-z0-9]{6})\s*$", re.I)

PRIVACY_NOTICE = (
    f"🔒 Privacy: we save your checks so you can see them in your SachScan history; "
    f"files you send are deleted after {config.UPLOAD_RETENTION_HOURS} hours."
)


def help_text(platform: str) -> str:
    how = {
        "whatsapp": "Forward any message, screenshot, voice note, PDF or link to this number",
        "telegram": "Forward or send me any message, photo, voice note, PDF or link",
        "discord": "Use /check, or right-click a message → Apps → Check with SachScan",
    }.get(platform, "Send me any message, screenshot, voice note, PDF or link")
    return (
        "🙏 *SachScan* checks forwards against trusted sources (PIB Fact Check, *.gov.in, WHO, fact-checkers) "
        "and replies with an evidence-backed verdict.\n\n"
        f"• {how}.\n"
        "• Send *link <code>* to connect this chat to your SachScan account (get the code on the website).\n"
        "• Send *help* to see this again.\n\n" + PRIVACY_NOTICE
    )


# ---------------------------------------------------------------------------
# Account linking
# ---------------------------------------------------------------------------

async def create_link_code(user_id: str) -> tuple[str, datetime]:
    code = "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(6))
    expires = datetime.now(timezone.utc) + timedelta(minutes=LINK_CODE_TTL_MIN)
    svc = db.service()
    await svc.delete("link_codes", {"user_id": f"eq.{user_id}"})  # one active code per user
    await svc.insert("link_codes", {"code": code, "user_id": user_id, "expires_at": expires.isoformat()}, returning=False)
    return code, expires


async def redeem_link_code(platform: str, external_user_id: str, code: str) -> str:
    """Link a platform id to the account that created `code`. Returns a reply message."""
    if not db.configured():
        return "Account linking is not available right now."
    svc = db.service()
    rows = await svc.select("link_codes", {"code": f"eq.{code.upper()}", "select": "user_id,expires_at", "limit": 1})
    if not rows or datetime.fromisoformat(rows[0]["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
        return "❌ That code is invalid or expired. Get a new one on the SachScan website (History → Link a chat app)."
    user_id = rows[0]["user_id"]
    await svc.insert("platform_links", {"user_id": user_id, "platform": platform, "external_user_id": external_user_id},
                     upsert=True, on_conflict="platform,external_user_id", returning=False)
    await svc.delete("link_codes", {"code": f"eq.{code.upper()}"})
    # Attach earlier chats from this platform id to the account
    await svc.update("conversations", {"platform": f"eq.{platform}", "external_user_id": f"eq.{external_user_id}", "user_id": "is.null"}, {"user_id": user_id})
    return "✅ Linked! Your checks from here will now appear in your SachScan history on the website."


def parse_link_command(text: str) -> Optional[str]:
    m = LINK_RE.match(text or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Standard flow for bot channels
# ---------------------------------------------------------------------------

async def handle_check(inp: NormalizedInput, *, bold: str = "*", limit: int = 4000) -> tuple[Optional[CheckResponse], str]:
    """Rate-limit, check, save, and return (result, reply text formatted for the platform)."""
    key = f"{inp.platform}:{inp.external_user_id}"
    try:
        limits.hit(key)
    except limits.RateLimited as e:
        return None, f"⏳ {e}"
    if inp.user_id is None:
        inp.user_id = await chatlog.resolve_user(inp.platform, inp.external_user_id)
    result, error = None, None
    try:
        result = await check_content(inp)
    except CheckError as e:
        error = str(e)
        if e.status in (400, 413, 415):
            limits.undo(key)
    except Exception:
        log.exception("%s check failed", inp.platform)
        error = "Sorry, something went wrong while checking. Please try again."
    conv = await chatlog.save_exchange(
        platform=inp.platform, content_type=inp.content_type, text=inp.text, file_bytes=inp.file_bytes,
        mime_type=inp.mime_type, file_name=inp.file_name, user_id=inp.user_id, external_user_id=inp.external_user_id,
        platform_message_id=inp.original_message_id, result=result, error=error,
    )
    if result is None:
        return None, f"⚠️ {error}"
    full_url = f"{config.PUBLIC_WEB_URL}/help.html?c={conv}" if (conv and inp.user_id and config.PUBLIC_WEB_URL) else (
        f"{config.PUBLIC_WEB_URL}/help.html" if config.PUBLIC_WEB_URL else None)
    return result, format_chat(result, full_url, bold=bold, limit=limit)


# ---------------------------------------------------------------------------
# Opt-in auto-scan settings per Discord guild / Telegram group (integration_settings)
# ---------------------------------------------------------------------------

FLAG_VERDICTS = {"false", "outdated"}


def should_flag(result: Optional[CheckResponse]) -> bool:
    """Auto-scan only speaks up for false/outdated claims or scam signals; otherwise it stays quiet."""
    if result is None:
        return False
    return result.overall_verdict in FLAG_VERDICTS or any(c.verdict in FLAG_VERDICTS for c in result.claims) or bool(result.scam_signals)


async def get_space_settings(platform: str, space_id: str) -> dict:
    if not db.configured():
        return {}
    try:
        rows = await db.service().select("integration_settings", {"platform": f"eq.{platform}", "external_space_id": f"eq.{space_id}", "select": "settings", "limit": 1})
        return rows[0]["settings"] if rows else {}
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("settings read failed: %s", e)
        return {}


async def save_space_settings(platform: str, space_id: str, settings: dict, enabled_by: str) -> None:
    await db.service().insert("integration_settings", {
        "platform": platform, "external_space_id": space_id, "settings": settings, "enabled_by": enabled_by,
    }, upsert=True, on_conflict="platform,external_space_id", returning=False)


async def list_space_settings(platform: str) -> dict[str, dict]:
    if not db.configured():
        return {}
    try:
        rows = await db.service().select("integration_settings", {"platform": f"eq.{platform}", "select": "external_space_id,settings"})
        return {r["external_space_id"]: r["settings"] or {} for r in rows}
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("settings list failed: %s", e)
        return {}
