"""Step 4 - give each claim one of the 5 verdicts with a simple explanation and sources.

Claude is the primary judge; Gemini is the fallback; a rule-based answer is the last
resort so the API always returns the agreed schema."""
from __future__ import annotations

from datetime import date

import llm
from schemas import AIGeneration, ClaimResult, JudgeOutput, Source
from search import Evidence

LANG_NAME = {"en": "simple English", "hi": "simple Hindi (Devanagari script)", "mr": "simple Marathi (Devanagari script)"}

JUDGE_SYSTEM = """You are SachScan, an evidence-based fact-checker for WhatsApp forwards in India.
Your readers include elderly people and people not comfortable in English, so write short, plain sentences.
You never claim to be a perfect truth detector: you give evidence-backed assessments and use
"cannot_be_confirmed" honestly whenever the evidence is thin, missing or unreliable."""

JUDGE_PROMPT = """Today's date: {today}

ORIGINAL FORWARD:
<<<
{text}
>>>

EVIDENCE GATHERED FOR EACH CLAIM (source ids in square brackets):
{evidence}

For EACH claim, choose exactly one verdict:
- "verified": trusted sources confirm it.
- "false": trusted sources contradict it.
- "outdated": it was true once but newer sources supersede it (scheme ended, rule changed, old advice). Use source dates.
- "partly_supported": some parts check out, others don't or are exaggerated.
- "cannot_be_confirmed": not enough reliable evidence either way. Prefer this over guessing.

Rules:
- Base verdicts ONLY on the evidence above. Government sites, PIB Fact Check, WHO/ICMR, RBI and established fact-checkers (BOOM, Alt News, Factly) outrank random websites and social media.
- "explanation": 1-3 short sentences in {lang_name} saying WHAT is wrong or right and WHY, naming the source.
- "sources": cite only ids that appear in the evidence (e.g. "S1.2"); "quote" must be a short exact line from that source's text above that supports or contradicts the claim; "date" as YYYY-MM-DD if known else null.
- "confidence": 0-100, how sure you are of the verdict given the evidence.
- "overall_summary": one or two simple sentences in {lang_name} for the whole forward.
- "share_safe_reply": a polite 1-3 sentence message in {lang_name} the user can paste back into the family group (respectful, no blame).
- "scam_signals": short phrases in {lang_name} for manipulation tactics in the message (urgency, asks for OTP/payment, suspicious link, fear, forward pressure). Empty list if none.
- "ai_generation": stylometric hints that the TEXT may be machine-written (score 0-100, likely_ai, indicators in {lang_name}). This is a weak hint only — keep the score modest unless clear.
- Keep each "claim" string exactly as given.

Reply with ONLY this JSON:
{{"overall_summary": "...", "share_safe_reply": "...",
  "claims": [{{"claim": "...", "verdict": "...", "explanation": "...", "confidence": 0,
              "sources": [{{"id": "S1.1", "quote": "...", "date": null, "publisher": "..."}}]}}],
  "scam_signals": ["..."],
  "ai_generation": {{"score": 0, "likely_ai": false, "indicators": ["..."]}}}}"""


def format_evidence(evidence: list[Evidence]) -> tuple[str, dict[str, dict]]:
    lines, index = [], {}
    for i, ev in enumerate(evidence, 1):
        lines.append(f"\nCLAIM {i}: {ev.claim}")
        if ev.error and not ev.summary:
            lines.append("  (No evidence could be retrieved for this claim.)")
            continue
        lines.append(f"  Research notes: {ev.summary[:2500]}")
        for j, s in enumerate(ev.sources, 1):
            sid = f"S{i}.{j}"
            index[sid] = {"title": s.title, "url": s.url, "publisher": s.publisher, "date": s.date}
            lines.append(f"  [{sid}] {s.publisher} — {s.title} ({s.date or 'date unknown'}) {s.url}")
            if s.snippet:
                lines.append(f"       text: {s.snippet[:500]}")
    return "\n".join(lines), index


def _to_claims(out: JudgeOutput, evidence: list[Evidence], index: dict[str, dict]) -> list[ClaimResult]:
    results = []
    for i, ev in enumerate(evidence):
        jc = out.claims[i] if i < len(out.claims) else None
        # Match by text when the model reordered claims
        for c in out.claims:
            if c.claim.strip() == ev.claim.strip():
                jc = c
                break
        if jc is None:
            results.append(ClaimResult(claim=ev.claim, verdict="cannot_be_confirmed", confidence=30))
            continue
        sources = []
        for ref in jc.sources:
            meta = index.get(ref.id.strip().strip("[]"))
            if not meta:  # never surface a URL the evidence didn't contain
                continue
            sources.append(Source(title=meta["title"], publisher=ref.publisher or meta["publisher"], url=meta["url"], date=ref.date or meta["date"], quote=ref.quote))
        if not sources:  # fall back to the top retrieved sources without quotes
            sources = [Source(title=s.title, publisher=s.publisher, url=s.url, date=s.date, quote=s.snippet[:200]) for s in ev.sources[:2]]
        verdict, confidence = jc.verdict, jc.confidence
        if verdict in ("verified", "false", "outdated") and not ev.sources:
            verdict, confidence = "cannot_be_confirmed", min(confidence, 40)  # a firm verdict needs a source
        results.append(ClaimResult(claim=ev.claim, verdict=verdict, explanation=jc.explanation, confidence=confidence, sources=sources))
    return results


