"""Orchestrates Extract -> Split -> Search -> Judge and streams progress events."""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import AsyncIterator, Optional

import config
import judge as judge_mod
import llm
import signals
from claims import split_claims
from extract import Extracted, extract_file, extract_text_or_url, extract_url
from schemas import AIGeneration, CheckResponse
from search import search_all

log = logging.getLogger("sachscan.pipeline")

PROGRESS_TEXT = {
    "extract": "1/4 Reading the message…",
    "split": "2/4 Splitting it into individual claims (Claim DNA)…",
    "search": "3/4 Searching trusted sources (PIB, *.gov.in, WHO, fact-checkers)…",
    "judge": "4/4 Weighing the evidence and writing the verdict…",
}


# ---------------------------------------------------------------------------
# Tiny in-memory result cache (never written to disk)
# ---------------------------------------------------------------------------

class _TTLCache:
    def __init__(self, ttl: int, size: int = 200):
        self.ttl, self.size, self.data = ttl, size, OrderedDict()

    def get(self, key):
        item = self.data.get(key)
        if not item or time.time() - item[0] > self.ttl:
            self.data.pop(key, None)
            return None
        return item[1]

    def put(self, key, value):
        if self.ttl <= 0:
            return
        self.data[key] = (time.time(), value)
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


_cache = _TTLCache(config.RESULT_CACHE_TTL_S)


def _cache_key(ex: Extracted) -> str:
    return hashlib.sha256(f"{ex.input_type}\x00{' '.join(ex.text.split()).lower()}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def overall_verdict(verdicts: list[str]) -> str:
    vs = set(verdicts)
    if not vs:
        return "cannot_be_confirmed"
    if len(vs) == 1:
        return vs.pop()
    firm = vs - {"cannot_be_confirmed"}
    if len(firm) == 1:
        only = next(iter(firm))
        return "partly_supported" if only == "verified" else only
    return "partly_supported"


def _merge_signals(*lists: list[str]) -> list[str]:
    seen, out = set(), []
    for lst in lists:
        for s in lst or []:
            key = s.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(s.strip())
    return out[:8]


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def run_check(
    *,
    text: Optional[str] = None,
    url: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    file_mime: str = "",
    file_name: str = "",
) -> AsyncIterator[dict]:
    """Yields {"type": "progress", ...} events and finally {"type": "result", "data": CheckResponse}
    or {"type": "error", "message": ...}. ExtractionError messages are user-facing."""
    t0 = time.time()
    timings: dict[str, float] = {}
    providers: dict[str, str] = {}

    # 1. Extract -----------------------------------------------------------
    yield {"type": "progress", "step": "extract", "message": PROGRESS_TEXT["extract"]}
    if file_bytes:
        ex = await extract_file(file_bytes, file_mime, file_name)
        if text and text.strip():  # caption shared alongside media
            ex.text = f"{text.strip()}\n\n{ex.text}".strip()
    elif url and url.strip():
        ex = await extract_url(url.strip())
    elif text and text.strip():
        ex = await extract_text_or_url(text)
    else:
        yield {"type": "error", "status": 400, "message": "Please paste a message, a link, or attach a file to check."}
        return
    providers["extract"] = ex.provider
    timings["extract"] = round(time.time() - t0, 2)
    if not ex.text.strip():
        yield {"type": "error", "status": 422, "message": "We could not find any text or speech in that input."}
        return

    cached = _cache.get(_cache_key(ex))
    if cached is not None:
        result = cached.model_copy(deep=True)
        result.meta = {**result.meta, "cached": True, "elapsed_s": round(time.time() - t0, 2)}
        yield {"type": "result", "data": result}
        return

    # 2. Split ---------------------------------------------------------------
    yield {"type": "progress", "step": "split", "message": PROGRESS_TEXT["split"]}
    t = time.time()
    split, providers["split"] = await split_claims(ex.text)
    lang = split.language
    timings["split"] = round(time.time() - t, 2)
    heuristic_signals = signals.scam_signals(ex.text, lang)

    if not split.claims:
        t_ = judge_mod.OFFLINE.get(lang, judge_mod.OFFLINE["en"])
        result = CheckResponse(
            input_type=ex.input_type, language=lang, extracted_text=ex.text,
            overall_summary=t_["no_claims"], claims=[], scam_signals=heuristic_signals,
            overall_verdict="cannot_be_confirmed", confidence=0, share_safe_reply="",
            ai_generation=AIGeneration(**signals.ai_style(ex.text)),
            meta={"providers": providers, "timings": timings, "elapsed_s": round(time.time() - t0, 2)},
        )
        yield {"type": "result", "data": result}
        return

    yield {"type": "claims", "claims": split.claims, "language": lang}

    # 3. Search --------------------------------------------------------------
    yield {"type": "progress", "step": "search", "message": PROGRESS_TEXT["search"]}
    t = time.time()
    evidence = await search_all(split.claims, split.topic, ex.text if len(split.claims) > 1 else "", lang)
    providers["search"] = ",".join(sorted({e.provider for e in evidence}))
    timings["search"] = round(time.time() - t, 2)

    # 4. Judge ---------------------------------------------------------------
    yield {"type": "progress", "step": "judge", "message": PROGRESS_TEXT["judge"]}
    t = time.time()
    out, claim_results, providers["judge"] = await judge_mod.judge(ex.text, evidence, lang)
    timings["judge"] = round(time.time() - t, 2)
    offline = judge_mod.OFFLINE.get(lang, judge_mod.OFFLINE["en"])
    style = signals.ai_style(ex.text)

    if out is None:
        claim_results = judge_mod.offline_claims(evidence, lang)
        summary = offline["summary_risky"] if heuristic_signals else offline["summary"]
        reply, llm_signals, ai = offline["reply"], [], AIGeneration(**style)
    else:
        summary, reply, llm_signals = out.overall_summary, out.share_safe_reply or offline["reply"], out.scam_signals
        # Blend model judgement with the deterministic heuristic so the score is stable
        ai = AIGeneration(
            score=round(0.6 * out.ai_generation.score + 0.4 * style["score"]),
            likely_ai=out.ai_generation.likely_ai or style["likely_ai"],
            indicators=(out.ai_generation.indicators or style["indicators"])[:5],
        )

    publishers = []
    for ev in evidence:
        for s in ev.sources:
            if s.publisher and s.publisher not in publishers:
                publishers.append(s.publisher)

    result = CheckResponse(
        input_type=ex.input_type,
        language=lang,
        extracted_text=ex.text,
        overall_summary=summary,
        claims=claim_results,
        scam_signals=_merge_signals(llm_signals, heuristic_signals),
        overall_verdict=overall_verdict([c.verdict for c in claim_results]),
        confidence=round(sum(c.confidence for c in claim_results) / len(claim_results)) if claim_results else 0,
        share_safe_reply=reply,
        ai_generation=ai,
        sources_checked=publishers[:8],
        meta={
            "providers": providers,
            "timings": timings,
            "elapsed_s": round(time.time() - t0, 2),
            "degraded": out is None or any(e.error and not e.sources for e in evidence),
            "ai_configured": llm.gemini_available() or llm.claude_available(),
        },
    )
    if out is not None:
        _cache.put(_cache_key(ex), result)
    yield {"type": "result", "data": result}
