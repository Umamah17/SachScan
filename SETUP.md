# SachScan setup

Everything secret lives in `backend/.env` (never committed). `backend/.env.example` lists every variable.

Run the app (from the repo root, Windows):

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 3001 --reload --reload-dir backend
```

or double-click `backend\start-server.bat`. Open **http://localhost:3001** (not `0.0.0.0`).

First-time Python setup: `python -m venv backend\.venv` then `backend\.venv\Scripts\pip install -r backend\requirements.txt`.

---

## 1. Supabase (database, login, file storage)

1. Create a project at [supabase.com](https://supabase.com) (region: Mumbai).
2. **Project Settings → API Keys** and fill `backend/.env`:
   - `SUPABASE_URL` = the project URL (`https://<ref>.supabase.co`)
   - `SUPABASE_ANON_KEY` = the **Publishable** key (`sb_publishable_…`, or the legacy `anon` key). Public by design.
   - `SUPABASE_SERVICE_ROLE_KEY` = the **Secret** key (`sb_secret_…`, or legacy `service_role`). **Server only, never share it.**
3. **SQL Editor → New query**: paste every file in `supabase/migrations/` (in name order) and **Run**.
   This creates the tables, Row Level Security policies and the private `uploads` bucket.
4. Restart the server. `GET /health` should show `"database": true`.

## 2. Google login (via Supabase Auth)

Login only asks Google for `openid email profile`. Gmail access is a separate, optional step (section 8).

