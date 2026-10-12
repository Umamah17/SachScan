"""Test setup: no real AI keys, no real Supabase (unless the RLS test opts in), temp data dir."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep the real Supabase values (env or backend/.env) for the opt-in RLS test before blanking them.
from dotenv import dotenv_values  # noqa: E402

_dotenv = dotenv_values(ROOT / "backend" / ".env")
for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault(f"RLS_TEST_{k}", os.environ.get(k) or _dotenv.get(k) or "")

# load_dotenv() never overrides variables that already exist, so blanking them here wins over backend/.env
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY",
          "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "WHATSAPP_APP_SECRET", "WHATSAPP_TOKEN",
          "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "DISCORD_BOT_TOKEN", "GOOGLE_PUBSUB_TOPIC"):
    os.environ[k] = ""
os.environ["SACHSCAN_DATA_DIR"] = tempfile.mkdtemp()
os.environ["SEARCH_PROVIDER"] = "gemini"
