"""Minimal async Supabase client (PostgREST + Storage) over httpx.

Two ways to talk to the database:
  * service()      -> service-role key, bypasses RLS. Server-side only (bots, webhooks, cache).
  * as_user(token) -> the signed-in user's JWT, so Row Level Security decides what they can see.

When Supabase is not configured every call raises SupabaseUnavailable; callers treat persistence
as best-effort so fact-checking keeps working.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from backend import config

log = logging.getLogger("sachscan.db")


class SupabaseUnavailable(RuntimeError):
    pass


class SupabaseError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Supabase {status}: {message}")
        self.status = status


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY)


_http: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _http


class Rest:
    def __init__(self, user_token: Optional[str] = None):
        if not config.SUPABASE_URL:
            raise SupabaseUnavailable("SUPABASE_URL is not set")
        if user_token is None and not config.SUPABASE_SERVICE_ROLE_KEY:
            raise SupabaseUnavailable("SUPABASE_SERVICE_ROLE_KEY is not set")
        if user_token is not None and not config.SUPABASE_ANON_KEY:
            raise SupabaseUnavailable("SUPABASE_ANON_KEY is not set")
        key = config.SUPABASE_ANON_KEY if user_token else config.SUPABASE_SERVICE_ROLE_KEY
        self.base = config.SUPABASE_URL.rstrip("/")
        self.headers = {"apikey": key}
        # New-style keys (sb_publishable_… / sb_secret_…) are not JWTs and go only in `apikey`;
        # legacy anon / service_role keys are JWTs and are also sent as the bearer token.
        bearer = user_token or (None if key.startswith("sb_") else key)
        if bearer:
            self.headers["Authorization"] = f"Bearer {bearer}"

    async def _request(self, method: str, path: str, *, params=None, json=None, headers=None, retry: bool = True) -> httpx.Response:
        h = {**self.headers, **(headers or {})}
        for attempt in (1, 2):
            try:
                r = await _client().request(method, f"{self.base}{path}", params=params, json=json, headers=h)
            except httpx.HTTPError as e:
                if attempt == 2 or not retry:
                    raise SupabaseUnavailable(f"Supabase unreachable: {e}") from e
                continue
            if r.status_code >= 500 and attempt == 1 and retry:
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise SupabaseError(r.status_code, str(msg)[:300])
            return r
        raise SupabaseUnavailable("unreachable")  # pragma: no cover

    # ---- PostgREST ---------------------------------------------------------
    async def select(self, table: str, params: dict[str, Any]) -> list[dict]:
        r = await self._request("GET", f"/rest/v1/{table}", params=params)
        return r.json()

    async def insert(self, table: str, rows: dict | list[dict], *, upsert: bool = False, on_conflict: Optional[str] = None, returning: bool = True) -> list[dict]:
        prefer = ["return=representation" if returning else "return=minimal"]
        if upsert:
            prefer.append("resolution=merge-duplicates")
        params = {"on_conflict": on_conflict} if on_conflict else None
        r = await self._request("POST", f"/rest/v1/{table}", params=params, json=rows, headers={"Prefer": ",".join(prefer)})
        return r.json() if returning and r.content else []

    async def update(self, table: str, params: dict[str, Any], values: dict) -> list[dict]:
        r = await self._request("PATCH", f"/rest/v1/{table}", params=params, json=values, headers={"Prefer": "return=representation"})
        return r.json() if r.content else []

    async def delete(self, table: str, params: dict[str, Any]) -> list[dict]:
        r = await self._request("DELETE", f"/rest/v1/{table}", params=params, headers={"Prefer": "return=representation"})
        return r.json() if r.content else []

    # ---- Storage -----------------------------------------------------------
    async def upload(self, bucket: str, path: str, data: bytes, mime: str) -> str:
        h = {**self.headers, "Content-Type": mime or "application/octet-stream", "x-upsert": "true"}
        try:
            r = await _client().post(f"{self.base}/storage/v1/object/{bucket}/{path}", content=data, headers=h, timeout=30)
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(f"Storage unreachable: {e}") from e
        if r.status_code >= 400:
            raise SupabaseError(r.status_code, r.text[:300])
        return path

    async def remove(self, bucket: str, paths: list[str]) -> None:
        if paths:
            await self._request("DELETE", f"/storage/v1/object/{bucket}", json={"prefixes": paths})


def service() -> Rest:
    return Rest(None)


def as_user(token: str) -> Rest:
    return Rest(token)
