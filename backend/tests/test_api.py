"""Offline smoke tests (no API keys needed): run with  .venv/Scripts/python -m pytest tests -q"""
import json
import os
import tempfile

os.environ["SACHSCAN_DATA_DIR"] = tempfile.mkdtemp()
for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY"):
    os.environ[k] = ""

from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402

config.GEMINI_API_KEY = config.ANTHROPIC_API_KEY = config.PERPLEXITY_API_KEY = ""
import main  # noqa: E402

c = TestClient(main.app)
FORWARDS = json.load(open(os.path.join(os.path.dirname(__file__), "forwards.json"), encoding="utf-8"))


def test_static_and_bridge():
    r = c.get("/help")
    assert r.status_code == 200 and "sachscan-bridge.js" in r.text
    assert c.get("/backend/.env").status_code == 404
    assert "share_target" in c.get("/manifest.json").json()


def test_schema_and_languages():
    for fwd in FORWARDS:
        d = c.post("/check", json={"text": fwd["text"]}).json()
        assert d["language"] == fwd["language"], fwd["text"]
        assert {"input_type", "language", "extracted_text", "overall_summary", "claims", "scam_signals"} <= d.keys()
        for claim in d["claims"]:
            assert claim["verdict"] in ("verified", "false", "outdated", "partly_supported", "cannot_be_confirmed")


def test_stream_events():
    r = c.post("/api/check/stream", data={"text": FORWARDS[0]["text"]})
    types = [json.loads(line)["type"] for line in r.text.splitlines()]
    assert types[0] == "progress" and types[-1] == "result"


def test_auth_and_quota():
    r = c.post("/api/auth/register", json={"name": "T", "email": "t@example.com", "password": "secret1"})
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/auth/me", headers=h).json()["user"]["scansLeft"] == config.FREE_MONTHLY_SCANS
    d = c.post("/check", json={"text": FORWARDS[0]["text"]}, headers=h).json()
    assert d["meta"]["scans_left"] == config.FREE_MONTHLY_SCANS - 1
    assert c.post("/api/auth/login", json={"email": "t@example.com", "password": "wrong1"}).status_code == 401
    c.post("/api/auth/logout", headers=h)
    assert c.get("/api/auth/me", headers=h).status_code == 401


def test_ssrf_blocked():
    assert c.post("/check", json={"url": "http://127.0.0.1/"}).status_code == 422


def test_share_target_roundtrip():
    r = c.post("/share-target", data={"text": FORWARDS[0]["text"]}, follow_redirects=False)
    sid = r.headers["location"].split("shared=")[1]
    assert c.get(f"/api/shared/{sid}").json()["text"]
    c.post(f"/api/shared/{sid}/check/stream")
    assert c.get(f"/api/shared/{sid}").status_code == 404
