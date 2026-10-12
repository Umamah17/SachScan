"""Thin async wrappers around Gemini and Claude with JSON helpers.

Every caller goes through these so provider fallback, timeouts and JSON parsing
live in one place. A provider whose key is missing simply reports unavailable.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import config

log = logging.getLogger("sachscan.llm")


class LLMUnavailable(RuntimeError):
    """Raised when a provider is not configured or failed; callers fall back."""


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_loose(text: str) -> Any:
    """Parse JSON from an LLM reply that may include fences or prose around it."""
    if not text:
        raise ValueError("empty response")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Outermost {...} block
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found in response")


# ---------------------------------------------------------------------------
# Media parts (provider-neutral)
# ---------------------------------------------------------------------------

@dataclass
class MediaPart:
    data: bytes
    mime_type: str


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

_gemini_client = None


def gemini_available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _gemini():
    global _gemini_client
    if not gemini_available():
        raise LLMUnavailable("GEMINI_API_KEY not set")
    if _gemini_client is None:
        from google import genai

        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


_cooldown: dict[tuple[str, str], float] = {}


def gemini_models() -> list[str]:
    models = [config.GEMINI_MODEL] + config.GEMINI_FALLBACK_MODELS
    return list(dict.fromkeys(m for m in models if m))


def _cooling_down(model: str, kind: str) -> bool:
    import time

    return _cooldown.get((model, kind), 0) > time.time()


def _cool(model: str, kind: str, seconds: float) -> None:
    import time

    _cooldown[(model, kind)] = time.time() + seconds


def gemini_search_available() -> bool:
    """False once every model's Google Search grounding quota is exhausted (skips straight to web search)."""
    return gemini_available() and any(not _cooling_down(m, "search") for m in gemini_models())


async def gemini_generate(
    prompt: str,
    media: Optional[list[MediaPart]] = None,
    *,
    json_mode: bool = False,
    google_search: bool = False,
    system: Optional[str] = None,
    timeout: Optional[float] = None,
    thinking: bool = False,
):
    """Return the raw Gemini response object (callers read .text / grounding)."""
    from google.genai import types

    client = _gemini()
    contents: list[Any] = []
    for part in media or []:
        if len(part.data) <= 18 * 1024 * 1024:
            contents.append(types.Part.from_bytes(data=part.data, mime_type=part.mime_type))
        else:
            # Large media goes through the Files API; deleted right after use.
            import io

            uploaded = await client.aio.files.upload(
                file=io.BytesIO(part.data), config=types.UploadFileConfig(mime_type=part.mime_type)
            )
            for _ in range(30):
                if getattr(uploaded.state, "name", str(uploaded.state)) != "PROCESSING":
                    break
                await asyncio.sleep(1)
                uploaded = await client.aio.files.get(name=uploaded.name)
            contents.append(uploaded)
    contents.append(prompt)

    cfg: dict[str, Any] = {"temperature": 0.2}
    if system:
        cfg["system_instruction"] = system
    if google_search:
        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    elif json_mode:
        cfg["response_mime_type"] = "application/json"

    # Try each model in turn. Free-tier keys hit per-minute / per-day limits quickly, so a model
    # that returns 429 (or is unavailable to this key) is skipped for a cool-down period.
    kind = "search" if google_search else "plain"
    last_error: Exception | None = None
    tried = False
    try:
        for model in gemini_models():
            if _cooling_down(model, kind):
                continue
            tried = True
            model_cfg = dict(cfg)
            if not thinking and "2.5-flash" in model:
                # Thinking adds ~10 s; extraction / JSON / search steps don't need it.
                model_cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            try:
                return await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model, contents=contents, config=types.GenerateContentConfig(**model_cfg)
                    ),
                    timeout=timeout or config.STEP_TIMEOUT_S,
                )
            except asyncio.TimeoutError as e:
                raise LLMUnavailable("Gemini timed out") from e
            except Exception as e:  # SDK raises several error types; all mean "fall back"
                last_error = e
                text = str(e)
                if "429" in text or "RESOURCE_EXHAUSTED" in text:
                    per_day = "PerDay" in text
                    _cool(model, kind, 3600 if per_day or google_search else 60)
                    log.warning("Gemini %s (%s) rate-limited%s; trying next model", model, kind, " for today" if per_day else "")
                    continue
                if "404" in text or "NOT_FOUND" in text or "not supported" in text.lower():
                    _cool(model, kind, 24 * 3600)
                    log.warning("Gemini %s unavailable for this key; trying next model", model)
                    continue
                raise LLMUnavailable(f"Gemini error: {text[:300]}") from e
        if not tried:
            raise LLMUnavailable(f"All Gemini models are rate-limited ({kind}); waiting for quota to reset")
        raise LLMUnavailable(f"Gemini rate limit reached on all models: {str(last_error)[:200]}")
    finally:
        for c in contents:
            name = getattr(c, "name", None)
            if name and str(name).startswith("files/"):
                try:
                    await client.aio.files.delete(name=name)
                except Exception:
                    pass


