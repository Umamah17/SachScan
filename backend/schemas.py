"""Pydantic models for the /check API contract (see CLAUDE.md "API response schema")."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Verdict = Literal["verified", "false", "outdated", "partly_supported", "cannot_be_confirmed"]
InputType = Literal["text", "image", "audio", "pdf", "url", "video"]
Language = Literal["en", "hi", "mr"]

VERDICTS: tuple[str, ...] = ("verified", "false", "outdated", "partly_supported", "cannot_be_confirmed")

_VERDICT_ALIASES = {
    "true": "verified", "confirmed": "verified", "correct": "verified",
    "fake": "false", "incorrect": "false", "misleading": "partly_supported",
    "partly_true": "partly_supported", "partially_supported": "partly_supported",
    "partial": "partly_supported", "unverified": "cannot_be_confirmed",
    "unconfirmed": "cannot_be_confirmed", "unknown": "cannot_be_confirmed",
    "cannot_be_verified": "cannot_be_confirmed",
}


def normalise_verdict(v) -> str:
    v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
    v = _VERDICT_ALIASES.get(v, v)
    return v if v in VERDICTS else "cannot_be_confirmed"


def clamp_pct(v) -> int:
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 50


class Source(BaseModel):
    title: str = ""
    publisher: str = ""
    url: str = ""
    date: Optional[str] = None  # YYYY-MM-DD or null
    quote: str = ""


class ClaimResult(BaseModel):
    claim: str
    verdict: Verdict = "cannot_be_confirmed"
    explanation: str = ""
    confidence: int = 50
    sources: list[Source] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, v):
        return normalise_verdict(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        return clamp_pct(v)


class AIGeneration(BaseModel):
    """Stylometric signal only: a hint, never proof."""
    score: int = 0
    likely_ai: bool = False
    indicators: list[str] = Field(default_factory=list)


class CheckResponse(BaseModel):
    input_type: InputType = "text"
    language: Language = "en"
    extracted_text: str = ""
    overall_summary: str = ""
    claims: list[ClaimResult] = Field(default_factory=list)
    scam_signals: list[str] = Field(default_factory=list)

    # Extensions used by the SachScan UI (additive; the base contract above is unchanged)
    overall_verdict: Verdict = "cannot_be_confirmed"
    confidence: int = 0
    share_safe_reply: str = ""
    ai_generation: AIGeneration = Field(default_factory=AIGeneration)
    sources_checked: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


# ---- LLM intermediate outputs (validated, retried once on failure) ----------

class SplitOutput(BaseModel):
    language: Language = "en"
    claims: list[str] = Field(default_factory=list)
    topic: str = "general"

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, v):
        v = str(v or "en").lower()[:2]
        return v if v in ("en", "hi", "mr") else "en"


class JudgeSourceRef(BaseModel):
    id: str = ""
    quote: str = ""
    date: Optional[str] = None
    publisher: str = ""


class JudgeClaim(BaseModel):
    claim: str
    verdict: Verdict = "cannot_be_confirmed"
    explanation: str = ""
    confidence: int = 50
    sources: list[JudgeSourceRef] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, v):
        return normalise_verdict(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        return clamp_pct(v)


class JudgeOutput(BaseModel):
    overall_summary: str = ""
    share_safe_reply: str = ""
    claims: list[JudgeClaim] = Field(default_factory=list)
    scam_signals: list[str] = Field(default_factory=list)
    ai_generation: AIGeneration = Field(default_factory=AIGeneration)


# ---- Auth ------------------------------------------------------------------

class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=256)


class LoginIn(BaseModel):
    email: str
    password: str
    remember: bool = True


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=6, max_length=256)


class DemoSSOIn(BaseModel):
    provider: Literal["google", "github"]
