"""Short text reply for chat platforms (WhatsApp, Telegram, Discord, Gmail summaries).

Overall verdict line, up to 3 claims with emoji + explanation + one source link,
then a link to the full result on the web app."""
from __future__ import annotations

from typing import Optional

from backend.pipeline.schemas import CheckResponse

EMOJI = {"verified": "✅", "false": "❌", "outdated": "🕰️", "partly_supported": "🔷", "cannot_be_confirmed": "❔"}
LABEL = {
    "en": {"verified": "Verified", "false": "False", "outdated": "Outdated", "partly_supported": "Partly supported", "cannot_be_confirmed": "Cannot be confirmed"},
    "hi": {"verified": "सही पाया गया", "false": "गलत", "outdated": "पुरानी जानकारी", "partly_supported": "आंशिक रूप से सही", "cannot_be_confirmed": "पुष्टि नहीं हो सकी"},
    "mr": {"verified": "खरे आढळले", "false": "खोटे", "outdated": "जुनी माहिती", "partly_supported": "अंशतः खरे", "cannot_be_confirmed": "पुष्टी होऊ शकली नाही"},
}
FULL_RESULT = {"en": "Full result", "hi": "पूरा नतीजा", "mr": "संपूर्ण निकाल"}
WARNING = {"en": "Warning signs", "hi": "चेतावनी के संकेत", "mr": "धोक्याची चिन्हे"}


def format_chat(result: CheckResponse, full_url: Optional[str] = None, *, limit: int = 4000, bold: str = "*") -> str:
    """`bold` is the platform's bold marker: "*" for WhatsApp/Telegram, "**" for Discord, "" for plain text."""
    lang = result.language if result.language in LABEL else "en"
    labels = LABEL[lang]
    v = result.overall_verdict
    lines = [f"{EMOJI[v]} {bold}{labels[v].upper()}{bold}", result.overall_summary.strip(), ""]
    for i, c in enumerate(result.claims[:3], 1):
        lines.append(f"{i}. {EMOJI[c.verdict]} {bold}{labels[c.verdict]}{bold}: {c.claim}")
        if c.explanation:
            lines.append(f"   {c.explanation}")
        src = next((s for s in c.sources if s.url), None)
        if src:
            lines.append(f"   🔗 {src.publisher or src.title}{' (' + src.date + ')' if src.date else ''}: {src.url}")
        lines.append("")
    if len(result.claims) > 3:
        lines.append(f"(+{len(result.claims) - 3} more)")
    if result.scam_signals:
        lines.append(f"⚠️ {WARNING[lang]}: " + "; ".join(result.scam_signals[:3]))
        lines.append("")
    if full_url:
        lines.append(f"📄 {FULL_RESULT[lang]}: {full_url}")
    text = "\n".join(lines).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
