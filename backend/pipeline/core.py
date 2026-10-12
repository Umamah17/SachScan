"""The single fact-checking pipeline shared by every channel.

    check_content(NormalizedInput) -> CheckResult

Web chat, WhatsApp, Discord, Telegram, Gmail and the Chrome extension only convert their input to
NormalizedInput, call check_content, and format the reply for their platform.

Steps: cache lookup -> Extract -> Split -> Search -> Judge -> cache store.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional

from backend import config
from backend.pipeline import cache
from backend.pipeline import judge as judge_mod
from backend.pipeline import llm
from backend.pipeline import signals
from backend.pipeline.claims import split_claims
from backend.pipeline.extract import ExtractionError, Extracted, classify_mime, extract_file, extract_text_or_url, extract_url
from backend.pipeline.schemas import AIGeneration, CheckResponse
from backend.pipeline.search import search_all

log = logging.getLogger("sachscan.pipeline")

CheckResult = CheckResponse  # schema from CLAUDE.md (plus UI extensions)

Platform = Literal["web", "whatsapp", "discord", "telegram", "gmail", "extension"]
ContentType = Literal["text", "image", "audio", "pdf", "url", "video"]

PROGRESS_TEXT = {
    "extract": "1/4 Reading the message…",
    "split": "2/4 Splitting it into individual claims (Claim DNA)…",
    "search": "3/4 Searching trusted sources (PIB, *.gov.in, WHO, fact-checkers)…",
    "judge": "4/4 Weighing the evidence and writing the verdict…",
}

MB = 1024 * 1024


@dataclass
class NormalizedInput:
    platform: Platform
    content_type: ContentType = "text"
    text: str = ""
    file_bytes: Optional[bytes] = None
    mime_type: str = ""
    external_user_id: Optional[str] = None
    user_id: Optional[str] = None
    original_message_id: Optional[str] = None
    file_name: str = ""
    extra: dict = field(default_factory=dict)  # channel-specific context (never used by the pipeline)


class CheckError(Exception):
    """User-facing failure (bad input, unreadable file, ...). `status` maps to HTTP codes."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


Progress = Optional[Callable[[dict], Awaitable[None]]]


def content_type_for(mime: str, file_name: str = "") -> ContentType:
    return classify_mime(mime, file_name)  # type: ignore[return-value]


def validate_size(inp: NormalizedInput) -> None:
    if not inp.file_bytes:
        return
    limit = config.MAX_PDF_MB if inp.content_type == "pdf" else config.MAX_MEDIA_MB
    if len(inp.file_bytes) > limit * MB:
        kind = "PDFs" if inp.content_type == "pdf" else "images, voice notes and videos"
        raise CheckError(f"That file is too large. We can check {kind} up to {limit} MB.", status=413)


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


async def _emit(progress: Progress, event: dict) -> None:
    if progress is not None:
        try:
            await progress(event)
        except Exception:  # a broken client stream must never break the check
            log.debug("progress callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# check_content
# ---------------------------------------------------------------------------

async def check_content(inp: NormalizedInput, progress: Progress = None) -> CheckResult:
    """Fact-check one input. Raises CheckError for user-facing problems."""
    t0 = time.time()
    timings: dict[str, float] = {}
    providers: dict[str, str] = {}
    validate_size(inp)

    # 0. Cache before any AI call (raw file hash for media, normalised text otherwise)
    pre_key = cache.bytes_key(inp.file_bytes) if inp.file_bytes else (cache.text_key("text", inp.text) if inp.text.strip() else None)
    if pre_key:
        hit = await cache.get(pre_key)
        if hit is not None:
            hit.meta = {**hit.meta, "cached": True, "elapsed_s": round(time.time() - t0, 2)}
            return hit

    # 1. Extract -----------------------------------------------------------
    await _emit(progress, {"type": "progress", "step": "extract", "message": PROGRESS_TEXT["extract"]})
    try:
        if inp.file_bytes:
            ex = await extract_file(inp.file_bytes, inp.mime_type, inp.file_name)
            if inp.text.strip():  # caption shared alongside media
                ex.text = f"{inp.text.strip()}\n\n{ex.text}".strip()
        elif inp.content_type == "url" and inp.text.strip():
            ex = await extract_url(inp.text.strip())
        elif inp.text.strip():
            ex = await extract_text_or_url(inp.text)
        else:
            raise CheckError("Please paste a message, a link, or attach a file to check.", status=400)
    except ExtractionError as e:
        raise CheckError(str(e)) from e
    providers["extract"] = ex.provider
    timings["extract"] = round(time.time() - t0, 2)
    if not ex.text.strip():
        raise CheckError("We could not find any text or speech in that input.")

    text_key = cache.text_key(ex.input_type, ex.text)
    if text_key != pre_key:
        hit = await cache.get(text_key)
        if hit is not None:
            hit.meta = {**hit.meta, "cached": True, "elapsed_s": round(time.time() - t0, 2)}
            return hit

    # 2. Split ---------------------------------------------------------------
    await _emit(progress, {"type": "progress", "step": "split", "message": PROGRESS_TEXT["split"]})
    t = time.time()
    split, providers["split"] = await split_claims(ex.text)
    lang = split.language
    timings["split"] = round(time.time() - t, 2)
    heuristic_signals = signals.scam_signals(ex.text, lang)

    if not split.claims:
        t_ = judge_mod.OFFLINE.get(lang, judge_mod.OFFLINE["en"])
        return CheckResponse(
            input_type=ex.input_type, language=lang, extracted_text=ex.text,
            overall_summary=t_["no_claims"], claims=[], scam_signals=heuristic_signals,
            overall_verdict="cannot_be_confirmed", confidence=0, share_safe_reply="",
            ai_generation=AIGeneration(**signals.ai_style(ex.text)),
            meta={"providers": providers, "timings": timings, "elapsed_s": round(time.time() - t0, 2)},
        )

    await _emit(progress, {"type": "claims", "claims": split.claims, "language": lang})

    # 3. Search --------------------------------------------------------------
    await _emit(progress, {"type": "progress", "step": "search", "message": PROGRESS_TEXT["search"]})
    t = time.time()
    evidence = await search_all(split.claims, split.topic, ex.text if len(split.claims) > 1 else "", lang)
    providers["search"] = ",".join(sorted({e.provider for e in evidence}))
    timings["search"] = round(time.time() - t, 2)

    # 4. Judge ---------------------------------------------------------------
    await _emit(progress, {"type": "progress", "step": "judge", "message": PROGRESS_TEXT["judge"]})
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
    if out is not None:  # never cache an offline fallback
        await cache.put([k for k in (pre_key, text_key) if k], result)
    return result
