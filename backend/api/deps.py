"""Supabase Auth for FastAPI: `get_current_user` verifies the Supabase JWT on protected endpoints.

Verification order:
  1. Asymmetric signing keys (current Supabase default): JWKS from {SUPABASE_URL}/auth/v1/.well-known/jwks.json
  2. Legacy HS256 projects: SUPABASE_JWT_SECRET
  3. If neither can be used (e.g. JWKS unreachable), ask Supabase Auth itself: GET /auth/v1/user

On a user's first request a `profiles` row is upserted (id, email, name, avatar).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt
from fastapi import Header, HTTPException

from backend import config
from backend.db import supabase as db

log = logging.getLogger("sachscan.auth")


@dataclass
class CurrentUser:
    id: str
    email: str
    name: str
    avatar_url: str
    token: str  # the raw JWT, used to query Postgres under RLS


_jwks_client: Optional[jwt.PyJWKClient] = None
_profiles_seen: set[str] = set()
_remote_cache: dict[str, tuple[float, dict]] = {}


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=5)
    return _jwks_client


def _decode_local(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "")
    opts = {"require": ["exp", "sub"]}
    if alg == "HS256":
        if not config.SUPABASE_JWT_SECRET:
            raise LookupError("HS256 token but SUPABASE_JWT_SECRET is not set")
        return jwt.decode(token, config.SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated", options=opts)
    key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256", "EdDSA"], audience="authenticated", options=opts)


async def _decode_remote(token: str) -> dict:
    cached = _remote_cache.get(token)
    if cached and cached[0] > time.time():
        return cached[1]
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(f"{config.SUPABASE_URL}/auth/v1/user", headers={"apikey": config.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise jwt.InvalidTokenError("Supabase rejected the token")
    u = r.json()
    claims = {"sub": u["id"], "email": u.get("email", ""), "user_metadata": u.get("user_metadata") or {}}
    _remote_cache[token] = (time.time() + 60, claims)
    return claims


async def _verify(token: str) -> dict:
    try:
        return await asyncio.to_thread(_decode_local, token)
    except jwt.ExpiredSignatureError:
        raise
    except (LookupError, jwt.PyJWKClientError) as e:
        log.debug("local JWT verification unavailable (%s); asking Supabase", e)
        return await _decode_remote(token)


async def _upsert_profile(user: CurrentUser) -> None:
    if user.id in _profiles_seen or not db.configured():
        return
    try:
        await db.service().insert("profiles", {"id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url},
                                  upsert=True, on_conflict="id", returning=False)
        _profiles_seen.add(user.id)
    except (db.SupabaseUnavailable, db.SupabaseError) as e:
        log.warning("profile upsert failed: %s", e)


async def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    if not config.SUPABASE_URL:
        raise HTTPException(503, "Sign-in is not configured on the server (SUPABASE_URL missing).")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in.")
    token = authorization[7:].strip()
    try:
        claims = await _verify(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Your session expired. Please sign in again.")
    except (jwt.InvalidTokenError, httpx.HTTPError, KeyError):
        raise HTTPException(401, "Please sign in again.")
    meta = claims.get("user_metadata") or {}
    user = CurrentUser(
        id=claims["sub"],
        email=claims.get("email", "") or meta.get("email", ""),
        name=meta.get("full_name") or meta.get("name") or "",
        avatar_url=meta.get("avatar_url") or meta.get("picture") or "",
        token=token,
    )
    await _upsert_profile(user)
    return user


async def optional_current_user(authorization: Optional[str] = Header(None)) -> Optional[CurrentUser]:
    if not authorization:
        return None
    return await get_current_user(authorization)
