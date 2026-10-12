"""Web fact-check endpoints. All require a signed-in Supabase user and persist the exchange.

POST /check               multipart (text / url / file) or JSON -> CheckResult JSON   (also used by the extension)
POST /api/check/stream    same input -> NDJSON events: progress, claims, result, conversation, error
POST /chat                same input + conversation_id -> {"conversation_id", "result"}
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile

from backend import config, limits
from backend.api.deps import CurrentUser, get_current_user
from backend.db import chatlog
from backend.pipeline.core import CheckError, NormalizedInput, check_content, content_type_for
from backend.pipeline.extract import ExtractionError, looks_like_only_url

log = logging.getLogger("sachscan.api.check")
router = APIRouter(tags=["check"])
_tasks: set[asyncio.Task] = set()  # keeps background checks alive until they finish


@dataclass
class WebInput:
    inp: NormalizedInput
    conversation_id: Optional[str] = None


async def read_upload(f: UploadFile) -> bytes:
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    data = await f.read(limit + 1)
    await f.close()
    if len(data) > limit:
        raise HTTPException(413, f"That file is too large. Images, voice notes and videos can be up to {config.MAX_MEDIA_MB} MB, PDFs up to {config.MAX_PDF_MB} MB.")
    return data


def make_input(*, platform: str, text: str = "", url: str = "", file_bytes: Optional[bytes] = None, mime: str = "",
               file_name: str = "", user_id: Optional[str] = None, external_user_id: Optional[str] = None,
               message_id: Optional[str] = None) -> NormalizedInput:
    """Build a NormalizedInput from loose web/share fields."""
    if file_bytes:
        try:
            ctype = content_type_for(mime, file_name)
        except ExtractionError as e:
            raise CheckError(str(e), status=415)
        return NormalizedInput(platform=platform, content_type=ctype, text=text, file_bytes=file_bytes, mime_type=mime,
                               file_name=file_name, user_id=user_id, external_user_id=external_user_id, original_message_id=message_id)
    if url.strip():
        return NormalizedInput(platform=platform, content_type="url", text=url.strip(), user_id=user_id, external_user_id=external_user_id, original_message_id=message_id)
    ctype = "url" if looks_like_only_url(text) else "text"
    return NormalizedInput(platform=platform, content_type=ctype, text=text, user_id=user_id, external_user_id=external_user_id, original_message_id=message_id)


async def parse_web_input(request: Request, user: CurrentUser, platform: str = "web") -> WebInput:
    ctype = request.headers.get("content-type", "")
    file_bytes, mime, file_name = None, "", ""
    if ctype.startswith("application/json"):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON body.")
        text, url, conv = str(body.get("text") or ""), str(body.get("url") or ""), body.get("conversation_id")
        platform = body.get("platform") if body.get("platform") in ("web", "extension") else platform
    else:
        form = await request.form()
        text, url, conv = str(form.get("text") or ""), str(form.get("url") or ""), form.get("conversation_id")
        upload = form.get("file") or form.get("media")
        if isinstance(upload, UploadFile) and upload.filename is not None:
            file_bytes = await read_upload(upload) or None
            mime, file_name = upload.content_type or "", upload.filename or ""
    if not (text.strip() or url.strip() or file_bytes):
        raise HTTPException(400, "Please paste a message, a link, or attach a file to check.")
    try:
        inp = make_input(platform=platform, text=text, url=url, file_bytes=file_bytes, mime=mime, file_name=file_name, user_id=user.id)
    except CheckError as e:
        raise HTTPException(e.status, str(e))
    return WebInput(inp=inp, conversation_id=str(conv) if conv else None)


def _rate_gate(user: CurrentUser) -> str:
    key = f"user:{user.id}"
    try:
        limits.hit(key)
    except limits.RateLimited as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after_s)})
    return key


async def run_and_save(w: WebInput, user: CurrentUser, progress=None):
    """check_content + persistence. Returns (result | None, error message | None, status, conversation id)."""
    result, error, status = None, None, 200
    try:
        result = await check_content(w.inp, progress)
    except CheckError as e:
        error, status = str(e), e.status
    except Exception:
        log.exception("check failed")
        error, status = "Something went wrong while checking. Please try again.", 500
    conv = await chatlog.save_exchange(
        platform=w.inp.platform, content_type=w.inp.content_type, text=w.inp.text, file_bytes=w.inp.file_bytes,
        mime_type=w.inp.mime_type, file_name=w.inp.file_name, user_id=user.id, result=result, error=error,
        conversation_id=w.conversation_id, user_token=user.token,
    )
    return result, error, status, conv


@router.post("/check", summary="Fact-check text / URL / file; returns the CheckResult schema")
@router.post("/api/check", include_in_schema=False)
async def check(request: Request, user: CurrentUser = Depends(get_current_user)):
    w = await parse_web_input(request, user)
    _rate_gate(user)
    result, error, status, conv = await run_and_save(w, user)
    if result is None:
        return JSONResponse({"detail": error}, status_code=status)
    data = result.model_dump(mode="json")
    data["meta"]["conversation_id"] = conv
    return JSONResponse(data)


@router.post("/chat", summary="Chat turn: check + save into a conversation")
async def chat(request: Request, user: CurrentUser = Depends(get_current_user)):
    w = await parse_web_input(request, user)
    _rate_gate(user)
    result, error, status, conv = await run_and_save(w, user)
    if result is None:
        return JSONResponse({"detail": error, "conversation_id": conv}, status_code=status)
    return {"conversation_id": conv, "result": result.model_dump(mode="json")}


def stream_check(w: WebInput, user: CurrentUser) -> StreamingResponse:
    """NDJSON stream of pipeline progress, then the result, then the saved conversation id."""
    queue: asyncio.Queue = asyncio.Queue()

    async def progress(ev: dict) -> None:
        await queue.put(ev)

    async def worker():
        result, error, status, conv = await run_and_save(w, user, progress)
        if result is not None:
            await queue.put({"type": "result", "data": result.model_dump(mode="json")})
        else:
            await queue.put({"type": "error", "status": status, "message": error})
        if conv:
            await queue.put({"type": "conversation", "id": conv})
        await queue.put(None)

    async def gen():
        # The worker is not cancelled if the browser disconnects, so the exchange is still saved
        # and shows up in History.
        _tasks.add(task := asyncio.create_task(worker()))
        task.add_done_callback(_tasks.discard)
        while (ev := await queue.get()) is not None:
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/api/check/stream", summary="Same as /check but streams NDJSON progress events")
async def check_stream(request: Request, user: CurrentUser = Depends(get_current_user)):
    w = await parse_web_input(request, user)
    _rate_gate(user)
    return stream_check(w, user)
