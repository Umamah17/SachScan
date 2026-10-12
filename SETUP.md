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

---

Sections for WhatsApp, Discord, Telegram, Gmail/Pub-Sub, ngrok, the Chrome extension and the demo script are added
as those integrations are built.
