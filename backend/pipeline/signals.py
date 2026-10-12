"""Offline heuristics that never need an API: language detection, scam signals and
AI-style hints. They run on every request so the demo always has something useful,
and they back up the LLM when providers are down."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from backend.pipeline.extract import find_urls

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
MARATHI_MARKERS = ("आहे", "नाही", "आणि", "च्या", "झाले", "करा", "होते", "आहेत", "तुम्ही", "आपल्या", "मध्ये", "साठी", "ला ", "ळ")
HINDI_MARKERS = ("है", "नहीं", "और", "के ", "की ", "में", "हैं", "था", "आप", "करें", "लिए", "से ")


def detect_language(text: str) -> str:
    """en / hi / mr by script + common function words."""
    if not text:
        return "en"
    dev = len(DEVANAGARI.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if dev < max(10, latin * 0.3):
        return "en"
    mr = sum(text.count(m) for m in MARATHI_MARKERS)
    hi = sum(text.count(m) for m in HINDI_MARKERS)
    return "mr" if mr > hi else "hi"


# ---------------------------------------------------------------------------
# Scam signals
# ---------------------------------------------------------------------------

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "is.gd", "rb.gy", "shorturl.at", "tiny.cc", "ow.ly", "rebrand.ly", "t.ly", "s.id"}
SUSPICIOUS_TLDS = (".top", ".xyz", ".vip", ".club", ".online", ".site", ".buzz", ".icu", ".live", ".shop", ".click", ".info", ".work", ".rest", ".cfd", ".sbs")
GOV_LIKE = re.compile(r"(gov|govt|pm|yojana|scheme|rbi|sbi|aadhaar|uidai|income.?tax|epfo|kisan)", re.I)

PATTERNS = {
    "urgency": re.compile(r"\b(urgent|immediately|hurry|last date|today only|before \d|within \d+ ?(hours?|hrs|minutes?)|expires?|deadline|act now|limited time)\b|तुरंत|जल्दी|आज ही|अंतिम तिथि|लगेच|त्वरित|लवकर|आजच", re.I),
    "forward_pressure": re.compile(r"\b(forward|share) (this|to|it|with)\b|forward (to|this) .*(groups?|\d+ (people|contacts))|share before|ज़्यादा से ज़्यादा|सबको भेजें|फॉरवर्ड करें|शेअर करा|पुढे पाठवा|सर्वांना पाठवा", re.I),
    "credentials": re.compile(r"\b(otp|pin|cvv|password|passcode|upi pin|card number|account number|kyc|aadhaa?r number)\b|ओटीपी|पासवर्ड|केवाईसी", re.I),
    "money_bait": re.compile(r"(₹|rs\.?|inr|rupees|\$)\s?\d[\d,]*|\b(free|win|won|prize|lottery|cash ?back|reward|gift|grant|lakh|crore|bonus|jackpot)\b|मुफ्त|फ्री|इनाम|लॉटरी|मोफत|बक्षीस|लाख|करोड़|कोटी", re.I),
    "fear": re.compile(r"\b(blocked|suspended|deactivated|arrest|penalty|fine|legal action|account will be closed)\b|बंद हो जाएगा|ब्लॉक|गिरफ्तार|बंद होईल", re.I),
    "miracle_health": re.compile(r"\b(cures?|100% (cure|effective)|miracle|no side effects?|doctors (hate|don'?t want)|home remedy)\b|रामबाण|चमत्कार|इलाज|उपाय", re.I),
}

LABELS = {
    "en": {
        "urgency": "Creates urgency or a deadline to make you act without checking",
        "forward_pressure": "Pressures you to forward it to many people",
        "credentials": "Asks for OTP, PIN, password, KYC or bank details",
        "money_bait": "Promises money, prizes or free benefits",
        "fear": "Threatens account blocking, fines or legal action",
        "miracle_health": "Makes miracle-cure style health promises",
        "shortener": "Uses a shortened link that hides where it really goes",
        "suspicious_link": "Link domain is not an official government or known site",
        "fake_gov_link": "Claims to be official but the link is not a .gov.in / .nic.in site",
        "all_caps": "Excessive capital letters or exclamation marks",
    },
    "hi": {
        "urgency": "जल्दबाज़ी या समय-सीमा का दबाव बनाता है ताकि आप बिना जाँचे कदम उठाएँ",
        "forward_pressure": "इसे बहुत लोगों को फॉरवर्ड करने का दबाव डालता है",
        "credentials": "OTP, PIN, पासवर्ड, KYC या बैंक जानकारी माँगता है",
        "money_bait": "पैसे, इनाम या मुफ्त फायदे का वादा करता है",
        "fear": "खाता बंद होने, जुर्माने या कानूनी कार्रवाई का डर दिखाता है",
        "miracle_health": "चमत्कारी इलाज जैसे स्वास्थ्य दावे करता है",
        "shortener": "छोटा (शॉर्ट) लिंक इस्तेमाल करता है जो असली वेबसाइट छिपाता है",
        "suspicious_link": "लिंक किसी आधिकारिक सरकारी या जानी-मानी वेबसाइट का नहीं है",
        "fake_gov_link": "सरकारी होने का दावा करता है, पर लिंक .gov.in / .nic.in का नहीं है",
        "all_caps": "बहुत ज़्यादा बड़े अक्षर या विस्मयादिबोधक चिह्न",
    },
    "mr": {
        "urgency": "घाई किंवा मुदतीचा दबाव निर्माण करतो जेणेकरून तुम्ही तपासल्याशिवाय कृती कराल",
        "forward_pressure": "अनेक लोकांना फॉरवर्ड करण्याचा दबाव टाकतो",
        "credentials": "OTP, PIN, पासवर्ड, KYC किंवा बँक तपशील मागतो",
        "money_bait": "पैसे, बक्षीस किंवा मोफत लाभाचे आमिष दाखवतो",
        "fear": "खाते बंद होणे, दंड किंवा कायदेशीर कारवाईची भीती दाखवतो",
        "miracle_health": "चमत्कारिक उपचारासारखे आरोग्य दावे करतो",
        "shortener": "लहान (शॉर्ट) लिंक वापरतो जी खरी वेबसाइट लपवते",
        "suspicious_link": "लिंक अधिकृत सरकारी किंवा ओळखीच्या वेबसाइटची नाही",
        "fake_gov_link": "सरकारी असल्याचा दावा करतो, पण लिंक .gov.in / .nic.in ची नाही",
        "all_caps": "खूप जास्त मोठी अक्षरे किंवा उद्गारचिन्हे",
    },
}


def _host(url: str) -> str:
    try:
        return (urlparse(url if "://" in url else "https://" + url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def scam_signal_keys(text: str) -> list[str]:
    keys = [k for k, rx in PATTERNS.items() if rx.search(text or "")]
    urls = find_urls(text) + re.findall(r"\b(?:www\.)?[a-z0-9-]+\.(?:com|in|org|net|top|xyz|vip|club|online|site|ly|gl|co|info|live|shop|click)/\S+", text or "", re.I)
    for u in urls:
        host = _host(u)
        if not host:
            continue
        if host in SHORTENERS:
            keys.append("shortener")
        elif host.endswith(SUSPICIOUS_TLDS):
            keys.append("suspicious_link")
        if GOV_LIKE.search(text or "") and not host.endswith((".gov.in", ".nic.in", ".gov", "gov.in", "rbi.org.in")) and host not in SHORTENERS:
            keys.append("fake_gov_link")
    letters = re.findall(r"[A-Za-z]", text or "")
    if (letters and sum(c.isupper() for c in letters) / len(letters) > 0.5 and len(letters) > 30) or (text or "").count("!") >= 4:
        keys.append("all_caps")
    return list(dict.fromkeys(keys))


def scam_signals(text: str, lang: str) -> list[str]:
    labels = LABELS.get(lang, LABELS["en"])
    return [labels[k] for k in scam_signal_keys(text)]


# ---------------------------------------------------------------------------
# AI-style hints (stylometry only; never treated as proof)
# ---------------------------------------------------------------------------

AI_PHRASES = re.compile(r"\b(in today'?s (fast-paced|digital) world|it is important to note|delve|furthermore|moreover|in conclusion|a testament to|navigating the|ever-evolving|game-?changer|unlock(ing)? the)\b", re.I)


def ai_style(text: str) -> dict:
    indicators: list[str] = []
    score = 10
    if AI_PHRASES.search(text or ""):
        score += 25
        indicators.append("Uses stock phrasing common in machine-written text")
    if re.search(r"\b(undisclosed|unnamed|sources say|insiders?|a senior official)\b", text or "", re.I):
        score += 20
        indicators.append("Attributes a claim to unnamed or undisclosed sources")
    if not re.search(r"\b(19|20)\d{2}\b|\d{1,2}[/-]\d{1,2}", text or "") and len(text or "") > 120:
        score += 10
        indicators.append("Makes specific claims without any date")
    if not find_urls(text) and re.search(r"\b(ministry|department|government|govt|official)\b|मंत्रालय|सरकार|शासन", text or "", re.I):
        score += 10
        indicators.append("Mentions an official body but cites no circular, link or reference number")
    sentences = [s for s in re.split(r"[.!?।]\s", text or "") if s.strip()]
    if len(sentences) >= 4:
        lens = [len(s.split()) for s in sentences]
        mean = sum(lens) / len(lens)
        var = sum((l - mean) ** 2 for l in lens) / len(lens)
        if mean > 8 and var < 12:
            score += 10
            indicators.append("Very uniform sentence lengths")
    score = min(score, 95)
    if not indicators:
        indicators.append("No strong machine-writing markers found in the wording")
    return {"score": score, "likely_ai": score >= 55, "indicators": indicators}
