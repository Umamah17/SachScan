"""SachScan / ForwardCheck backend.

Run:  cd backend && uvicorn main:app --host 0.0.0.0 --port 3000
Then open http://localhost:3000 — this server also serves the existing frontend
(same routes and cache headers as server.js) without modifying any frontend file.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

import auth
import config
import llm
from extract import ExtractionError
from integrations import router as integrations_router
from legal import router as legal_router
from pipeline import run_check
from schemas import CheckResponse, DemoSSOIn, ForgotIn, LoginIn, RegisterIn, ResetIn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sachscan")

app = FastAPI(title="SachScan API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(integrations_router)
app.include_router(legal_router)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "input"
    return JSONResponse({"detail": f"Invalid {field}: {first.get('msg', 'bad value')}"}, status_code=422)


# ===========================================================================
# Fact-check API
# ===========================================================================

class CheckInput:
    def __init__(self, text: str = "", url: str = "", file_bytes: Optional[bytes] = None, file_mime: str = "", file_name: str = ""):
        self.text, self.url, self.file_bytes, self.file_mime, self.file_name = text, url, file_bytes, file_mime, file_name

    def kwargs(self) -> dict:
        return dict(text=self.text, url=self.url, file_bytes=self.file_bytes, file_mime=self.file_mime, file_name=self.file_name)


async def _read_upload(f: StarletteUploadFile) -> bytes:
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    data = await f.read(limit + 1)
    await f.close()
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    return data


async def parse_check_input(request: Request) -> CheckInput:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON body.")
        return CheckInput(text=str(body.get("text") or ""), url=str(body.get("url") or ""))
    form = await request.form()
    inp = CheckInput(text=str(form.get("text") or ""), url=str(form.get("url") or ""))
    upload = form.get("file") or form.get("media")
    if isinstance(upload, StarletteUploadFile) and upload.filename is not None:
        inp.file_bytes = await _read_upload(upload)
        inp.file_mime, inp.file_name = upload.content_type or "", upload.filename or ""
        if not inp.file_bytes:
            inp.file_bytes = None
    return inp


def _quota_gate(request: Request, user) -> Optional[int]:
    """Consume a scan for signed-in users; rate-limit anonymous users by IP."""
    if user is not None:
        return auth.consume_scan(user["id"])
    auth.rate_limit(f"anon-check:{auth.client_ip(request)}", config.ANON_CHECKS_PER_HOUR, 3600)
    return None


async def _events(inp: CheckInput, user, scans_left: Optional[int]):
    """Pipeline events with quota bookkeeping and errors turned into events.
    The quota gate runs *before* this (so a 402/429 is a normal HTTP error, not mid-stream)."""
    ok = False
    try:
        async for ev in run_check(**inp.kwargs()):
            if ev["type"] == "result":
                ok = True
                if scans_left is not None:
                    ev["data"].meta["scans_left"] = scans_left
            yield ev
    except ExtractionError as e:
        yield {"type": "error", "status": 422, "message": str(e)}
    except Exception:
        log.exception("check failed")
        yield {"type": "error", "status": 500, "message": "Something went wrong while checking. Please try again."}
    finally:
        if not ok and user is not None:
            auth.refund_scan(user["id"])


def _serialise(ev: dict) -> str:
    if ev["type"] == "result":
        ev = {"type": "result", "data": ev["data"].model_dump()}
    return json.dumps(ev, ensure_ascii=False) + "\n"


async def _check_json(inp: CheckInput, request: Request, user) -> JSONResponse:
    scans_left = _quota_gate(request, user)
    final: Optional[dict] = None
    async for ev in _events(inp, user, scans_left):  # drain fully so quota bookkeeping runs
        if ev["type"] in ("result", "error"):
            final = ev
    if final and final["type"] == "result":
        return JSONResponse(final["data"].model_dump())
    if final:
        return JSONResponse({"detail": final["message"]}, status_code=final.get("status", 500))
    return JSONResponse({"detail": "No result produced."}, status_code=500)


def _stream(inp: CheckInput, request: Request, user) -> StreamingResponse:
    scans_left = _quota_gate(request, user)

    async def gen():
        async for ev in _events(inp, user, scans_left):
            yield _serialise(ev)

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/check", response_model=CheckResponse, tags=["check"], summary="Fact-check text / URL / file (multipart or JSON)")
@app.post("/api/check", response_model=CheckResponse, tags=["check"], include_in_schema=False)
async def check(request: Request, user=Depends(auth.optional_user)):
    """multipart/form-data fields: `text`, `url`, `file` (image / audio / PDF / video).
    Or JSON: {"text": "..."} / {"url": "..."}. Returns the agreed CheckResponse schema."""
    return await _check_json(await parse_check_input(request), request, user)


@app.post("/api/check/stream", tags=["check"], summary="Same as /check but streams NDJSON progress events")
async def check_stream(request: Request, user=Depends(auth.optional_user)):
    inp = await parse_check_input(request)
    if not (inp.text.strip() or inp.url.strip() or inp.file_bytes):
        raise HTTPException(400, "Please paste a message, a link, or attach a file to check.")
    return _stream(inp, request, user)


# ---------------------------------------------------------------------------
# Web Share Target (WhatsApp -> Share -> SachScan PWA)
# ---------------------------------------------------------------------------

_shared: dict[str, tuple[float, CheckInput]] = {}
SHARE_TTL_S = 600


def _prune_shared():
    now = time.time()
    for k in [k for k, (t, _) in _shared.items() if now - t > SHARE_TTL_S]:
        _shared.pop(k, None)


@app.post("/share-target", include_in_schema=False)
async def share_target(request: Request):
    """Receives the OS share sheet POST, keeps it in memory briefly, and opens the scanner."""
    _prune_shared()
    form = await request.form()
    parts = [str(form.get(k) or "").strip() for k in ("title", "text")]
    url = str(form.get("url") or "").strip()
    text = "\n".join(p for p in parts if p)
    inp = CheckInput(text=text)
    if url and url not in text:
        inp.text = (text + "\n" + url).strip() if text else ""
        inp.url = "" if text else url
    media = form.getlist("media") if hasattr(form, "getlist") else []
    for upload in media:
        if isinstance(upload, StarletteUploadFile) and upload.filename is not None:
            data = await _read_upload(upload)
            if data:
                inp.file_bytes, inp.file_mime, inp.file_name = data, upload.content_type or "", upload.filename or ""
                break  # one item per check keeps results readable
    share_id = secrets.token_urlsafe(16)
    _shared[share_id] = (time.time(), inp)
    return RedirectResponse(f"/help.html?shared={share_id}", status_code=303)


@app.get("/api/shared/{share_id}", tags=["check"])
async def shared_preview(share_id: str):
    item = _shared.get(share_id)
    if not item:
        raise HTTPException(404, "This shared item has expired. Please share it again.")
    inp = item[1]
    return {"text": inp.text, "url": inp.url, "file_name": inp.file_name, "file_mime": inp.file_mime, "has_file": bool(inp.file_bytes)}


@app.post("/api/shared/{share_id}/check/stream", tags=["check"])
async def shared_check(share_id: str, request: Request, user=Depends(auth.optional_user)):
    item = _shared.pop(share_id, None)  # one-shot: deleted once processed
    if not item:
        raise HTTPException(404, "This shared item has expired. Please share it again.")
    return _stream(item[1], request, user)


# ===========================================================================
# Auth API
# ===========================================================================

def _auth_payload(user: dict, token: str, api_key: Optional[str] = None) -> dict:
    out = {"token": token, "user": user}
    if api_key:
        out["apiKey"] = api_key
    return out


@app.post("/api/auth/register", tags=["auth"])
async def register(body: RegisterIn, request: Request):
    auth.rate_limit(f"register:{auth.client_ip(request)}", 10, 3600)
    user, api_key = auth.create_user(body.name, body.email, body.password)
    return _auth_payload(user, auth.create_session(user["id"]), api_key)


@app.post("/api/auth/login", tags=["auth"])
async def login(body: LoginIn, request: Request):
    auth.rate_limit(f"login:{auth.client_ip(request)}", 20, 600)
    user = auth.verify_login(body.email, body.password)
    return _auth_payload(user, auth.create_session(user["id"], body.remember))


@app.post("/api/auth/logout", tags=["auth"])
async def logout(request: Request):
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        auth.delete_session(header[7:].strip())
    return {"ok": True}


@app.get("/api/auth/me", tags=["auth"])
async def me(user=Depends(auth.required_user)):
    return {"user": auth.public_user(user)}


@app.post("/api/auth/api-key", tags=["auth"], summary="Rotate and reveal a new developer API key")
async def rotate_key(user=Depends(auth.required_user)):
    pub, key = auth.rotate_api_key(user["id"])
    return {"user": pub, "apiKey": key}


def _base_url(request: Request) -> str:
    return config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


@app.post("/api/auth/forgot", tags=["auth"])
async def forgot(body: ForgotIn, request: Request):
    auth.rate_limit(f"forgot:{auth.client_ip(request)}", 5, 3600)
    auth.start_password_reset(body.email, _base_url(request))
    return {"ok": True}


@app.post("/api/auth/reset", tags=["auth"])
async def reset(body: ResetIn):
    user = auth.finish_password_reset(body.token, body.password)
    return _auth_payload(user, auth.create_session(user["id"]))


DEMO_SSO = {
    "google": ("Google Explorer", "google.user@sachscan.io"),
    "github": ("Developer SSO", "dev@github.sachscan.io"),
}


@app.post("/api/auth/demo-sso", tags=["auth"], summary="Demo SSO accounts (only when real OAuth is not configured)")
async def demo_sso(body: DemoSSOIn):
    if not config.ALLOW_DEMO_SSO or _oauth_configured(body.provider):
        raise HTTPException(403, "Demo sign-in is disabled.")
    name, email = DEMO_SSO[body.provider]
    user = auth.get_or_create_sso_user(name, email, f"demo-{body.provider}")
    return _auth_payload(user, auth.create_session(user["id"]))


# ---------------------------------------------------------------------------
# Real OAuth (Google / GitHub) when client credentials are configured
# ---------------------------------------------------------------------------

OAUTH = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
}
_oauth_states: dict[str, float] = {}


def _oauth_configured(provider: str) -> bool:
    return bool(
        (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET) if provider == "google"
        else (config.GITHUB_CLIENT_ID and config.GITHUB_CLIENT_SECRET) if provider == "github" else False
    )


def _oauth_creds(provider: str) -> tuple[str, str]:
    return (config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET) if provider == "google" else (config.GITHUB_CLIENT_ID, config.GITHUB_CLIENT_SECRET)


@app.get("/api/auth/oauth/{provider}/start", tags=["auth"])
async def oauth_start(provider: str, request: Request):
    if provider not in OAUTH or not _oauth_configured(provider):
        raise HTTPException(404, f"{provider} sign-in is not configured.")
    now = time.time()
    for k in [k for k, t in _oauth_states.items() if now - t > 600]:
        _oauth_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = now
    client_id, _ = _oauth_creds(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": f"{_base_url(request)}/api/auth/oauth/{provider}/callback",
        "scope": OAUTH[provider]["scope"],
        "state": state,
        "response_type": "code",
    }
    if provider == "google":
        params["prompt"] = "select_account"
    return RedirectResponse(f"{OAUTH[provider]['authorize']}?{urlencode(params)}")


@app.get("/api/auth/oauth/{provider}/callback", tags=["auth"], include_in_schema=False)
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or _oauth_states.pop(state, None) is None or provider not in OAUTH:
        return RedirectResponse("/login.html#sso_error=1")
    client_id, secret = _oauth_creds(provider)
    redirect_uri = f"{_base_url(request)}/api/auth/oauth/{provider}/callback"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/json"}) as client:
            tok = (await client.post(OAUTH[provider]["token"], data={
                "client_id": client_id, "client_secret": secret, "code": code,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            })).json()
            access = tok.get("access_token")
            if not access:
                raise ValueError("no access token")
            hdr = {"Authorization": f"Bearer {access}"}
            if provider == "google":
                info = (await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=hdr)).json()
                if not info.get("email_verified", False):
                    raise ValueError("unverified email")
                email, name = info.get("email"), info.get("name") or ""
            else:
                info = (await client.get("https://api.github.com/user", headers=hdr)).json()
                emails = (await client.get("https://api.github.com/user/emails", headers=hdr)).json()
                primary = next((e for e in emails if e.get("primary") and e.get("verified")), None) if isinstance(emails, list) else None
                if not primary:
                    raise ValueError("no verified email")
                email, name = primary["email"], info.get("name") or info.get("login") or ""
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("OAuth %s failed: %s", provider, e)
        return RedirectResponse("/login.html#sso_error=1")
    user = auth.get_or_create_sso_user(name, email, provider)
    token = auth.create_session(user["id"])
    # Token travels in the URL fragment, which browsers never send to servers.
    return RedirectResponse(f"/login.html#sso={token}")


# ===========================================================================
# Meta
# ===========================================================================

@app.get("/api/health", tags=["meta"])
async def health():
    return {
        "status": "ok",
        "providers": {
            "gemini": llm.gemini_available(),
            "claude": llm.claude_available(),
            "perplexity": bool(config.PERPLEXITY_API_KEY),
        },
        "search_provider": config.SEARCH_PROVIDER,
    }


@app.get("/api/config", tags=["meta"])
async def public_config():
    return {
        "oauth": {"google": _oauth_configured("google"), "github": _oauth_configured("github")},
        "demoSSO": config.ALLOW_DEMO_SSO,
        "freeScans": config.FREE_MONTHLY_SCANS,
        "maxUploadMB": config.MAX_UPLOAD_MB,
        "aiConfigured": llm.gemini_available() or llm.claude_available(),
    }


# ===========================================================================
# Static frontend (mirrors server.js; injects the API bridge into HTML pages)
# ===========================================================================

PRETTY_ROUTES = {
    "": "index.html", "integrations": "integrations.html", "login": "login.html",
    "technology": "technology.html", "help": "help.html", "offline": "offline.html",
}
BLOCKED_TOP = {"backend", "node_modules", "scratch_fonts"}
BRIDGE_TAG = '<script src="/sachscan-bridge.js?v=1.0.0"></script>'
LONG_CACHE = {".jpg", ".jpeg", ".png", ".webp", ".woff2"}

SHARE_TARGET = {
    "action": "/share-target",
    "method": "POST",
    "enctype": "multipart/form-data",
    "params": {
        "title": "title",
        "text": "text",
        "url": "url",
        "files": [{"name": "media", "accept": ["image/*", "audio/*", "video/*", "application/pdf", ".pdf", ".opus", ".ogg", ".m4a", ".mp3", ".jpg", ".jpeg", ".png", ".webp", ".mp4"]}],
    },
}


@app.get("/sachscan-bridge.js", include_in_schema=False)
async def bridge_js():
    return FileResponse(config.STATIC_DIR / "sachscan-bridge.js", media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    data = json.loads((config.FRONTEND_DIR / "manifest.json").read_text(encoding="utf-8"))
    data["share_target"] = SHARE_TARGET
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


def _resolve_static(path: str) -> Optional[Path]:
    path = path.strip("/")
    path = PRETTY_ROUTES.get(path, path)
    parts = Path(path).parts
    if not parts or parts[0] in BLOCKED_TOP or any(p.startswith(".") or p == ".." for p in parts):
        return None
    root = config.FRONTEND_DIR.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


@app.get("/{path:path}", include_in_schema=False)
@app.head("/{path:path}", include_in_schema=False)
async def static_files(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Unknown API endpoint.")
    target = _resolve_static(path)
    if target is None:
        return Response("404 Not Found", status_code=404, media_type="text/plain")
    ext = target.suffix.lower()
    if target.name == "sw.js":
        headers = {"Service-Worker-Allowed": "/", "Cache-Control": "no-cache, no-store, must-revalidate"}
    elif ext in LONG_CACHE:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    else:
        headers = {"Cache-Control": "no-cache"}
    if ext == ".html":
        html = target.read_text(encoding="utf-8")
        if BRIDGE_TAG not in html:
            html = html.replace("</head>", f"  {BRIDGE_TAG}\n</head>", 1) if "</head>" in html else BRIDGE_TAG + html
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)
    media_type = "text/javascript; charset=utf-8" if ext == ".js" else None
    return FileResponse(target, media_type=media_type, headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