async def judge(text: str, evidence: list[Evidence], lang: str) -> tuple[JudgeOutput | None, list[ClaimResult], str]:
    ev_text, index = format_evidence(evidence)
    prompt = JUDGE_PROMPT.format(today=date.today().isoformat(), text=text[:6000], evidence=ev_text, lang_name=LANG_NAME.get(lang, LANG_NAME["en"]))
    attempt = await llm.json_with_fallback(prompt, JudgeOutput.model_validate, order=("claude", "gemini"), system=JUDGE_SYSTEM)
    if attempt.value is None:
        return None, [], "none"
    out: JudgeOutput = attempt.value
    out.ai_generation = AIGeneration(score=max(0, min(100, out.ai_generation.score)), likely_ai=out.ai_generation.likely_ai, indicators=out.ai_generation.indicators[:5])
    return out, _to_claims(out, evidence, index), attempt.provider


# ---------------------------------------------------------------------------
# Offline fallback text (used when every AI provider is unavailable)
# ---------------------------------------------------------------------------

OFFLINE = {
    "en": {
        "explanation": "We could not reach our evidence sources right now, so this claim cannot be confirmed yet. Please check PIB Fact Check (pib.gov.in/factcheck) or an official .gov.in site before believing or sharing it.",
        "summary_risky": "We could not verify these claims right now, and the message shows warning signs of a scam or misinformation. Do not click links or share personal details.",
        "summary": "We could not verify these claims right now. Treat the message with caution until an official source confirms it.",
        "reply": "I could not confirm this message from any official source. Let's not forward it until PIB Fact Check or an official website confirms it.",
        "no_claims": "This message does not contain a specific fact that can be checked.",
    },
    "hi": {
        "explanation": "अभी हम सबूत के स्रोतों तक नहीं पहुँच पाए, इसलिए इस दावे की पुष्टि नहीं हो सकती। भरोसा करने या शेयर करने से पहले PIB Fact Check (pib.gov.in/factcheck) या किसी आधिकारिक .gov.in वेबसाइट पर जाँच लें।",
        "summary_risky": "अभी इन दावों की पुष्टि नहीं हो सकी, और संदेश में धोखाधड़ी या गलत जानकारी के संकेत हैं। किसी लिंक पर क्लिक न करें और निजी जानकारी न दें।",
        "summary": "अभी इन दावों की पुष्टि नहीं हो सकी। जब तक कोई आधिकारिक स्रोत पुष्टि न करे, सावधान रहें।",
        "reply": "मुझे इस संदेश की पुष्टि किसी आधिकारिक स्रोत से नहीं मिली। जब तक PIB Fact Check या कोई सरकारी वेबसाइट पुष्टि न करे, इसे आगे न भेजें।",
        "no_claims": "इस संदेश में कोई ऐसा तथ्य नहीं है जिसकी जाँच की जा सके।",
    },
    "mr": {
        "explanation": "सध्या आम्ही पुराव्यांच्या स्रोतांपर्यंत पोहोचू शकलो नाही, त्यामुळे या दाव्याची पुष्टी होऊ शकत नाही. विश्वास ठेवण्यापूर्वी किंवा शेअर करण्यापूर्वी PIB Fact Check (pib.gov.in/factcheck) किंवा अधिकृत .gov.in वेबसाइटवर तपासा.",
        "summary_risky": "सध्या या दाव्यांची पुष्टी होऊ शकली नाही, आणि संदेशात फसवणूक किंवा चुकीच्या माहितीची चिन्हे आहेत. कोणत्याही लिंकवर क्लिक करू नका आणि वैयक्तिक माहिती देऊ नका.",
        "summary": "सध्या या दाव्यांची पुष्टी होऊ शकली नाही. अधिकृत स्रोत पुष्टी करेपर्यंत सावध रहा.",
        "reply": "या संदेशाची पुष्टी मला कोणत्याही अधिकृत स्रोताकडून मिळाली नाही. PIB Fact Check किंवा सरकारी वेबसाइट पुष्टी करेपर्यंत हा संदेश पुढे पाठवू नका.",
        "no_claims": "या संदेशात तपासता येईल अशी कोणतीही विशिष्ट माहिती नाही.",
    },
}


def offline_claims(evidence: list[Evidence], lang: str) -> list[ClaimResult]:
    t = OFFLINE.get(lang, OFFLINE["en"])
    out = []
    for ev in evidence:
        sources = [Source(title=s.title, publisher=s.publisher, url=s.url, date=s.date, quote=s.snippet[:200]) for s in ev.sources[:3]]
        out.append(ClaimResult(claim=ev.claim, verdict="cannot_be_confirmed", explanation=t["explanation"], confidence=20, sources=sources))
    return out

