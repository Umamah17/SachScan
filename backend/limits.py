"""Per-user / per-platform-id rate limiting (in-process sliding window)."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from backend import config

_hits: dict[str, deque] = defaultdict(deque)


class RateLimited(Exception):
    def __init__(self, retry_after_s: int):
        super().__init__(f"Rate limit reached. Try again in about {max(1, retry_after_s // 60)} minutes.")
        self.retry_after_s = retry_after_s


def hit(key: str, limit: int | None = None, window_s: int = 3600) -> None:
    """Record one check for `key` (e.g. "user:<uuid>", "whatsapp:<number>"); raise RateLimited when over."""
    limit = config.RATE_LIMIT_PER_HOUR if limit is None else limit
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise RateLimited(int(window_s - (now - q[0])))
    q.append(now)


def undo(key: str) -> None:
    """Give a check back (e.g. the input was rejected before any AI call)."""
    q = _hits.get(key)
    if q:
        q.pop()
