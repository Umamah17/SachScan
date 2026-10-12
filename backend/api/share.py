"""Web Share Target (WhatsApp -> Share -> SachScan PWA).

The OS share sheet POSTs without our auth header, so the item is parked in memory for 10 minutes
under a random id and the browser is redirected to the (signed-in) checker, which runs it."""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile

from backend.api.check import WebInput, _rate_gate, make_input, read_upload, stream_check
from backend.api.deps import CurrentUser, get_current_user
from backend.pipeline.core import CheckError

router = APIRouter(tags=["share"])

SHARE_TTL_S = 600
_shared: dict[str, tuple[float, dict]] = {}


def _prune():
    now = time.time()
    for k in [k for k, (t, _) in _shared.items() if now - t > SHARE_TTL_S]:
        _shared.pop(k, None)


@router.post("/share-target", include_in_schema=False)
async def share_target(request: Request):
    _prune()
    form = await request.form()
    text = "\n".join(p for p in (str(form.get(k) or "").strip() for k in ("title", "text")) if p)
    url = str(form.get("url") or "").strip()
    if url and url not in text:
        text = f"{text}\n{url}".strip()
    item = {"text": text, "file_bytes": None, "mime": "", "file_name": ""}
    for upload in form.getlist("media"):
        if isinstance(upload, UploadFile) and upload.filename is not None:
            data = await read_upload(upload)
            if data:
                item.update(file_bytes=data, mime=upload.content_type or "", file_name=upload.filename or "")
                break  # one item per check keeps results readable
    share_id = secrets.token_urlsafe(16)
    _shared[share_id] = (time.time(), item)
    return RedirectResponse(f"/help.html?shared={share_id}", status_code=303)


@router.get("/api/shared/{share_id}")
async def shared_preview(share_id: str, user: CurrentUser = Depends(get_current_user)):
    item = _shared.get(share_id)
    if not item:
        raise HTTPException(404, "This shared item has expired. Please share it again.")
    d = item[1]
    return {"text": d["text"], "file_name": d["file_name"], "file_mime": d["mime"], "has_file": bool(d["file_bytes"])}


@router.post("/api/shared/{share_id}/check/stream")
async def shared_check(share_id: str, user: CurrentUser = Depends(get_current_user)):
    if share_id not in _shared:
        raise HTTPException(404, "This shared item has expired. Please share it again.")
    _rate_gate(user)
    d = _shared.pop(share_id)[1]  # one-shot
    try:
        inp = make_input(platform="web", text=d["text"], file_bytes=d["file_bytes"], mime=d["mime"], file_name=d["file_name"], user_id=user.id)
    except CheckError as e:
        raise HTTPException(e.status, str(e))
    return stream_check(WebInput(inp=inp), user)
