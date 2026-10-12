"""Step 3 - gather evidence for each claim from trusted sources.

Providers (SEARCH_PROVIDER in .env picks the first one; the rest are fallbacks):
  gemini      Gemini + Google Search grounding (free tier)
  perplexity  Perplexity Sonar API (paid)
  claude      Claude with the server-side web_search tool
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import httpx

import config
import llm
from llm import LLMUnavailable

log = logging.getLogger("sachscan.search")

TRUSTED_HINT = {
    "health": "WHO (who.int), MoHFW (mohfw.gov.in), ICMR (icmr.gov.in), PIB Fact Check, BOOM, Alt News, Factly",
    "scheme": "PIB Fact Check (pib.gov.in), myScheme (myscheme.gov.in), india.gov.in, the relevant ministry's *.gov.in site, BOOM, Alt News, Factly",
    "finance": "RBI (rbi.org.in), SEBI, NPCI, PIB Fact Check, Ministry of Finance, BOOM, Factly",
    "news": "PIB Fact Check, major Indian and international news agencies (PTI, Reuters, AP), BOOM, Alt News, Factly",
}
DEFAULT_HINT = "PIB Fact Check, *.gov.in, *.nic.in, WHO, ICMR, RBI, BOOM, Alt News, Factly, Reuters, PTI"

TRUSTED_DOMAINS = (
    "pib.gov.in", "gov.in", "nic.in", "myscheme.gov.in", "mohfw.gov.in", "icmr.gov.in", "rbi.org.in", "who.int",
    "boomlive.in", "altnews.in", "factly.in", "reuters.com", "apnews.com", "ptinews.com", "indiatoday.in",
    "thehindu.com", "indianexpress.com", "bbc.com", "sebi.gov.in", "npci.org.in", "imd.gov.in", "ndma.gov.in",
)

SEARCH_PROMPT = """You are researching ONE claim from a viral WhatsApp forward for a fact-checker in India. Today is {today}.

