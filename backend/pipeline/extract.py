"""Step 1 - turn any input (text / URL / image / audio / PDF / video) into plain text.

Nothing is written to disk except transiently by the Gemini Files API for very large
media, which llm.gemini_generate deletes immediately after use.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import mimetypes
import re
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from backend import config
from backend.pipeline import llm
from backend.pipeline.llm import LLMUnavailable, MediaPart

log = logging.getLogger("sachscan.extract")

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
BARE_URL_RE = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)


class ExtractionError(ValueError):
    """User-facing extraction problem (bad file, unreachable URL, ...)."""


@dataclass
class Extracted:
    input_type: str  # text | image | audio | pdf | url | video
    text: str
    source_url: Optional[str] = None
    provider: str = "local"


EXTRACT_PROMPT = (
    "You are the text-extraction step of a fact-checking app for WhatsApp forwards.\n"
    "Transcribe ALL readable text / speech in this {kind} exactly, in its original language and script "
    "(English, Hindi or Marathi; do not translate). If it is a screenshot of a chat, include only the "
    "forwarded message content, not UI chrome like timestamps or battery icons. If there is little or no "
    "text, describe in one or two sentences what the image/video shows and any claim it implies.\n"
    "Return only the extracted text, with no preamble."
)


def classify_mime(mime: str, filename: str = "") -> str:
    mime = (mime or "").lower()
    if not mime or mime == "application/octet-stream":
        mime = (mimetypes.guess_type(filename)[0] or "").lower()
    if filename.lower().endswith((".opus", ".ogg")):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        return "pdf"
    if mime.startswith("text/"):
        return "text"
    raise ExtractionError(f"Unsupported file type: {mime or filename or 'unknown'}")


def normalise_mime(mime: str, filename: str, kind: str) -> str:
    mime = (mime or "").lower()
    if not mime or mime == "application/octet-stream":
        mime = (mimetypes.guess_type(filename)[0] or "").lower()
    if kind == "audio" and (not mime.startswith("audio/") or filename.lower().endswith((".opus", ".ogg"))):
        # WhatsApp voice notes are Opus-in-Ogg
        mime = "audio/ogg"
    if kind == "pdf":
        mime = "application/pdf"
    return mime or "application/octet-stream"


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[: config.MAX_TEXT_CHARS]


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?") for u in URL_RE.findall(text or "")]


def looks_like_only_url(text: str) -> Optional[str]:
    t = (text or "").strip()
    if " " in t or "\n" in t:
        return None
    if URL_RE.fullmatch(t):
        return t
    if BARE_URL_RE.fullmatch(t):
        return "https://" + t
    return None


# ---------------------------------------------------------------------------
# Media via multimodal LLMs
# ---------------------------------------------------------------------------

async def _llm_transcribe(data: bytes, mime: str, kind: str) -> tuple[str, str]:
    prompt = EXTRACT_PROMPT.format(kind={"audio": "voice note / audio", "pdf": "PDF document"}.get(kind, kind))
    errors = []
    if llm.gemini_available():  # Gemini handles image, audio, video and PDF
        try:
            resp = await llm.gemini_generate(prompt, [MediaPart(data, mime)])
            if (resp.text or "").strip():
                return resp.text, "gemini"
        except LLMUnavailable as e:
            errors.append(str(e))
    if llm.openrouter_available() and kind in ("image", "pdf"):
        try:
            msg = await llm.openrouter_chat(prompt, [MediaPart(data, mime)])
            if (msg.get("content") or "").strip():
                return msg["content"], "openrouter"
        except LLMUnavailable as e:
            errors.append(str(e))
    if llm.claude_available() and kind in ("image", "pdf"):
        try:
            content = llm.claude_media_blocks([MediaPart(data, mime)]) + [{"type": "text", "text": prompt}]
            _, blocks = await llm.claude_message(content)
            text = llm.claude_text(blocks)
            if text.strip():
                return text, "claude"
        except LLMUnavailable as e:
            errors.append(str(e))
    if kind in ("audio", "video") and not llm.gemini_available():
        raise ExtractionError("Voice notes and videos need a Gemini key on the server. Please type or paste the message instead.")
    if not (llm.gemini_available() or llm.claude_available() or llm.openrouter_available()):
        raise ExtractionError("Reading images, voice notes and scanned PDFs needs an AI key on the server. Please paste the text instead.")
    raise ExtractionError("Could not read this file right now. Please try again or paste the text. " + "; ".join(errors)[:300])


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ExtractionError("This PDF is password protected.")
        pages = [p.extract_text() or "" for p in reader.pages[:30]]
        return "\n".join(pages)
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Could not open PDF: {e}") from e


async def extract_file(data: bytes, mime: str, filename: str = "") -> Extracted:
    if not data:
        raise ExtractionError("The uploaded file is empty.")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ExtractionError(f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    kind = classify_mime(mime, filename)
    mime = normalise_mime(mime, filename, kind)

    if kind == "text":
        return Extracted("text", clean_text(data.decode("utf-8", errors="replace")))

    if kind == "pdf":
        text = await asyncio.to_thread(_pdf_text, data)
        if len(text.strip()) >= 80:
            return Extracted("pdf", clean_text(text), provider="pypdf")
        # Scanned PDF: fall through to multimodal OCR
    text, provider = await _llm_transcribe(data, mime, kind)
    return Extracted(kind, clean_text(text), provider=provider)


# ---------------------------------------------------------------------------
# URLs (SSRF-safe fetch + trafilatura)
# ---------------------------------------------------------------------------

def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ExtractionError("Only http(s) links can be checked.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ExtractionError("That website could not be found.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ExtractionError("That link points to a private network address and cannot be fetched.")


async def fetch_url(url: str) -> tuple[bytes, str, str]:
    """Fetch with manual redirect handling so every hop is checked against private IPs."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36 SachScanBot/1.0",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8,mr;q=0.7",
    }
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers=headers) as client:
        for _ in range(5):
            await asyncio.to_thread(_assert_public_host, url)
            async with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("location", ""))
                    continue
                if resp.status_code >= 400:
                    raise ExtractionError(f"The website returned an error ({resp.status_code}).")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ExtractionError("That page is too large to check.")
                    chunks.append(chunk)
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return b"".join(chunks), ctype, str(resp.url)
    raise ExtractionError("Too many redirects.")


