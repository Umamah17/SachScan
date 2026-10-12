"""checks_cache: instant results for repeated forwards.

Keyed by a SHA-256 content hash. Stored in Supabase when configured (shared by the API and the
bot processes), otherwise in a small in-process dict. Entries older than CHECKS_CACHE_TTL_HOURS
are ignored, because news moves on."""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend import config
from backend.db import supabase as db
from backend.pipeline.schemas import CheckResponse

log = logging.getLogger("sachscan.cache")

_local: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


def text_key(content_type: str, text: str) -> str:
    return hashlib.sha256(f"{content_type}\x00{' '.join(text.split()).lower()}".encode()).hexdigest()


def bytes_key(data: bytes) -> str:
    return "file:" + hashlib.sha256(data).hexdigest()


def _ttl_s() -> int:
    return max(0, config.CHECKS_CACHE_TTL_HOURS) * 3600


async def get(key: str) -> Optional[CheckResponse]:
    if _ttl_s() == 0:
        return None
    if db.configured():
        since = (datetime.now(timezone.utc) - timedelta(seconds=_ttl_s())).isoformat()
        try:
            rows = await db.service().select("checks_cache", {"content_hash": f"eq.{key}", "created_at": f"gte.{since}", "select": "result_json", "limit": 1})
            return CheckResponse.model_validate(rows[0]["result_json"]) if rows else None
        except (db.SupabaseUnavailable, db.SupabaseError) as e:
            log.warning("cache read failed: %s", e)
            return None
    item = _local.get(key)
    if not item or time.time() - item[0] > _ttl_s():
        _local.pop(key, None)
        return None
    return CheckResponse.model_validate(item[1])


async def put(keys: list[str], result: CheckResponse) -> None:
    if _ttl_s() == 0:
        return
    payload = result.model_dump()
    payload["meta"] = {k: v for k, v in payload.get("meta", {}).items() if k in ("providers", "timings", "ai_configured")}
    if db.configured():
        rows = [{"content_hash": k, "result_json": payload, "created_at": datetime.now(timezone.utc).isoformat()} for k in dict.fromkeys(keys)]
        try:
            await db.service().insert("checks_cache", rows, upsert=True, on_conflict="content_hash", returning=False)
        except (db.SupabaseUnavailable, db.SupabaseError) as e:
            log.warning("cache write failed: %s", e)
        return
    for k in keys:
        _local[k] = (time.time(), payload)
        _local.move_to_end(k)
    while len(_local) > 300:
        _local.popitem(last=False)