Claim: "{claim}"
{context}
Search the web. Prefer these reliable sources: {hint}.
Report concisely in English:
1. What reliable sources say about this exact claim (confirm, contradict, or partly).
2. Whether the information is outdated (e.g. a scheme ended, a rule changed) — give publication dates.
3. Exact short quotes from the sources that support or contradict the claim, each with the source name and date.
If you find nothing reliable, say so plainly. Do not guess."""


@dataclass
class EvidenceSource:
    title: str
    url: str
    publisher: str = ""
    date: Optional[str] = None
    snippet: str = ""


@dataclass
class Evidence:
    claim: str
    summary: str = ""
    sources: list[EvidenceSource] = field(default_factory=list)
    provider: str = "none"
    error: str = ""


def publisher_of(url: str, title: str = "") -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if "vertexaisearch" in host and title:
        return title  # Gemini grounding titles are the source domain
    return host or title


def trusted_score(url: str) -> int:
    host = (urlparse(url).hostname or "").lower()
    return 1 if any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS) else 0


# ---------------------------------------------------------------------------
# Gemini grounding
# ---------------------------------------------------------------------------

async def _resolve_redirect(client: httpx.AsyncClient, url: str) -> str:
    """Gemini grounding returns vertexaisearch redirect links; resolve to the real page."""
    if "vertexaisearch.cloud.google.com" not in url:
        return url
    try:
        r = await client.head(url, follow_redirects=False)
        loc = r.headers.get("location")
        return loc or url
    except httpx.HTTPError:
        return url


async def search_gemini(claim: str, prompt: str) -> Evidence:
    resp = await llm.gemini_generate(prompt, google_search=True)
    ev = Evidence(claim=claim, summary=(resp.text or "").strip(), provider="gemini")
    try:
        gm = resp.candidates[0].grounding_metadata
    except (AttributeError, IndexError, TypeError):
        gm = None
    chunks = list(getattr(gm, "grounding_chunks", None) or [])
    snippets: dict[int, list[str]] = {}
    for sup in getattr(gm, "grounding_supports", None) or []:
        seg = getattr(getattr(sup, "segment", None), "text", "") or ""
        for idx in getattr(sup, "grounding_chunk_indices", None) or []:
            snippets.setdefault(idx, []).append(seg)
    async with httpx.AsyncClient(timeout=5) as client:
        urls = await asyncio.gather(*[_resolve_redirect(client, getattr(getattr(c, "web", None), "uri", "") or "") for c in chunks])
    for i, (c, url) in enumerate(zip(chunks, urls)):
        web = getattr(c, "web", None)
        if not web or not url:
            continue
        title = getattr(web, "title", "") or ""
        ev.sources.append(EvidenceSource(title=title, url=url, publisher=publisher_of(url, title), snippet=" … ".join(snippets.get(i, []))[:600]))
    return ev


# ---------------------------------------------------------------------------
# Perplexity
# ---------------------------------------------------------------------------

async def search_perplexity(claim: str, prompt: str) -> Evidence:
    if not config.PERPLEXITY_API_KEY:
        raise LLMUnavailable("PERPLEXITY_API_KEY not set")
    try:
        async with httpx.AsyncClient(timeout=config.STEP_TIMEOUT_S) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"},
                json={"model": config.PERPLEXITY_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            )
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"Perplexity connection error: {e}") from e
    if r.status_code != 200:
        raise LLMUnavailable(f"Perplexity error {r.status_code}")
    data = r.json()
    ev = Evidence(claim=claim, summary=data["choices"][0]["message"]["content"], provider="perplexity")
    results = data.get("search_results") or [{"url": u} for u in data.get("citations", [])]
    for sr in results:
        url = sr.get("url", "")
        if url:
            ev.sources.append(EvidenceSource(title=sr.get("title") or publisher_of(url), url=url, publisher=publisher_of(url), date=sr.get("date"), snippet=sr.get("snippet", "")[:600]))
    return ev


# ---------------------------------------------------------------------------
# Claude web_search
# ---------------------------------------------------------------------------

async def search_claude(claim: str, prompt: str) -> Evidence:
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 4, "user_location": {"type": "approximate", "country": "IN"}}]
    _, blocks = await llm.claude_message(prompt, tools=tools, max_tokens=8000)
    ev = Evidence(claim=claim, summary=llm.claude_text(blocks).strip(), provider="claude")
    seen: dict[str, EvidenceSource] = {}
    for b in blocks:
        btype = getattr(b, "type", "")
        if btype == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
            for r in b.content:
                url = getattr(r, "url", "")
                if url and url not in seen:
                    seen[url] = EvidenceSource(title=getattr(r, "title", "") or publisher_of(url), url=url, publisher=publisher_of(url), date=getattr(r, "page_age", None))
        elif btype == "text":
            for c in getattr(b, "citations", None) or []:
                url = getattr(c, "url", "")
                if url:
                    src = seen.setdefault(url, EvidenceSource(title=getattr(c, "title", "") or publisher_of(url), url=url, publisher=publisher_of(url)))
                    cited = getattr(c, "cited_text", "") or ""
                    if cited and cited not in src.snippet:
                        src.snippet = (src.snippet + " … " + cited).strip(" …")[:600]
    ev.sources = list(seen.values())
    return ev


# ---------------------------------------------------------------------------
# Free web search (no key): Google News RSS search + Factly's public search API,
# then reading the fact-check pages for quotes and dates.
# ---------------------------------------------------------------------------

_FREE_SEM = asyncio.Semaphore(3)
_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[\wऀ-ॿ]{3,}", re.U)
STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "has", "have", "will", "all", "under",
    "says", "said", "fact", "check", "who", "what", "when", "click", "before", "after", "your", "you", "its",
    "है", "हैं", "और", "के", "की", "का", "को", "से", "में", "ने", "पर", "आहे", "आणि", "च्या", "ला", "हे",
}
UA = {"User-Agent": "SachScanBot/1.0 (fact-checking assistant; +https://github.com/Bhavesh1424/SachScan)"}


def _clean_html(s: str) -> str:
    import html as html_mod

    return " ".join(_TAG.sub(" ", html_mod.unescape(html_mod.unescape(s))).split())


def _keywords(text: str) -> list[str]:
    seen, out = set(), []
    for w in _WORD.findall(text):
        lw = w.lower()
        if lw not in STOPWORDS and lw not in seen:
            seen.add(lw)
            out.append(w)
    return out


def _tag(item: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
    return _clean_html(m.group(1)) if m else ""


def _rss_date(raw: str) -> Optional[str]:
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return None


async def google_news(query: str, lang: str, limit: int = 6) -> list[EvidenceSource]:
    hl, ceid = ("hi", "IN:hi") if lang in ("hi", "mr") else ("en-IN", "IN:en")
    async with _FREE_SEM:
        try:
            async with httpx.AsyncClient(timeout=12, headers=UA, follow_redirects=True) as client:
                r = await client.get("https://news.google.com/rss/search", params={"q": query, "hl": hl, "gl": "IN", "ceid": ceid})
        except httpx.HTTPError as e:
            log.warning("Google News RSS failed: %s", e)
            return []
    if r.status_code != 200:
        return []
    out = []
    for item in _ITEM.findall(r.text)[:limit]:
        title, link = _tag(item, "title"), _tag(item, "link")
        src = re.search(r'<source url="([^"]+)">(.*?)</source>', item, re.S)
        publisher = _clean_html(src.group(2)) if src else publisher_of(link)
        site = src.group(1) if src else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        out.append(EvidenceSource(title=title, url=link, publisher=publisher, date=_rss_date(_tag(item, "pubDate")), snippet=f"Headline: {title}"))
        out[-1]._site = site  # real domain, used for trust ranking
    return out


async def factly_search(terms: list[str], limit: int = 3) -> list[EvidenceSource]:
    """Factly (IFCN-certified Indian fact-checker) exposes the standard WordPress search API."""
    for n in (4, 2):  # WordPress matches every term, so retry with fewer
        if len(terms) < n and n != 2:
            continue
        q = " ".join(terms[:n])
        if not q:
            return []
        async with _FREE_SEM:
            try:
                async with httpx.AsyncClient(timeout=12, headers=UA, follow_redirects=True) as client:
                    r = await client.get("https://factly.in/wp-json/wp/v2/search", params={"search": q, "per_page": limit})
                data = r.json() if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                return []
        if data:
            return [EvidenceSource(title=_clean_html(d.get("title", "")), url=d.get("url", ""), publisher="factly.in") for d in data if d.get("url")]
    return []


def _best_sentences(page_text: str, claim: str, n: int = 2) -> str:
    kws = {k.lower() for k in _keywords(claim)}
    if not kws:
        return ""
    sentences = re.split(r"(?<=[.!?।])\s+", page_text)
    scored = sorted(((len(kws & {k.lower() for k in _keywords(s)}), s.strip()) for s in sentences if 30 < len(s) < 500), key=lambda x: -x[0])
    return " … ".join(s for score, s in scored[:n] if score >= 2)


async def _read_page(url: str, claim: str) -> tuple[str, Optional[str]]:
    """Fetch a result page (SSRF-safe) and return (most relevant sentences, publication date)."""
    from extract import fetch_url  # local import avoids a cycle at module load

    try:
        data, ctype, final = await asyncio.wait_for(fetch_url(url), timeout=8)
    except Exception:
        return "", None
    if "html" not in ctype:
        return "", None

    def parse():
        import trafilatura

        decoded = data.decode("utf-8", errors="replace")
        text = trafilatura.extract(decoded, url=final, favor_recall=True) or ""
        meta = trafilatura.extract_metadata(decoded)
        return _best_sentences(text, claim), (getattr(meta, "date", None) if meta else None)

    try:
        return await asyncio.to_thread(parse)
    except Exception:
        return "", None


def _trust(src: EvidenceSource) -> int:
    return trusted_score(getattr(src, "_site", "") or src.url) * 2 + (1 if "fact" in (src.title + src.publisher).lower() else 0)


async def search_web(claim: str, prompt: str, lang: str = "en") -> Evidence:
    terms = _keywords(claim)
    short = " ".join(terms[:8])
    news_fc, news, factly = await asyncio.gather(
        google_news(f"{short} fact check", lang), google_news(short, lang), factly_search(terms)
    )
    seen: dict[str, EvidenceSource] = {}
    for src in factly + news_fc + news:
        key = src.url if src.publisher == "factly.in" else (src.publisher + src.title).lower()
        if src.url and key not in seen:
            seen[key] = src
    if not seen:
        raise LLMUnavailable("web search returned no results")
    sources = sorted(seen.values(), key=lambda s: -_trust(s))[:6]
    # Read fact-check pages we can fetch directly (Google News links are redirects to JS pages)
    readable = [s for s in sources if "news.google.com" not in s.url][:3]
    pages = await asyncio.gather(*[_read_page(s.url, claim) for s in readable])
    for src, (sentences, page_date) in zip(readable, pages):
        if sentences:
            src.snippet = f"{src.title}. {sentences}"[:800]
        if page_date:
            src.date = page_date[:10]
    for src in sources:
        if not src.snippet:
            src.snippet = f"Headline: {src.title}"
    lines = [f"- {s.publisher} ({s.date or 'date unknown'}): {s.snippet[:300]}" for s in sources]
    summary = "Web/news search results (judge from the headlines and quoted texts):" + chr(10) + chr(10).join(lines)
    return Evidence(claim=claim, summary=summary, sources=sources, provider="web")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

PROVIDERS = {
    "gemini": (llm.gemini_search_available, search_gemini),
    "perplexity": (lambda: bool(config.PERPLEXITY_API_KEY), search_perplexity),
    "claude": (llm.claude_available, search_claude),
    "web": (lambda: True, search_web),  # free fallback, always last
}


def provider_order() -> list[str]:
    first = config.SEARCH_PROVIDER if config.SEARCH_PROVIDER in PROVIDERS else "gemini"
    return [first] + [p for p in ("gemini", "claude", "perplexity", "web") if p != first]


async def search_claim(claim: str, topic: str, context: str = "", lang: str = "en") -> Evidence:
    prompt = SEARCH_PROMPT.format(
        today=date.today().isoformat(), claim=claim.replace('"', "'"),
        hint=TRUSTED_HINT.get(topic, DEFAULT_HINT),
        context=f"Context (the full forward, for reference only): {context[:600]}\n" if context else "",
    )
    errors = []
    for name in provider_order():
        available, fn = PROVIDERS[name]
        if not available():
            continue
        try:
            call = fn(claim, prompt, lang) if name == "web" else fn(claim, prompt)
            ev = await asyncio.wait_for(call, timeout=config.STEP_TIMEOUT_S + 5)
            if name != "web":  # web results are already ranked
                ev.sources.sort(key=lambda s: -trusted_score(s.url))
            ev.sources = ev.sources[:8]
            return ev
        except (LLMUnavailable, asyncio.TimeoutError) as e:
            log.warning("search provider %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
        except Exception as e:  # never let one provider crash the request
            log.exception("search provider %s crashed", name)
            errors.append(f"{name}: {e}")
    return Evidence(claim=claim, error="; ".join(errors) or "no search provider configured")


BATCH_PROMPT = """You are researching claims from ONE viral WhatsApp forward for a fact-checker in India. Today is {today}.

