-- SachScan core schema: profiles, chat logs, platform account links, result cache, bot settings.
--
-- Security model
--   * Row Level Security is ON for every table.
--   * Signed-in users (the `authenticated` role, via the web app) can only see and change
--     their own profile, conversations, messages and platform links.
--   * Bots, webhooks and the result cache are written server-side with the service-role key,
--     which bypasses RLS. That key must never reach the browser.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- profiles: one row per auth user, upserted on first login
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text,
  name        text,
  avatar_url  text,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- conversations / messages: every exchange from every channel
-- ---------------------------------------------------------------------------
create table if not exists public.conversations (
  id                uuid primary key default gen_random_uuid(),
  -- null for messages from platform users who have not linked an account yet
  user_id           uuid references auth.users (id) on delete cascade,
  platform          text not null check (platform in ('web', 'whatsapp', 'discord', 'telegram', 'gmail', 'extension')),
  -- the sender's platform id (phone number, Discord user id, ...); lets unlinked chats be attached later
  external_user_id  text,
  title             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists conversations_user_updated_idx on public.conversations (user_id, updated_at desc);
create index if not exists conversations_platform_ext_idx on public.conversations (platform, external_user_id);

create table if not exists public.messages (
  id                   uuid primary key default gen_random_uuid(),
  conversation_id      uuid not null references public.conversations (id) on delete cascade,
  role                 text not null check (role in ('user', 'assistant')),
  content_type         text not null default 'text' check (content_type in ('text', 'image', 'audio', 'pdf', 'url', 'video')),
  text                 text,
  file_path            text,          -- object path in the private `uploads` bucket (deleted after 24 h)
  result_json          jsonb,         -- the CheckResult, on assistant messages
  platform_message_id  text,          -- e.g. WhatsApp wamid, "<chat_id>:<message_id>" for Telegram
  created_at           timestamptz not null default now()
);
create index if not exists messages_conversation_idx on public.messages (conversation_id, created_at);
-- De-duplicates webhook retries: the same inbound platform message is stored (and checked) once.
create unique index if not exists messages_platform_dedupe_idx
  on public.messages (platform_message_id) where platform_message_id is not null and role = 'user';

-- Keep conversations.updated_at fresh so History is ordered by latest activity.
create or replace function public.touch_conversation() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  update public.conversations set updated_at = now() where id = new.conversation_id;
  return new;
end $$;
drop trigger if exists messages_touch_conversation on public.messages;
create trigger messages_touch_conversation after insert on public.messages
  for each row execute function public.touch_conversation();

-- ---------------------------------------------------------------------------
-- platform_links: WhatsApp number / Discord id / Telegram id  ->  web account
-- ---------------------------------------------------------------------------
create table if not exists public.platform_links (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users (id) on delete cascade,
  platform          text not null check (platform in ('whatsapp', 'discord', 'telegram')),
  external_user_id  text not null,
  linked_at         timestamptz not null default now(),
  unique (platform, external_user_id)
);

-- One-time codes from POST /link/start; the user sends "link <code>" to a bot.
create table if not exists public.link_codes (
  code        text primary key,
  user_id     uuid not null references auth.users (id) on delete cascade,
  expires_at  timestamptz not null
);

-- ---------------------------------------------------------------------------
-- checks_cache: instant answers for repeated forwards (server-side only)
-- ---------------------------------------------------------------------------
create table if not exists public.checks_cache (
  content_hash  text primary key,
  result_json   jsonb not null,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- integration_settings: opt-in auto-scan per Discord guild channel / Telegram group
-- ---------------------------------------------------------------------------
create table if not exists public.integration_settings (
  id                 uuid primary key default gen_random_uuid(),
  platform           text not null check (platform in ('discord', 'telegram', 'gmail')),
  external_space_id  text not null,      -- Discord guild id, Telegram group id, ...
  settings           jsonb not null default '{}'::jsonb,
  enabled_by         text,               -- platform id of the admin who opted in
  created_at         timestamptz not null default now(),
  unique (platform, external_space_id)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.profiles             enable row level security;
alter table public.conversations        enable row level security;
alter table public.messages             enable row level security;
alter table public.platform_links       enable row level security;
alter table public.link_codes           enable row level security;
alter table public.checks_cache         enable row level security;
alter table public.integration_settings enable row level security;
-- link_codes, checks_cache, integration_settings: no policies => service role only.

drop policy if exists "own profile read" on public.profiles;
create policy "own profile read"   on public.profiles for select to authenticated using (id = auth.uid());
drop policy if exists "own profile insert" on public.profiles;
create policy "own profile insert" on public.profiles for insert to authenticated with check (id = auth.uid());
drop policy if exists "own profile update" on public.profiles;
create policy "own profile update" on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

drop policy if exists "own conversations read" on public.conversations;
create policy "own conversations read"   on public.conversations for select to authenticated using (user_id = auth.uid());
drop policy if exists "own conversations insert" on public.conversations;
create policy "own conversations insert" on public.conversations for insert to authenticated with check (user_id = auth.uid());
drop policy if exists "own conversations update" on public.conversations;
create policy "own conversations update" on public.conversations for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "own conversations delete" on public.conversations;
create policy "own conversations delete" on public.conversations for delete to authenticated using (user_id = auth.uid());

drop policy if exists "own messages read" on public.messages;
create policy "own messages read" on public.messages for select to authenticated
  using (exists (select 1 from public.conversations c where c.id = conversation_id and c.user_id = auth.uid()));
drop policy if exists "own messages insert" on public.messages;
create policy "own messages insert" on public.messages for insert to authenticated
  with check (exists (select 1 from public.conversations c where c.id = conversation_id and c.user_id = auth.uid()));
drop policy if exists "own messages delete" on public.messages;
create policy "own messages delete" on public.messages for delete to authenticated
  using (exists (select 1 from public.conversations c where c.id = conversation_id and c.user_id = auth.uid()));

drop policy if exists "own links read" on public.platform_links;
create policy "own links read"   on public.platform_links for select to authenticated using (user_id = auth.uid());
drop policy if exists "own links delete" on public.platform_links;
create policy "own links delete" on public.platform_links for delete to authenticated using (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- Storage: private bucket for uploaded media, path uploads/{user_id}/...
-- Files are deleted after 24 h by the backend cleanup job.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit)
values ('uploads', 'uploads', false, 16777216)
on conflict (id) do nothing;

drop policy if exists "own uploads read" on storage.objects;
create policy "own uploads read" on storage.objects for select to authenticated
  using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);
