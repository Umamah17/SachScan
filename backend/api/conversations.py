"""Chat history. Queries run with the user's own JWT, so Row Level Security guarantees
users only ever see their own conversations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import CurrentUser, get_current_user
from backend.db import supabase as db

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _client(user: CurrentUser) -> db.Rest:
    try:
        return db.as_user(user.token)
    except db.SupabaseUnavailable:
        raise HTTPException(503, "Chat history is not configured on the server.")


async def _run(coro):
    try:
        return await coro
    except db.SupabaseUnavailable:
        raise HTTPException(503, "Chat history is temporarily unavailable.")
    except db.SupabaseError as e:
        if e.status in (401, 403):
            raise HTTPException(401, "Please sign in again.")
        raise HTTPException(502, "Could not load chat history.")


@router.get("")
async def list_conversations(limit: int = Query(30, ge=1, le=100), user: CurrentUser = Depends(get_current_user)):
    rows = await _run(_client(user).select("conversations", {
        "select": "id,title,platform,created_at,updated_at", "order": "updated_at.desc", "limit": limit,
    }))
    return {"conversations": rows}


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    client = _client(user)
    conv = await _run(client.select("conversations", {"id": f"eq.{conversation_id}", "select": "id,title,platform,created_at,updated_at"}))
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    rows = await _run(client.select("messages", {
        "conversation_id": f"eq.{conversation_id}",
        "select": "id,role,content_type,text,result_json,created_at", "order": "created_at.asc",
    }))
    return {"conversation": conv[0], "messages": rows}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, user: CurrentUser = Depends(get_current_user)):
    deleted = await _run(_client(user).delete("conversations", {"id": f"eq.{conversation_id}"}))
    if not deleted:
        raise HTTPException(404, "Conversation not found.")
    return {"ok": True}