def _html_to_text(html: bytes, url: str) -> str:
    import trafilatura

    decoded = html.decode("utf-8", errors="replace")
    text = trafilatura.extract(decoded, url=url, include_comments=False, include_tables=False, favor_recall=True) or ""
    meta = trafilatura.extract_metadata(decoded)
    header = []
    if meta is not None:
        if getattr(meta, "title", None):
            header.append(meta.title)
        if getattr(meta, "date", None):
            header.append(f"Published: {meta.date}")
        if getattr(meta, "sitename", None):
            header.append(f"Site: {meta.sitename}")
    return "\n".join(header + [text]).strip()


async def extract_url(url: str) -> Extracted:
    try:
        data, ctype, final_url = await fetch_url(url)
    except ExtractionError:
        raise
    except httpx.HTTPError as e:
        raise ExtractionError("Could not open that link. Is it correct and publicly reachable?") from e

    if ctype.startswith(("image/", "audio/", "video/")) or ctype == "application/pdf":
        inner = await extract_file(data, ctype, urlparse(final_url).path)
        return Extracted("url", inner.text, source_url=final_url, provider=inner.provider)

    text = await asyncio.to_thread(_html_to_text, data, final_url)
    if len(text) < 40:
        raise ExtractionError("Could not find readable article text at that link. Try pasting the message text instead.")
    return Extracted("url", clean_text(f"[Source link: {final_url}]\n{text}"), source_url=final_url, provider="trafilatura")


async def extract_text_or_url(text: str) -> Extracted:
    url = looks_like_only_url(text)
    if url:
        return await extract_url(url)
    return Extracted("text", clean_text(text))
