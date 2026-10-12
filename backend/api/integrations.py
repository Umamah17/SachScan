"""GET /api/integrations: status of each messaging channel (for health dashboards)."""
from __future__ import annotations

from fastapi import APIRouter

from backend import config

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
async def list_integrations():
    return {
        "integrations": [
            {"id": "web", "status": "live"},
            {"id": "whatsapp", "status": "live" if getattr(config, "WHATSAPP_TOKEN", "") else "not_configured"},
            {"id": "telegram", "status": "live" if config.TELEGRAM_BOT_TOKEN else "not_configured"},
            {"id": "discord", "status": "live" if getattr(config, "DISCORD_BOT_TOKEN", "") else "not_configured"},
            {"id": "gmail", "status": "live" if getattr(config, "GOOGLE_PUBSUB_TOPIC", "") else "not_configured"},
            {"id": "x", "status": "coming_soon"},
            {"id": "reddit", "status": "coming_soon"},
            {"id": "instagram", "status": "coming_soon"},
            {"id": "threads", "status": "coming_soon"},
        ]
    }
