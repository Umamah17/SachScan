"""Step 2 - split a forward into atomic, checkable claims and detect its language."""
from __future__ import annotations

import re
from datetime import date

from backend import config
from backend.pipeline import llm
from backend.pipeline.schemas import SplitOutput
from backend.pipeline.signals import detect_language

SPLIT_PROMPT = """You split WhatsApp forwards into individual factual claims for a fact-checker in India.
Today's date is {today}.

Message:
<<<
{text}
>>>

Rules:
- Extract at most {max_claims} independently checkable factual claims (usually 1-3).
- Merge closely related details of the same fact into ONE claim (e.g. "PM announced ₹50,000 for all students under X scheme" is one claim, not four).
- Keep each claim short and self-contained, in the SAME language and script as the message.
- Include implied claims that matter (e.g. "this link is the official government portal").
- Skip greetings, opinions, emojis, and instructions like "forward this" (they are not facts).
- If the message has no checkable claim at all, return an empty list.
- language: "en", "hi" or "mr" — the main language of the message (Marathi and Hindi both use Devanagari; decide by vocabulary).
- topic: one of health, scheme, finance, news, science, other.

Reply with ONLY this JSON:
{{"language": "en|hi|mr", "topic": "...", "claims": ["...", "..."]}}"""


def _fallback_split(text: str) -> list[str]:
    """Sentence split used when no LLM is available."""
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text)
    claims = []
    for p in parts:
        p = p.strip(" -•*\t")
        if len(p) < 25 or re.fullmatch(r"(forward|share).*", p, re.I):
            continue
        claims.append(p)
    return claims[: config.MAX_CLAIMS] or ([text.strip()[:300]] if text.strip() else [])


async def split_claims(text: str) -> tuple[SplitOutput, str]:
    prompt = SPLIT_PROMPT.format(today=date.today().isoformat(), text=text[: config.MAX_TEXT_CHARS], max_claims=config.MAX_CLAIMS)
    attempt = await llm.json_with_fallback(prompt, SplitOutput.model_validate, order=("openrouter", "gemini", "claude"))
    if attempt.value is not None:
        out: SplitOutput = attempt.value
        out.claims = [c.strip() for c in out.claims if c and c.strip()][: config.MAX_CLAIMS]
        # Trust the script check when the model disagrees about English vs Devanagari
        heuristic = detect_language(text)
        if (heuristic == "en") != (out.language == "en"):
            out.language = heuristic
        return out, attempt.provider
    return SplitOutput(language=detect_language(text), claims=_fallback_split(text), topic="other"), "heuristic"