1. [Google Cloud Console](https://console.cloud.google.com) → create a project (e.g. "SachScan").
2. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, app name "SachScan", support email = yours.
   - Scopes: `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` only.
   - Leave publishing status on **Testing** and add your team's Gmail addresses under **Test users**
     (up to 100). Google verification is not needed in Testing mode.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - **Authorized JavaScript origins**: `http://localhost:3001` (and your production / ngrok URL)
   - **Authorized redirect URIs**: `https://<your-project-ref>.supabase.co/auth/v1/callback`
     (copy the exact value from Supabase → Authentication → Sign In / Providers → Google)
   - Copy the **Client ID** and **Client secret**.
4. Supabase → **Authentication → Sign In / Providers → Google** → enable, paste Client ID + secret, save.
5. Supabase → **Authentication → URL Configuration**:
   - **Site URL**: `http://localhost:3001` (change to your production URL when you deploy)
   - **Redirect URLs** (allow list), one per line:
     - `http://localhost:3001/**`
     - `https://<your-ngrok-or-production-domain>/**`
6. Open http://localhost:3001/help.html → you are sent to the login page → **Continue with Google**.

The browser keeps the Supabase session; every API call sends `Authorization: Bearer <access token>`, which the
backend verifies (`backend/api/deps.py`). On a user's first request a `profiles` row is created.

## 3. ngrok (public HTTPS URL for webhooks)

Meta, Telegram and Google push messages to your server, so it needs a public HTTPS address.

1. Sign up at [ngrok.com](https://ngrok.com) → **Your Authtoken** → copy it.
2. Install ngrok (Windows: `winget install ngrok.ngrok`, or download the zip) and run once:
   `ngrok config add-authtoken <your-token>`
3. Claim your **free static domain** (ngrok dashboard → Domains). It stays the same across restarts, so you
   only configure webhooks once.
4. With the backend running on port 3001:
   ```powershell
   ngrok http --domain=<your-static-domain> 3001
   ```
5. Put `PUBLIC_BASE_URL=https://<your-static-domain>.ngrok-free.app` in `backend/.env` and restart the backend.
   Also add `https://<your-static-domain>.ngrok-free.app/**` to Supabase → Authentication → URL Configuration →
   Redirect URLs, and the same origin to the Google OAuth client's JavaScript origins, if you'll open the app
   through ngrok (e.g. on a phone).

## 4. WhatsApp bot (official WhatsApp Business Cloud API)

Users forward messages **to** the bot number and it replies. Nobody's personal chats are read (WhatsApp is
end-to-end encrypted), and no unofficial libraries are used: they break WhatsApp's terms and get numbers banned.

1. [developers.facebook.com](https://developers.facebook.com) → **Get Started** (register as a developer).
2. **My Apps → Create app** → use case **Other** → type **Business** (or "Connect with customers through WhatsApp")
   → name `SachScan` → create.
3. In the app dashboard add **WhatsApp** → **Set up** (let it create a business portfolio if asked).
4. **WhatsApp → API Setup**:
   - Meta gives you a free **test phone number**. During testing it can only message **pre-registered recipient
     numbers**: under **To → Manage phone number list**, add your team's WhatsApp numbers (max 5) and confirm the
     codes they receive.
   - Copy into `backend/.env`:
     - `WHATSAPP_PHONE_NUMBER_ID` = **Phone number ID** (a long number, not the phone number itself)
     - `WHATSAPP_TOKEN` = **Temporary access token** (expires after 24 h; see step 7 for a permanent one)
     - `WHATSAPP_BOT_NUMBER` = the test number, digits only (e.g. `15551234567`)
5. **App settings → Basic → App secret → Show** → `WHATSAPP_APP_SECRET`. The backend rejects any webhook whose
   `X-Hub-Signature-256` doesn't match this secret.
6. Start the backend and ngrok, then **WhatsApp → Configuration → Webhook → Edit**:
   - **Callback URL**: `https://<your-ngrok-domain>/webhooks/whatsapp`
   - **Verify token**: the value of `WHATSAPP_VERIFY_TOKEN` in `backend/.env`
   - **Verify and save**, then under **Webhook fields** subscribe to **messages**.
7. Permanent token (so the demo doesn't break after 24 h): **business.facebook.com → Settings → Users → System
   users → Add** (Admin) → **Add assets** → your app (full control) → **Generate new token** for the app with
   `whatsapp_business_messaging` and `whatsapp_business_management` → put it in `WHATSAPP_TOKEN`.
8. Put the bot number in `integrations.config.js` (`WHATSAPP_BOT_NUMBER`) so the website card opens
   `https://wa.me/<number>?text=help`.
9. From a registered phone, send **help** to the bot, then forward any message, screenshot, voice note or PDF.

Notes
- Webhooks are answered with 200 immediately and processed in the background; retries are de-duplicated by
  message id. Each check gets exactly **one** reply message (service messages are billed per message).
- Commands: `help`, `link <code>` (code from the website: History → Link a chat app).
- Limits: images / voice notes / videos up to 16 MB, PDFs up to 10 MB, 20 checks per hour per number.

## 5. Discord bot (discord.py, separate process)

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application** → name `SachScan`.
2. **General Information** → copy **Application ID** → `DISCORD_APP_ID` in `backend/.env` **and** `DISCORD_APP_ID` in
   `integrations.config.js` (the website card builds the invite link from it).
3. **Bot** → **Reset Token** → copy → `DISCORD_BOT_TOKEN` in `backend/.env` (secret; never commit or share).
4. **Bot → Privileged Gateway Intents** → turn on **Message Content Intent** → Save. It's needed to read message text for
   the right-click command and the opt-in AutoMod Sentinel. (Once a bot is in 100+ servers Discord must approve this
   intent; not an issue for a hackathon.)
5. Invite the bot with the website's Discord card, or this URL (scopes `bot applications.commands`; permissions: View
   Channels, Send Messages, Send Messages in Threads, Create Public Threads, Embed Links, Read Message History):
   `https://discord.com/oauth2/authorize?client_id=<DISCORD_APP_ID>&scope=bot%20applications.commands&permissions=309237730304`
6. Run it (from the repo root) and keep the window open:
   ```powershell
   backend\.venv\Scripts\python.exe -m bots.discord_bot
   ```
   or double-click `backend\start-discord-bot.bat`. Commands appear instantly in every server the bot is in.

Using it
- `/check text:<…>` or `/check attachment:<file>`: the reply is visible only to you; add `public:True` to post it.
- Right-click a message → **Apps** → **Check with SachScan**.
- `/sachscan enable #channel` (members with **Manage Server** only): the bot checks new messages with links or
  attachments there and replies **in a thread only** when something is false, outdated or a scam. It never deletes.
  `/sachscan disable [#channel]`, `/sachscan status`. Settings are stored in `integration_settings`.
- `/link <code>` connects a Discord account to the website history; DMs to the bot work like the WhatsApp bot.

---

Sections for Telegram, Gmail/Pub-Sub, the Chrome extension and the demo script are added as those
integrations are built.
