"""Central configuration, loaded once from backend/.env (never from the frontend)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("SACHSCAN_DATA_DIR", "") or BACKEND_DIR / "data")
STATIC_DIR = BACKEND_DIR / "static"

load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# --- AI providers -----------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "").strip()

# gemini | perplexity | claude | web  (others are used automatically as fallbacks;
# "web" = free DuckDuckGo search + page reading, always the last resort)
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "gemini").strip().lower()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
# Tried in order when the main model hits a (free-tier) rate limit; each has its own quota.
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-flash-lite-latest,gemini-flash-latest"
).split(",") if m.strip()]
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5").strip()
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low").strip()  # low keeps the demo fast
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar").strip()
# Server-side refusal fallback for Claude (beta). Retried without it if the API rejects it.
CLAUDE_REFUSAL_FALLBACK = _bool("CLAUDE_REFUSAL_FALLBACK", True)

# --- Pipeline limits --------------------------------------------------------
MAX_MEDIA_MB = _int("MAX_MEDIA_MB", 16)   # images, voice notes, videos
MAX_PDF_MB = _int("MAX_PDF_MB", 10)
MAX_UPLOAD_MB = max(MAX_MEDIA_MB, MAX_PDF_MB)  # hard cap for any download / upload
MAX_TEXT_CHARS = _int("MAX_TEXT_CHARS", 12000)
MAX_CLAIMS = _int("MAX_CLAIMS", 4)
STEP_TIMEOUT_S = _int("STEP_TIMEOUT_S", 45)
CHECKS_CACHE_TTL_HOURS = _int("CHECKS_CACHE_TTL_HOURS", 24)  # 0 disables the result cache
RATE_LIMIT_PER_HOUR = _int("RATE_LIMIT_PER_HOUR", 20)          # checks per user / platform id
UPLOAD_RETENTION_HOURS = _int("UPLOAD_RETENTION_HOURS", 24)

# --- Supabase (database, auth, storage) -----------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()                    # public, used by the browser
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()    # SECRET, server only
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()  # only for projects on legacy HS256 JWT secrets

# --- Public URLs ----------------------------------------------------------------
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")  # https URL of this backend (ngrok / deploy)
PUBLIC_WEB_URL = (os.getenv("PUBLIC_WEB_URL", "").strip().rstrip("/") or PUBLIC_BASE_URL)  # where the web app is served

# --- Legacy local accounts (removed in the Supabase auth step) -------------------
FREE_MONTHLY_SCANS = _int("FREE_MONTHLY_SCANS", 500)
SESSION_DAYS = _int("SESSION_DAYS", 30)
ANON_CHECKS_PER_HOUR = _int("ANON_CHECKS_PER_HOUR", 60)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
ALLOW_DEMO_SSO = _bool("ALLOW_DEMO_SSO", True)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER).strip()
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "").strip()
DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID", "").strip()
WHATSAPP_NUMBER = "".join(c for c in os.getenv("WHATSAPP_NUMBER", "") if c.isdigit())
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
X_HANDLE = os.getenv("X_HANDLE", "").strip().lstrip("@")
REDDIT_COMMUNITY = os.getenv("REDDIT_COMMUNITY", "").strip().removeprefix("r/")
INSTAGRAM_HANDLE = os.getenv("INSTAGRAM_HANDLE", "").strip().lstrip("@")
THREADS_HANDLE = os.getenv("THREADS_HANDLE", "").strip().lstrip("@")

# --- Telegram -------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")

# --- HTTP -------------------------------------------------------------------
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
PORT = _int("PORT", 3000)

DATA_DIR.mkdir(parents=True, exist_ok=True)
