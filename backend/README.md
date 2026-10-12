# SachScan backend ("Is This Forward True?")

FastAPI backend for the SachScan PWA. It checks WhatsApp forwards (text, links, screenshots,
voice notes, PDFs and videos) against trusted sources. Each claim gets one of 5 verdicts, with
evidence, in the user's language (English, Hindi or Marathi).

**No frontend file is modified.** This server serves the existing frontend with the same
routes and cache headers as `server.js`. At serve time it adds one script tag
(`/sachscan-bridge.js`) to each HTML page, and that script connects the existing UI to the API.

## Run it

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then add GEMINI_API_KEY and/or ANTHROPIC_API_KEY
uvicorn main:app --host 0.0.0.0 --port 3000
```

Open http://localhost:3000. Stop `node server.js` first, or choose another `--port`.
For the phone demo, expose the server over HTTPS (needed for the PWA share target):
`ngrok http 3000` or `cloudflared tunnel --url http://localhost:3000`. Put that URL in
`PUBLIC_BASE_URL`.

Without any API keys the app still runs in **offline mode**: scam red flags, language detection
and claim splitting work, and every claim is marked `cannot_be_confirmed`, with an honest
notice in the UI.

## Pipeline

```
Extract  -> Gemini multimodal (image/audio/video/PDF), Claude fallback for image/PDF,
            pypdf for text PDFs, trafilatura for URLs (SSRF-protected fetch)
Split    -> Gemini -> Claude -> sentence-split fallback (language: en | hi | mr)
Search   -> SEARCH_PROVIDER=gemini|perplexity|claude, others used automatically as fallbacks
            (Gemini Google Search grounding / Perplexity Sonar / Claude web_search)
Judge    -> Claude -> Gemini -> offline fallback; strict JSON validated with Pydantic, retried once
```

Safety rules in code: only source URLs that were actually retrieved can appear in a verdict.
A firm verdict (verified, false or outdated) with no retrieved source is downgraded to
`cannot_be_confirmed`. User content is never written to disk; results are cached in memory
for 30 minutes (`RESULT_CACHE_TTL_S=0` turns this off).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/check` (alias `/api/check`) | multipart `text` / `url` / `file`, or JSON `{"text"}` / `{"url"}`. Returns the CheckResponse schema |
| POST | `/api/check/stream` | Same input; NDJSON events `progress`, `claims`, `result`, `error` (drives the UI steps) |
| POST | `/share-target` | Web Share Target (WhatsApp → Share → SachScan); redirects to `/help.html?shared=…` |
| GET/POST | `/api/shared/{id}`, `/api/shared/{id}/check/stream` | One-shot shared item (deleted after processing, 10 min TTL) |
| POST | `/api/auth/register`, `/login`, `/logout`, `/forgot`, `/reset`, `/demo-sso` | Accounts (PBKDF2 passwords, hashed session tokens) |
| GET | `/api/auth/me` · POST `/api/auth/api-key` | Profile and quota · rotate developer API key |
| GET | `/api/auth/oauth/{google,github}/start` | Real OAuth when client IDs are set |
| POST | `/api/integrations/telegram/webhook`, `/telegram/setup` | Telegram bot |
| POST | `/api/integrations/discord/interactions` | Discord `/check` command and the "Check with SachScan" message command |
| GET | `/api/integrations`, `/api/health`, `/api/config` | Status |

Interactive docs: http://localhost:3000/api/docs

The response follows the schema in CLAUDE.md (`input_type`, `language`, `extracted_text`,
`overall_summary`, `claims[]`, `scam_signals`) and adds these fields for the UI:
`overall_verdict`, `confidence`, `share_safe_reply`, `ai_generation`, `sources_checked`, `meta`.

Developer API: signed-in users get a key (`sach_live_sec_…`). Send it as the `X-API-Key` header.
Signed-in users have 500 scans a month. Anonymous use is rate-limited per IP
(`ANON_CHECKS_PER_HOUR`).

## What the bridge connects (frontend unchanged)

- **help.html**: Verify button, Enter-to-send and the sample buttons call `/api/check/stream`,
  with live progress in the existing typing indicator. Results show one card per claim, with
  color-coded verdict chips, sources, quotes, scam red flags and a share-safe reply. You can
  paste or drag a screenshot, voice note or PDF into the chat box. WhatsApp shares arrive
  here as well.
- **login.html**: sign-in, registration, Google/GitHub sign-in (demo accounts until OAuth keys
  are set), forgot/reset password, sign-out, and the real scan quota and API-key hint.
- **manifest.json**: served with a `share_target`, so the installed PWA appears in Android's
  share sheet.

## Messaging bots (optional)

**Telegram**
1. Create a bot with @BotFather and put the token in `TELEGRAM_BOT_TOKEN`.
2. Set `PUBLIC_BASE_URL` (HTTPS) and optionally `TELEGRAM_WEBHOOK_SECRET`.
3. On the server machine, run `curl -X POST http://localhost:3000/api/integrations/telegram/setup`.

**Discord**
1. Set `DISCORD_PUBLIC_KEY` and `DISCORD_APPLICATION_ID`.
2. Set the Interactions Endpoint URL to `<PUBLIC_BASE_URL>/api/integrations/discord/interactions`.
3. Register a `check` slash command with a `text` string option, and a message command named
   "Check with SachScan".

WhatsApp works through the PWA share target. The paid WhatsApp Business API tipline is future
scope, as the project plan says.

## Tests

```bash
.venv\Scripts\python -m pytest tests -q
```

These run offline, with no keys. Sample forwards in EN, HI and MR are in `tests/forwards.json`.
