"""Accounts, sessions, API keys and monthly scan quota (SQLite, stdlib only).

Passwords: PBKDF2-HMAC-SHA256 with a per-user salt. Session tokens and API keys are
random and only their SHA-256 hashes are stored.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from fastapi import Header, HTTPException, Request

import config

log = logging.getLogger("sachscan.auth")

DB_PATH = config.DATA_DIR / "sachscan.db"
PBKDF2_ROUNDS = 240_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                pw_hash TEXT,
                pw_salt TEXT,
                provider TEXT NOT NULL DEFAULT 'password',
                api_key_hash TEXT UNIQUE,
                api_key_hint TEXT,
                scans_used INTEGER NOT NULL DEFAULT 0,
                quota_month TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            """
        )
    return _conn


@contextmanager
def tx():
    with _lock:
        conn = _db()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return digest, salt


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    return email


# ---------------------------------------------------------------------------
# Rate limiting (in-memory sliding window)
# ---------------------------------------------------------------------------

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_s: int) -> None:
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests. Please wait a little and try again.")
    q.append(now)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _public(row: sqlite3.Row) -> dict:
    used = row["scans_used"] if row["quota_month"] == _month() else 0
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "provider": row["provider"],
        "scansLeft": max(0, config.FREE_MONTHLY_SCANS - used),
        "scansTotal": config.FREE_MONTHLY_SCANS,
        "apiKeyHint": row["api_key_hint"],
        "createdAt": row["created_at"],
    }


def _new_api_key() -> tuple[str, str, str]:
    key = "sach_live_sec_" + secrets.token_urlsafe(24)
    return key, _sha(key), key[:18] + "…" + key[-4:]


def create_user(name: str, email: str, password: Optional[str], provider: str = "password") -> tuple[dict, str]:
    email = normalise_email(email)
    name = name.strip()[:120] or email.split("@")[0]
    pw_hash = pw_salt = None
    if password is not None:
        pw_hash, pw_salt = hash_password(password)
    api_key, key_hash, hint = _new_api_key()
    with tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, f'An account with "{email}" is already registered. Please sign in instead.')
        cur = conn.execute(
            "INSERT INTO users(name,email,pw_hash,pw_salt,provider,api_key_hash,api_key_hint,scans_used,quota_month,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, email, pw_hash, pw_salt, provider, key_hash, hint, 0, _month(), datetime.now(timezone.utc).isoformat()),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _public(row), api_key


def get_or_create_sso_user(name: str, email: str, provider: str) -> dict:
    email = normalise_email(email)
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row:
        return _public(row)
    user, _ = create_user(name, email, None, provider)
    return user


def verify_login(email: str, password: str) -> dict:
    email = normalise_email(email)
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        raise HTTPException(404, f'No account found for "{email}". Please create an account first to claim your {config.FREE_MONTHLY_SCANS} free monthly scans!')
    if not row["pw_hash"]:
        raise HTTPException(400, f"This account uses {row['provider'].title()} sign-in. Please use that button instead.")
    digest, _ = hash_password(password, row["pw_salt"])
    if not hmac.compare_digest(digest, row["pw_hash"]):
        raise HTTPException(401, "The password you entered does not match our records. Please try again or reset it.")
    return _public(row)


def rotate_api_key(user_id: int) -> tuple[dict, str]:
    api_key, key_hash, hint = _new_api_key()
    with tx() as conn:
        conn.execute("UPDATE users SET api_key_hash=?, api_key_hint=? WHERE id=?", (key_hash, hint, user_id))
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _public(row), api_key


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: int, remember: bool = True) -> str:
    token = secrets.token_urlsafe(32)
    ttl = config.SESSION_DAYS * 86400 if remember else 86400
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (_sha(token), user_id, time.time() + ttl))
    return token


def delete_session(token: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))


def user_from_token(token: Optional[str]) -> Optional[sqlite3.Row]:
    if not token:
        return None
    with tx() as conn:
        return conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
            (_sha(token), time.time()),
        ).fetchone()


def user_from_api_key(key: Optional[str]) -> Optional[sqlite3.Row]:
    if not key:
        return None
    with tx() as conn:
        return conn.execute("SELECT * FROM users WHERE api_key_hash=?", (_sha(key),)).fetchone()


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def optional_user(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None)) -> Optional[sqlite3.Row]:
    """FastAPI dependency: the signed-in user (session token or API key), or None."""
    token = _bearer(authorization)
    if token and token.startswith("sach_live_sec_"):
        return user_from_api_key(token)
    return user_from_token(token) or user_from_api_key(x_api_key)


def required_user(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None)) -> sqlite3.Row:
    user = optional_user(authorization, x_api_key)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def public_user(row: sqlite3.Row) -> dict:
    return _public(row)


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

def consume_scan(user_id: int) -> int:
    """Use one scan; returns scans left. Raises 402 when the monthly quota is exhausted."""
    month = _month()
    with tx() as conn:
        row = conn.execute("SELECT scans_used, quota_month FROM users WHERE id=?", (user_id,)).fetchone()
        used = row["scans_used"] if row["quota_month"] == month else 0
        if used >= config.FREE_MONTHLY_SCANS:
            raise HTTPException(402, f"You have used all {config.FREE_MONTHLY_SCANS} free scans for this month.")
        conn.execute("UPDATE users SET scans_used=?, quota_month=? WHERE id=?", (used + 1, month, user_id))
    return config.FREE_MONTHLY_SCANS - used - 1


def refund_scan(user_id: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET scans_used=MAX(0, scans_used-1) WHERE id=? AND quota_month=?", (user_id, _month()))


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def start_password_reset(email: str, base_url: str) -> None:
    try:
        email = normalise_email(email)
    except HTTPException:
        return
    with tx() as conn:
        row = conn.execute("SELECT id, name FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            return  # do not reveal whether the account exists
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)", (_sha(token), row["id"], time.time() + 3600))
    link = f"{base_url}/login.html#reset={token}"
    if config.SMTP_HOST:
        msg = EmailMessage()
        msg["Subject"] = "Reset your SachScan password"
        msg["From"] = config.SMTP_FROM
        msg["To"] = email
        msg.set_content(f"Hi {row['name']},\n\nReset your SachScan password here (valid for 1 hour):\n{link}\n\nIf you did not ask for this, ignore this email.")
        try:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        except (smtplib.SMTPException, OSError):
            log.exception("Failed to send reset email")
    else:
        log.warning("Password reset link for %s (SMTP not configured): %s", email, link)


def finish_password_reset(token: str, password: str) -> dict:
    with tx() as conn:
        row = conn.execute("SELECT user_id FROM password_resets WHERE token_hash=? AND expires_at>?", (_sha(token), time.time())).fetchone()
        if not row:
            raise HTTPException(400, "This reset link is invalid or has expired. Please request a new one.")
        digest, salt = hash_password(password)
        conn.execute("UPDATE users SET pw_hash=?, pw_salt=?, provider='password' WHERE id=?", (digest, salt, row["user_id"]))
        conn.execute("DELETE FROM password_resets WHERE user_id=?", (row["user_id"],))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    return _public(user)