{claims}

Search the web for EACH claim. Prefer these reliable sources: {hint}.
Write one section per claim, in this exact format, in English:

CLAIM 1:
- What reliable sources say about this claim (confirm, contradict, or partly), naming each source and its publication date.
- Whether it is outdated (e.g. a scheme ended, a rule changed).
- Short exact quotes from the sources.

CLAIM 2:
...

If you find nothing reliable for a claim, say so plainly in its section. Do not guess."""

_SECTION = re.compile(r"(?im)^\W*CLAIM\s*(\d+)\s*[:.)-]")


async def search_gemini_batch(claims: list[str], topic: str) -> list[Evidence]:
    """One grounded Gemini call for all claims (free tier allows only a few requests per minute).
    Each source is assigned to the claim whose section cites it."""
    prompt = BATCH_PROMPT.format(
        today=date.today().isoformat(),
        claims="\n".join(f'CLAIM {i}: "{c}"' for i, c in enumerate(claims, 1)),
        hint=TRUSTED_HINT.get(topic, DEFAULT_HINT),
    )
    resp = await llm.gemini_generate(prompt, google_search=True)
    text = resp.text or ""
    # Split the answer into per-claim sections
    marks = [(m.start(), int(m.group(1))) for m in _SECTION.finditer(text)]
    sections: dict[int, tuple[int, int]] = {}
    for k, (start, n) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        if 1 <= n <= len(claims) and n not in sections:
            sections[n] = (start, end)

    def section_of(pos: int) -> int:
        for n, (s, e) in sections.items():
            if s <= pos < e:
                return n
        return 1 if len(claims) == 1 else 0

    try:
        gm = resp.candidates[0].grounding_metadata
    except (AttributeError, IndexError, TypeError):
        gm = None
    chunks = list(getattr(gm, "grounding_chunks", None) or [])
    async with httpx.AsyncClient(timeout=5) as client:
        urls = await asyncio.gather(*[_resolve_redirect(client, getattr(getattr(c, "web", None), "uri", "") or "") for c in chunks])

    per_claim: dict[int, dict[int, list[str]]] = {n: {} for n in range(1, len(claims) + 1)}
    for sup in getattr(gm, "grounding_supports", None) or []:
        seg = getattr(sup, "segment", None)
        seg_text = getattr(seg, "text", "") or ""
        pos = text.find(seg_text) if seg_text else -1
        n = section_of(pos) if pos >= 0 else 0
        targets = [n] if n else list(per_claim)  # unplaced support: share with every claim
        for idx in getattr(sup, "grounding_chunk_indices", None) or []:
            for t in targets:
                per_claim[t].setdefault(idx, []).append(seg_text)

    results = []
    for i, claim in enumerate(claims, 1):
        s, e = sections.get(i, (0, len(text)) if len(claims) == 1 or not sections else (0, 0))
        ev = Evidence(claim=claim, summary=text[s:e].strip() or text.strip(), provider="gemini")
        for idx, snippets in per_claim[i].items():
            if idx >= len(chunks):
                continue
            web, url = getattr(chunks[idx], "web", None), urls[idx]
            if not web or not url:
                continue
            title = getattr(web, "title", "") or ""
            ev.sources.append(EvidenceSource(title=title, url=url, publisher=publisher_of(url, title), snippet=" … ".join(snippets)[:600]))
        ev.sources.sort(key=lambda src: -trusted_score(src.url))
        ev.sources = ev.sources[:8]
        results.append(ev)
    return results


async def search_all(claims: list[str], topic: str, context: str, lang: str = "en") -> list[Evidence]:
    if provider_order()[0] == "gemini" and llm.gemini_search_available():
        try:
            return await asyncio.wait_for(search_gemini_batch(claims, topic), timeout=config.STEP_TIMEOUT_S + 10)
        except (LLMUnavailable, asyncio.TimeoutError) as e:
            log.warning("batched Gemini search failed, falling back per claim: %s", e)
        except Exception:
            log.exception("batched Gemini search crashed, falling back per claim")
    return list(await asyncio.gather(*[search_claim(c, topic, context, lang) for c in claims]))
