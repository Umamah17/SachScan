/**
 * SachScan integration cards: the ONE place for every card link.
 *
 * Fill in the public values below when each account exists (none of these are secrets).
 * Leave a value empty and that card shows a "Coming soon" badge and opens the app's home page.
 * Never put accounts here that the team does not own.
 *
 * Card status: "live" or "coming_soon". Every card opens its link in a new tab
 * (rel="noopener noreferrer"); Gmail starts the in-app "Connect Gmail" flow instead.
 */
(function () {
  'use strict';

  // ---- Public values (keep in sync with backend/.env where noted) -----------------------
  const WHATSAPP_BOT_NUMBER = '';     // international format, digits only, e.g. '15551234567' (= WHATSAPP_BOT_NUMBER in .env)
  const TELEGRAM_BOT_USERNAME = '';   // e.g. 'SachScanCheckBot'                        (= TELEGRAM_BOT_USERNAME in .env)
  const DISCORD_APP_ID = '';          // Developer Portal -> General Information -> Application ID (= DISCORD_APP_ID in .env)
  const X_HANDLE = '';                // without @
  const REDDIT_SUBREDDIT = '';        // without r/
  const INSTAGRAM_HANDLE = '';        // without @
  const THREADS_HANDLE = '';          // without @
  const CHROME_EXTENSION_URL = '/extension';  // install guide page (load unpacked from the GitHub zip)

  // Discord bot permissions: View Channels, Send Messages, Send Messages in Threads,
  // Create Public Threads, Embed Links, Read Message History. Nothing else.
  const DISCORD_PERMISSIONS = [1n << 10n, 1n << 11n, 1n << 38n, 1n << 35n, 1n << 14n, 1n << 16n].reduce((a, b) => a | b, 0n).toString();

  // ---- Helpers ---------------------------------------------------------------------------
  function formatPhone(digits) {
    if (!digits) return '';
    if (digits.startsWith('91') && digits.length === 12) return `+91 ${digits.slice(2, 7)} ${digits.slice(7)}`;
    if (digits.startsWith('1') && digits.length === 11) return `+1 ${digits.slice(1, 4)}-${digits.slice(4, 7)}-${digits.slice(7)}`;
    return '+' + digits;
  }
  const card = (id, value, url, fallbackUrl, label, fallbackLabel, comingSoon) => ({
    id,
    url: value ? url : fallbackUrl,
    status: value && !comingSoon ? 'live' : 'coming_soon',
    label: value ? label : fallbackLabel,
  });

  // ---- Cards -----------------------------------------------------------------------------
  window.SACHSCAN_INTEGRATIONS = [
    card('whatsapp', WHATSAPP_BOT_NUMBER, `https://wa.me/${WHATSAPP_BOT_NUMBER}?text=help`, 'https://www.whatsapp.com/',
      formatPhone(WHATSAPP_BOT_NUMBER), 'Bot number coming soon'),
    { id: 'gmail', url: '/integrations/gmail/connect', status: 'live', label: 'Connect Gmail' },
    card('discord', DISCORD_APP_ID,
      `https://discord.com/oauth2/authorize?client_id=${DISCORD_APP_ID}&scope=bot%20applications.commands&permissions=${DISCORD_PERMISSIONS}`,
      'https://discord.com/', 'Add to your server', 'Coming soon'),
    card('telegram', TELEGRAM_BOT_USERNAME, `https://t.me/${TELEGRAM_BOT_USERNAME}`, 'https://telegram.org/',
      '@' + TELEGRAM_BOT_USERNAME, 'Coming soon'),
    // Link-only platforms (no scanning: see SETUP.md "Not built"); always "Coming soon"
    card('x', X_HANDLE, `https://x.com/${X_HANDLE}`, 'https://x.com/', '@' + X_HANDLE, 'Follow us', true),
    card('reddit', REDDIT_SUBREDDIT, `https://www.reddit.com/r/${REDDIT_SUBREDDIT}`, 'https://www.reddit.com/', 'r/' + REDDIT_SUBREDDIT, 'Join us', true),
    card('instagram', INSTAGRAM_HANDLE, `https://www.instagram.com/${INSTAGRAM_HANDLE}`, 'https://www.instagram.com/', '@' + INSTAGRAM_HANDLE, 'Follow us', true),
    card('threads', THREADS_HANDLE, `https://www.threads.net/@${THREADS_HANDLE}`, 'https://www.threads.net/', '@' + THREADS_HANDLE, 'Follow us', true),
    { id: 'chrome', url: CHROME_EXTENSION_URL, status: 'coming_soon', label: 'Install guide' },
  ];
})();