async def gemini_json(prompt: str, media=None, *, system=None, timeout=None) -> Any:
    resp = await gemini_generate(prompt, media, json_mode=True, system=system, timeout=timeout)
    return parse_json_loose(resp.text or "")


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

_claude_client = None


def claude_available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def _claude():
    global _claude_client
    if not claude_available():
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    if _claude_client is None:
        import anthropic

        _claude_client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=2)
    return _claude_client


_fallback_rejected = False  # set once if the API rejects the fallback beta


async def claude_message(
    content: list[dict] | str,
    *,
    system: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    max_tokens: int = 16000,
    timeout: Optional[float] = None,
):
    """Send one Claude request, resuming server-tool `pause_turn`s. Returns the final message
    plus every content block seen across continuations."""
    import anthropic

    global _fallback_rejected
    client = _claude()
    messages: list[dict] = [{"role": "user", "content": content}]
    all_blocks: list[Any] = []

    async def _call():
        kwargs: dict[str, Any] = dict(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=messages,
        )
        # Passed through extra_body so older SDK versions without typed params still work.
        extra: dict[str, Any] = {"output_config": {"effort": config.CLAUDE_EFFORT}}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if config.CLAUDE_REFUSAL_FALLBACK and not _fallback_rejected:
            extra["fallbacks"] = "default"
            return await client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], extra_body=extra, **kwargs
            )
        return await client.messages.create(extra_body=extra, **kwargs)

    for _ in range(4):  # initial call + up to 3 pause_turn continuations
        try:
            try:
                msg = await asyncio.wait_for(_call(), timeout=timeout or config.STEP_TIMEOUT_S)
            except anthropic.BadRequestError:
                if config.CLAUDE_REFUSAL_FALLBACK and not _fallback_rejected:
                    _fallback_rejected = True
                    log.info("Claude refusal-fallback beta rejected; continuing without it")
                    msg = await asyncio.wait_for(_call(), timeout=timeout or config.STEP_TIMEOUT_S)
                else:
                    raise
        except asyncio.TimeoutError as e:
            raise LLMUnavailable("Claude timed out") from e
        except anthropic.RateLimitError as e:
            raise LLMUnavailable("Claude rate limited") from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailable("Claude connection error") from e

        all_blocks.extend(msg.content)
        if msg.stop_reason == "refusal":
            raise LLMUnavailable("Claude declined the request")
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        return msg, all_blocks
    return msg, all_blocks


def claude_text(blocks: list[Any]) -> str:
    return "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")


def claude_media_blocks(media: Optional[list[MediaPart]]) -> list[dict]:
    """Claude accepts images and PDFs (not audio/video)."""
    out: list[dict] = []
    for part in media or []:
        b64 = base64.standard_b64encode(part.data).decode()
        if part.mime_type == "application/pdf":
            out.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        elif part.mime_type in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            out.append({"type": "image", "source": {"type": "base64", "media_type": part.mime_type, "data": b64}})
        else:
            raise LLMUnavailable(f"Claude cannot read {part.mime_type}")
    return out


async def claude_json(prompt: str, media=None, *, system=None, timeout=None) -> Any:
    content = claude_media_blocks(media) + [{"type": "text", "text": prompt}]
    _, blocks = await claude_message(content, system=system, timeout=timeout)
    return parse_json_loose(claude_text(blocks))


# ---------------------------------------------------------------------------
# Generic "ask for JSON with fallback + one retry" helper
# ---------------------------------------------------------------------------

@dataclass
class JSONAttempt:
    value: Any = None
    provider: str = ""
    errors: list[str] = field(default_factory=list)


async def json_with_fallback(prompt: str, validate, *, order=("gemini", "claude"), media=None, system=None) -> JSONAttempt:
    """Try providers in order. Each provider gets one retry on invalid JSON / schema."""
    result = JSONAttempt()
    funcs = {"gemini": (gemini_available, gemini_json), "claude": (claude_available, claude_json)}
    for name in order:
        available, fn = funcs[name]
        if not available():
            continue
        for attempt in range(2):
            p = prompt if attempt == 0 else prompt + "\n\nYour previous reply was not valid JSON for the schema. Reply with ONLY the JSON object."
            try:
                raw = await fn(p, media, system=system)
                result.value = validate(raw)
                result.provider = name
                return result
            except LLMUnavailable as e:
                result.errors.append(f"{name}: {e}")
                break  # provider down: move on, don't retry
            except Exception as e:  # invalid JSON or schema -> retry once
                result.errors.append(f"{name} (attempt {attempt + 1}): {e}")
    return result
