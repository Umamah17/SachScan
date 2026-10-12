"""check_content with mocked LLMs and search (no network)."""
import asyncio

import pytest

from backend.pipeline import cache, core, llm, search
from backend.pipeline.core import CheckError, NormalizedInput, check_content
from backend.pipeline.search import Evidence, EvidenceSource

FORWARD = "Urgent: PM announces Rs 50,000 for all students under National Youth Scheme. Click tinyurl.com/pm-grant-2025 now!"


@pytest.fixture
def mocked_ai(monkeypatch):
    calls = {"split": 0, "judge": 0, "search": 0}

    async def fake_json_with_fallback(prompt, validate, *, order=("gemini", "claude"), media=None, system=None):
        attempt = llm.JSONAttempt()
        if "split WhatsApp forwards" in prompt:
            calls["split"] += 1
            attempt.value = validate({"language": "en", "topic": "scheme", "claims": [
                "PM announced Rs 50,000 for all students", "tinyurl.com/pm-grant-2025 is the official portal"]})
        else:
            calls["judge"] += 1
            assert "S1.1" in prompt  # evidence ids are given to the judge
            attempt.value = validate({
                "overall_summary": "This is a fake scheme.", "share_safe_reply": "Please don't forward this.",
                "claims": [
                    {"claim": "PM announced Rs 50,000 for all students", "verdict": "FALSE", "explanation": "PIB says it is fake.",
                     "confidence": "92", "sources": [{"id": "S1.1", "quote": "This claim is fake", "date": "2025-01-02"},
                                                     {"id": "S9.9", "quote": "hallucinated source"}]},
                    {"claim": "tinyurl.com/pm-grant-2025 is the official portal", "verdict": "verified", "explanation": "x",
                     "confidence": 80, "sources": []},
                ],
                "scam_signals": ["Urgency"], "ai_generation": {"score": 70, "likely_ai": True, "indicators": ["generic"]},
            })
        attempt.provider = "mock"
        return attempt

    async def fake_search_all(claims, topic, context, lang="en"):
        calls["search"] += 1
        out = []
        for c in claims:
            if "tinyurl" in c:
                out.append(Evidence(claim=c, error="nothing found"))
            else:
                out.append(Evidence(claim=c, summary="PIB says fake", provider="mock", sources=[
                    EvidenceSource(title="PIB Fact Check", url="https://pib.gov.in/x", publisher="pib.gov.in", snippet="This claim is fake")]))
        return out

    monkeypatch.setattr(llm, "json_with_fallback", fake_json_with_fallback)
    monkeypatch.setattr(core, "search_all", fake_search_all)
    monkeypatch.setattr(llm, "gemini_available", lambda: True)
    cache._local.clear()
    return calls


def run(coro):
    return asyncio.run(coro)


def test_verdicts_sources_and_schema(mocked_ai):
    r = run(check_content(NormalizedInput(platform="web", text=FORWARD)))
    assert r.overall_verdict == "false"
    first, second = r.claims
    assert first.verdict == "false" and first.confidence == 92
    assert [s.url for s in first.sources] == ["https://pib.gov.in/x"]  # hallucinated S9.9 dropped
    assert first.sources[0].date == "2025-01-02" and first.sources[0].quote == "This claim is fake"
    # a firm verdict without any retrieved source is downgraded
    assert second.verdict == "cannot_be_confirmed" and second.confidence <= 40
    assert "Urgency" in r.scam_signals and any("shortened link" in s for s in r.scam_signals)
    assert r.language == "en" and r.input_type == "text"


def test_cache_skips_ai_on_repeat(mocked_ai):
    run(check_content(NormalizedInput(platform="whatsapp", text=FORWARD)))
    again = run(check_content(NormalizedInput(platform="telegram", text="  " + FORWARD.upper() + " ")))
    assert again.meta.get("cached") is True
    assert mocked_ai == {"split": 1, "judge": 1, "search": 1}


def test_progress_events(mocked_ai):
    events = []

    async def progress(ev):
        events.append(ev.get("step") or ev["type"])

    run(check_content(NormalizedInput(platform="web", text=FORWARD + " v2"), progress))
    assert events == ["extract", "split", "claims", "search", "judge"]


def test_offline_fallback_when_judge_fails(mocked_ai, monkeypatch):
    async def failing(prompt, validate, **kw):
        a = llm.JSONAttempt()
        if "split WhatsApp forwards" in prompt:
            a.value = validate({"language": "hi", "topic": "health", "claims": ["गर्म पानी से कोरोना ठीक होता है"]})
        return a  # judge: every provider failed

    monkeypatch.setattr(llm, "json_with_fallback", failing)
    r = run(check_content(NormalizedInput(platform="web", text="गर्म पानी पीने से कोरोना पूरी तरह ठीक हो जाता है, सबको भेजें!")))
    assert r.language == "hi"
    assert all(c.verdict == "cannot_be_confirmed" for c in r.claims)
    assert r.meta["degraded"] is True
    assert cache._local == {}  # offline answers are never cached


def test_size_limits():
    big_pdf = NormalizedInput(platform="web", content_type="pdf", file_bytes=b"0" * (11 * 1024 * 1024), mime_type="application/pdf")
    with pytest.raises(CheckError) as e:
        run(check_content(big_pdf))
    assert e.value.status == 413 and "10 MB" in str(e.value)
    big_img = NormalizedInput(platform="web", content_type="image", file_bytes=b"0" * (17 * 1024 * 1024), mime_type="image/png")
    with pytest.raises(CheckError):
        run(check_content(big_img))


def test_empty_input_is_user_error():
    with pytest.raises(CheckError) as e:
        run(check_content(NormalizedInput(platform="web", text="   ")))
    assert e.value.status == 400
