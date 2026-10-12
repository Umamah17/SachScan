"""Chat-log persistence: every exchange from every channel is saved as
(user message, assistant result) inside a conversation.

* Web requests write with the user's own JWT, so Row Level Security applies.
* Bots / webhooks write with the service-role key.
* Platform users who have not linked an account are saved with user_id = null plus their
  platform id (conversations.external_user_id), so the chats can be attached after linking.

Persistence is best-effort: a database outage is logged and never breaks a fact-check.
"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend import config
from backend.db import supabase as db
from backend.pipeline.schemas import CheckResponse

log = logging.getLogger("sachscan.chatlog")

BUCKET = "uploads"
PLATFORM_SESSION_HOURS = 12  # bot chats within this window continue the same conversation


def _title(text: str, file_name: str, content_type: str) -> str:
    t = " ".join((text or "").split())
    if t:
        return t[:60] + ("…" if len(t) > 60 else "")
    return file_name[:60] or {"image": "Screenshot", "audio": "Voice note", "pdf": "PDF", "video": "Video", "url": "Link"}.get(content_type, "Check")


async def resolve_user(platform: str, external_user_id: Optional[str]) -> Optional[str]:
    """Web account linked to this platform id, if any."""
    if not external_user_id or not db.configured():
        return None
    try:
        rows = await db.service().select("platform_links", {"platform": f"eq.{platform}", "external_user_id": f"eq.{external_user_id}", "select": "user_id", "limit": 1})
        return rows[0]["user_id"] if rows else None
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("resolve_user failed: %s", e)
        return None


async def already_processed(platform_message_id: Optional[str]) -> bool:
    """True if this inbound platform message was stored before (webhook retry)."""
    if not platform_message_id or not db.configured():
        return False
    try:
        rows = await db.service().select("messages", {"platform_message_id": f"eq.{platform_message_id}", "role": "eq.user", "select": "id", "limit": 1})
        return bool(rows)
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("dedupe lookup failed: %s", e)
        return False


async def _conversation_for(client: db.Rest, *, platform: str, user_id: Optional[str], external_user_id: Optional[str],
                            conversation_id: Optional[str], title: str) -> str:
    if conversation_id:
        rows = await client.select("conversations", {"id": f"eq.{conversation_id}", "select": "id", "limit": 1})
        if rows:
            return rows[0]["id"]
        conversation_id = None  # unknown or not theirs: start a new one
    if platform != "web" and external_user_id:
        since = (datetime.now(timezone.utc) - timedelta(hours=PLATFORM_SESSION_HOURS)).isoformat()
        rows = await client.select("conversations", {
            "platform": f"eq.{platform}", "external_user_id": f"eq.{external_user_id}",
            "updated_at": f"gte.{since}", "select": "id", "order": "updated_at.desc", "limit": 1,
        })
        if rows:
            return rows[0]["id"]
    row = (await client.insert("conversations", {"user_id": user_id, "platform": platform, "external_user_id": external_user_id, "title": title}))[0]
    return row["id"]


async def _store_media(user_id: Optional[str], platform: str, data: bytes, mime: str, file_name: str) -> Optional[str]:
    ext = (mimetypes.guess_extension(mime or "") or "") if not file_name or "." not in file_name else "." + file_name.rsplit(".", 1)[1][:8]
    path = f"{user_id or 'unlinked'}/{platform}/{uuid.uuid4().hex}{ext}"
    try:
        return await db.service().upload(BUCKET, path, data, mime)
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("media upload failed: %s", e)
        return None


async def save_exchange(
    *,
    platform: str,
    content_type: str,
    text: str = "",
    file_bytes: Optional[bytes] = None,
    mime_type: str = "",
    file_name: str = "",
    user_id: Optional[str] = None,
    external_user_id: Optional[str] = None,
    platform_message_id: Optional[str] = None,
    result: Optional[CheckResponse] = None,
    error: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_token: Optional[str] = None,
) -> Optional[str]:
    """Persist one exchange. Returns the conversation id, or None if persistence is unavailable."""
    if not db.configured():
        return None
    try:
        client = db.as_user(user_token) if user_token else db.service()
        conv = await _conversation_for(client, platform=platform, user_id=user_id, external_user_id=external_user_id,
                                       conversation_id=conversation_id, title=_title(text, file_name, content_type))
        file_path = await _store_media(user_id, platform, file_bytes, mime_type, file_name) if file_bytes else None
        # PostgREST bulk inserts require every row to have the same keys.
        user_msg = {
            "conversation_id": conv, "role": "user", "content_type": content_type,
            "text": text or (file_name or None), "file_path": file_path, "result_json": None,
            "platform_message_id": platform_message_id,
        }
        assistant_msg = {
            "conversation_id": conv, "role": "assistant", "content_type": "text",
            "text": result.overall_summary if result else (error or ""), "file_path": None,
            "result_json": result.model_dump(mode="json") if result else None, "platform_message_id": None,
        }
        await client.insert("messages", [user_msg, assistant_msg], returning=False)
        return conv
    except db.SupabaseError as e:
        if e.status == 409:  # unique platform_message_id: a retry raced us
            log.info("duplicate platform message ignored")
            return None
        log.warning("save_exchange failed: %s", e)
    except db.SupabaseUnavailable as e:
        log.warning("save_exchange skipped: %s", e)
    return None


async def cleanup_old_uploads() -> int:
    """Delete stored media older than UPLOAD_RETENTION_HOURS (runs hourly from the API process)."""
    if not db.configured():
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=config.UPLOAD_RETENTION_HOURS)).isoformat()
    svc = db.service()
    try:
        rows = await svc.select("messages", {"file_path": "not.is.null", "created_at": f"lt.{cutoff}", "select": "id,file_path", "limit": 500})
        if not rows:
            return 0
        await svc.remove(BUCKET, [r["file_path"] for r in rows])
        ids = ",".join(r["id"] for r in rows)
        await svc.update("messages", {"id": f"in.({ids})"}, {"file_path": None})
        log.info("upload cleanup removed %d files", len(rows))
        return len(rows)
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("upload cleanup failed: %s", e)
        return 0
