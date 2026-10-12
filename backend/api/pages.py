"""Trust pages linked from the site footers (Privacy, Terms, Security, Responsible Disclosure).

Content describes what this backend actually does; keep it in sync with the code."""
from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from backend import config

router = APIRouter(tags=["pages"])

_CONTACT = config.CONTACT_EMAIL or "the SachScan team (contact address not yet published)"

PAGES: dict[str, tuple[str, str, list[tuple[str, list[str]]]]] = {
    "privacy": (
        "Privacy Guarantee",
        "What happens to the messages you check.",
        [
            ("Your messages are not stored", [
                "Text, links, screenshots, voice notes and PDFs you submit are processed in memory and are never written to our database or disk.",
                "To answer repeat checks quickly, a finished result is kept in server memory for up to 30 minutes and then discarded. Restarting the server clears it.",
                "Items shared from WhatsApp through the app's share sheet are held in memory for at most 10 minutes and deleted as soon as they are checked.",
            ]),
            ("AI and search providers", [
                "To check a message, its text (or file) is sent to the AI providers configured on the server: Google Gemini and/or Anthropic Claude, and optionally Perplexity.",
                "Claims (not your whole message) are searched on Google News and Factly to find evidence. Those services receive only the search terms.",
                "Each provider handles data under its own privacy terms.",
            ]),
            ("Accounts", [
                "If you create an account we store your name, email, a salted PBKDF2 hash of your password (never the password itself), your monthly scan count and a hashed API key.",
                "Sign-in tokens are random and stored only as hashes. Signing out deletes the session on the server.",
                "Google / GitHub sign-in, when enabled, only reads your name and verified email address.",
            ]),
            ("Your choices", [
                f"To delete your account and its data, contact {_CONTACT}.",
                "You can use the checker without an account.",
            ]),
        ],
    ),
    "terms": (
        "Terms of Service",
        "Please read before relying on a verdict.",
        [
            ("What SachScan is", [
                "SachScan gives evidence-based assessments of claims in forwarded messages. It is not a perfect truth detector.",
                "Verdicts (verified, false, outdated, partly supported, cannot be confirmed) reflect the sources we could find at the time of the check. When evidence is thin we say \"cannot be confirmed\" instead of guessing.",
                "The AI-generation score is a writing-style hint only, never proof of who wrote a message.",
            ]),
            ("Your responsibilities", [
                "Check important health, money or legal decisions with an official source or a professional.",
                "Do not submit other people's private information, passwords, OTPs or bank details.",
                "Do not use the service to harass people or to automate large volumes of requests beyond your quota.",
            ]),
            ("Free tier", [
                f"Registered users get {config.FREE_MONTHLY_SCANS} scans per calendar month. Anonymous use is rate-limited per network.",
                "The service is provided as-is, without warranties, and may change or be unavailable at times.",
            ]),
        ],
    ),
    "security": (
        "Security Architecture",
        "How the platform protects you and your data.",
        [
            ("Data handling", [
                "No user content is persisted; results live only in a short-lived in-memory cache.",
                "API keys for AI providers stay on the server in environment variables and are never sent to the browser.",
            ]),
            ("Accounts and access", [
                "Passwords are hashed with PBKDF2-HMAC-SHA256 (240,000 iterations, per-user salt).",
                "Session tokens and developer API keys are random and stored only as SHA-256 hashes.",
                "Login, registration and password-reset endpoints are rate-limited. Reset links expire after one hour and sign out all sessions.",
            ]),
            ("Fetching links safely", [
                "When you check a URL, the server refuses private, loopback and link-local addresses on every redirect hop (SSRF protection), and limits download size.",
                "Sources shown in a verdict can only be links that were actually retrieved during the search, so the AI cannot invent citations.",
            ]),
            ("Bots and webhooks", [
                "Discord requests are verified with Ed25519 signatures; Telegram webhooks can require a secret token.",
            ]),
        ],
    ),
    "disclosure": (
        "Responsible Disclosure",
        "Found a security issue? Thank you for helping.",
        [
            ("How to report", [
                f"Email {_CONTACT} with a description, steps to reproduce and the impact you observed.",
                "Please give us reasonable time to fix the issue before sharing it publicly.",
            ]),
            ("Please don't", [
                "Access, change or delete other users' data, degrade the service for others, or use social engineering against users or the team.",
            ]),
            ("Scope", [
                "The SachScan web app, its API (/api/*), the share target and the messaging bot webhooks.",
            ]),
        ],
    ),
}

ALIASES = {
    "privacy-policy": "privacy", "terms-of-service": "terms", "security-and-trust": "security",
    "vulnerability-disclosure": "disclosure",
}


def render(slug: str) -> str:
    title, lead, sections = PAGES[slug]
    body = "".join(
        f'<section class="mb-8"><h2 class="font-bold text-lg text-black mb-3">{html.escape(h)}</h2>'
        f'<ul class="space-y-2 list-disc pl-5 text-sm text-neutral-700 leading-relaxed">'
        + "".join(f"<li>{html.escape(p)}</li>" for p in points)
        + "</ul></section>"
        for h, points in sections
    )
    nav = " · ".join(
        f'<a class="hover:text-black {"text-black font-semibold" if s == slug else ""}" href="/legal/{s}">{html.escape(PAGES[s][0])}</a>' for s in PAGES
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)} — SachScan</title>
<link rel="icon" href="/icons/icon-32.png"/>
<link rel="manifest" href="/manifest.json"/>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;600;700&family=Inter:wght@400;500&display=swap" rel="stylesheet"/>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#eaeaea}} h1,h2{{font-family:Geist,Inter,sans-serif}}</style>
</head>
<body class="min-h-screen text-black">
<header class="w-full border-b border-black/10 bg-white/70 backdrop-blur">
  <div class="max-w-3xl mx-auto px-5 py-4 flex items-center justify-between">
    <a href="/index.html" class="flex items-center gap-2 font-bold"><img src="/download__4_-removebg-preview.png" alt="" class="h-7 w-auto"/>SachScan</a>
    <a href="/help.html" class="text-xs font-semibold px-3 py-1.5 rounded-full bg-black text-white hover:bg-neutral-800">Check a message</a>
  </div>
</header>
<main class="max-w-3xl mx-auto px-5 py-10">
  <p class="text-[11px] uppercase tracking-widest text-neutral-500 mb-2">Trust &amp; Verification</p>
  <h1 class="text-3xl sm:text-4xl font-bold tracking-tight mb-2">{html.escape(title)}</h1>
  <p class="text-neutral-600 mb-8">{html.escape(lead)}</p>
  <div class="p-6 sm:p-8 rounded-2xl bg-white border border-black/10 shadow-sm">{body}</div>
  <p class="text-xs text-neutral-500 mt-6">{nav}</p>
</main>
</body>
</html>"""


@router.get("/legal/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def legal_page(slug: str):
    slug = ALIASES.get(slug, slug)
    if slug not in PAGES:
        raise HTTPException(404, "Page not found")
    return HTMLResponse(render(slug), headers={"Cache-Control": "no-cache"})
