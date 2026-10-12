"""Row Level Security: one user can never read or change another user's conversations.

Runs against the real Supabase project in backend/.env (skipped when it isn't configured).
Creates two throwaway users with the service key, signs them in, and deletes them afterwards.
"""
import os
import secrets
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

URL = os.environ.get("RLS_TEST_SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("RLS_TEST_SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("RLS_TEST_SUPABASE_SERVICE_ROLE_KEY", "")

pytestmark = pytest.mark.skipif(not (URL and ANON and SERVICE), reason="Supabase not configured in backend/.env")


def _service_headers():
    h = {"apikey": SERVICE}
    if not SERVICE.startswith("sb_"):
        h["Authorization"] = f"Bearer {SERVICE}"
    return h


@pytest.fixture(scope="module")
def users():
    made = []
    with httpx.Client(timeout=20) as c:
        for _ in range(2):
            email = f"rls-test-{uuid.uuid4().hex[:10]}@example.com"
            password = secrets.token_urlsafe(18)
            r = c.post(f"{URL}/auth/v1/admin/users", headers=_service_headers(),
                       json={"email": email, "password": password, "email_confirm": True})
            assert r.status_code in (200, 201), r.text
            uid = r.json()["id"]
            made.append(uid)
            t = c.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                       json={"email": email, "password": password})
            assert t.status_code == 200, f"password sign-in failed (is the Email provider enabled?): {t.text}"
            made[-1] = {"id": uid, "token": t.json()["access_token"]}
    yield made
    with httpx.Client(timeout=20) as c:
        for u in made:
            uid = u["id"] if isinstance(u, dict) else u
            c.delete(f"{URL}/auth/v1/admin/users/{uid}", headers=_service_headers())


def rest(token):
    return httpx.Client(base_url=f"{URL}/rest/v1", timeout=20,
                        headers={"apikey": ANON, "Authorization": f"Bearer {token}", "Prefer": "return=representation"})


def test_users_cannot_see_each_others_conversations(users):
    a, b = users
    with rest(a["token"]) as ca, rest(b["token"]) as cb:
        conv = ca.post("/conversations", json={"user_id": a["id"], "platform": "web", "title": "A's private check"})
        assert conv.status_code == 201, conv.text
        conv_id = conv.json()[0]["id"]
        msg = ca.post("/messages", json={"conversation_id": conv_id, "role": "user", "content_type": "text", "text": "secret"})
        assert msg.status_code == 201, msg.text

        # A sees their own data
        assert len(ca.get("/conversations", params={"select": "id"}).json()) == 1
        assert len(ca.get("/messages", params={"select": "id"}).json()) == 1

        # B sees nothing
        assert cb.get("/conversations", params={"select": "id"}).json() == []
        assert cb.get("/conversations", params={"id": f"eq.{conv_id}", "select": "id"}).json() == []
        assert cb.get("/messages", params={"conversation_id": f"eq.{conv_id}", "select": "id"}).json() == []

        # B cannot write into A's conversation, take it over, or delete it
        assert cb.post("/messages", json={"conversation_id": conv_id, "role": "user", "text": "intrusion"}).status_code in (401, 403)
        assert cb.post("/conversations", json={"user_id": a["id"], "platform": "web", "title": "spoof"}).status_code in (401, 403)
        assert cb.patch("/conversations", params={"id": f"eq.{conv_id}"}, json={"user_id": b["id"]}).json() == []
        assert cb.delete("/conversations", params={"id": f"eq.{conv_id}"}).json() == []
        assert len(ca.get("/conversations", params={"select": "id"}).json()) == 1  # still there

    # Server-only tables are invisible to signed-in users
    with rest(b["token"]) as cb:
        for table in ("checks_cache", "link_codes", "integration_settings"):
            assert cb.get(f"/{table}", params={"select": "*"}).json() == []


def test_api_enforces_ownership(users, monkeypatch):
    """Same guarantee through the FastAPI endpoints (also exercises Supabase JWT verification)."""
    from backend import config, main

    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", ANON)
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", SERVICE)
    a, b = users
    client = TestClient(main.app)
    ha, hb = {"Authorization": f"Bearer {a['token']}"}, {"Authorization": f"Bearer {b['token']}"}

    a_convs = client.get("/conversations", headers=ha)
    assert a_convs.status_code == 200, a_convs.text
    conv_id = a_convs.json()["conversations"][0]["id"]

    assert client.get("/conversations", headers=hb).json()["conversations"] == []
    assert client.get(f"/conversations/{conv_id}/messages", headers=hb).status_code == 404
    assert client.delete(f"/conversations/{conv_id}", headers=hb).status_code == 404
    assert client.get(f"/conversations/{conv_id}/messages", headers=ha).json()["messages"][0]["text"] == "secret"

    # tampered token is rejected
    bad = a["token"][:-4] + ("AAAA" if not a["token"].endswith("AAAA") else "BBBB")
    assert client.get("/conversations", headers={"Authorization": f"Bearer {bad}"}).status_code == 401

    # owner can delete
    assert client.delete(f"/conversations/{conv_id}", headers=ha).status_code == 200
