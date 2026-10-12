"""SachScan backend (FastAPI).

Run from the repo root:
    backend\\.venv\\Scripts\\python -m uvicorn backend.main:app --host 0.0.0.0 --port 3001 --reload
Open http://localhost:3001 — this server also serves the frontend (same routes/headers as server.js).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from backend import config
from backend.api.check import router as check_router
from backend.api.conversations import router as conversations_router
from backend.api.integrations import router as integrations_router
from backend.api.link import router as link_router
from backend.api.pages import router as pages_router
from backend.api.share import router as share_router
from backend.channels.whatsapp import router as whatsapp_router
from backend.db import chatlog
from backend.db import supabase as db
from backend.pipeline import llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can contain tokens; keep them out of INFO logs
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger("sachscan")


async def _hourly_cleanup():
    while True:
        await chatlog.cleanup_old_uploads()
        await asyncio.sleep(3600)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    if not db.configured():
        log.warning("Supabase is not configured: sign-in, chat history and the shared cache are disabled. See SETUP.md.")
    task = asyncio.create_task(_hourly_cleanup())
    yield
    task.cancel()


app = FastAPI(title="SachScan API", version="2.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
for r in (check_router, conversations_router, link_router, share_router, integrations_router, pages_router, whatsapp_router):
    app.include_router(r)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "input"
    return JSONResponse({"detail": f"Invalid {field}: {first.get('msg', 'bad value')}"}, status_code=422)


# ===========================================================================
# Public meta endpoints
# ===========================================================================

@app.get("/health", tags=["meta"])
@app.get("/api/health", tags=["meta"], include_in_schema=False)
async def health():
    return {
        "status": "ok",
        "providers": {"gemini": llm.gemini_available(), "openrouter": llm.openrouter_available(), "claude": llm.claude_available(), "perplexity": bool(config.PERPLEXITY_API_KEY)},
        "search_provider": config.SEARCH_PROVIDER,
        "database": db.configured(),
    }


@app.get("/api/config", tags=["meta"])
async def public_config():
    """Public values the browser needs (the Supabase anon key is designed to be public)."""
    return {
        "supabaseUrl": config.SUPABASE_URL,
        "supabaseAnonKey": config.SUPABASE_ANON_KEY,
        "maxMediaMB": config.MAX_MEDIA_MB,
        "maxPdfMB": config.MAX_PDF_MB,
        "aiConfigured": llm.gemini_available() or llm.claude_available() or llm.openrouter_available(),
    }


# ===========================================================================
# Static frontend (mirrors server.js)
# ===========================================================================

PRETTY_ROUTES = {
    "": "index.html", "integrations": "integrations.html", "login": "login.html",
    "technology": "technology.html", "help": "help.html", "offline": "offline.html",
}
BLOCKED_TOP = {"backend", "bots", "supabase", "node_modules", "scratch_fonts", "tests"}
LONG_CACHE = {".jpg", ".jpeg", ".png", ".webp", ".woff2"}


def _resolve_static(path: str) -> Optional[Path]:
    path = PRETTY_ROUTES.get(path.strip("/"), path.strip("/"))
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
    media_type = {".js": "text/javascript; charset=utf-8", ".html": "text/html; charset=utf-8"}.get(ext)
    if target.name == "manifest.json":
        media_type = "application/manifest+json"
    return FileResponse(target, media_type=media_type, headers=headers)
