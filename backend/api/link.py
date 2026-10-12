"""POST /link/start: one-time code the user sends to a bot as "link <code>"."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import CurrentUser, get_current_user
from backend.channels.common import LINK_CODE_TTL_MIN, create_link_code
from backend.db import supabase as db

router = APIRouter(tags=["account"])


@router.post("/link/start")
async def link_start(user: CurrentUser = Depends(get_current_user)):
    try:
        code, expires = await create_link_code(user.id)
    except (db.SupabaseUnavailable, db.SupabaseError):
        raise HTTPException(503, "Account linking is temporarily unavailable.")
    return {
        "code": code,
        "expires_at": expires.isoformat(),
        "instructions": f"Send  link {code}  to the SachScan WhatsApp, Telegram or Discord bot within {LINK_CODE_TTL_MIN} minutes.",
    }


@router.get("/me", tags=["account"])
async def me(user: CurrentUser = Depends(get_current_user)):
    links = []
    if db.configured():
        try:
            links = await db.as_user(user.token).select("platform_links", {"select": "platform,linked_at"})
        except (db.SupabaseUnavailable, db.SupabaseError):
            pass
    return {"id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url, "links": links}
