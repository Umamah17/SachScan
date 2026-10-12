/**
 * SachScan frontend API layer
 * ---------------------------
 * - Supabase Auth (Google) session; the access token is attached to every API call,
 *   and a 401 sends the user to the login page.
 * - Live verification chat on help.html (rendered with the page's existing markup/classes).
 * - Header avatar menu, integration cards, footer links, live API status.
 * Loaded on every page right after the Supabase JS library.
 */
(function () {
  'use strict';

  const API = (window.SACHSCAN_API_BASE || '').replace(/\/$/, '');

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const page = (location.pathname.split('/').pop() || 'index.html').replace(/\.html$/, '') || 'index';

  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }
  function safeUrl(u) {
    try { const p = new URL(u); return (p.protocol === 'https:' || p.protocol === 'http:') ? p.href : ''; } catch (e) { return ''; }
  }
  function onReady(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  // ---------------------------------------------------------------------------
  // Auth (Supabase, Google provider)
  // ---------------------------------------------------------------------------
  let configPromise = null;
  function getConfig() {
    if (!configPromise) configPromise = fetch(API + '/api/config').then((r) => r.json()).catch(() => ({}));
    return configPromise;
  }

  let clientPromise = null;
  function supa() {
    if (!clientPromise) {
      clientPromise = getConfig().then((c) => {
        if (!c.supabaseUrl || !c.supabaseAnonKey || !window.supabase) return null;
        return window.supabase.createClient(c.supabaseUrl, c.supabaseAnonKey, {
          auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
        });
      });
    }
    return clientPromise;
  }

  async function getSession() {
    const client = await supa();
    if (!client) return null;
    try { return (await client.auth.getSession()).data.session; } catch (e) { return null; }
  }

  async function authHeaders() {
    const s = await getSession();
    return s ? { Authorization: 'Bearer ' + s.access_token } : {};
  }

  function safeNext(n) {
    return n && n.startsWith('/') && !n.startsWith('//') ? n : null;
  }

  function toLogin() {
    if (page === 'login') return;
    location.href = '/login.html?next=' + encodeURIComponent(location.pathname + location.search);
  }

  async function signOut() {
    const client = await supa();
    if (client) { try { await client.auth.signOut(); } catch (e) { /* already signed out */ } }
    location.href = '/index.html';
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, await authHeaders(), opts.headers || {});
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const res = await fetch(API + path, Object.assign({}, opts, { headers, body }));
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (res.status === 401 && !opts.allow401) toLogin();
    if (!res.ok) {
      const err = new Error((data && data.detail) || ('Request failed (' + res.status + ')'));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // Expose a tiny API for other scripts (e.g. future pages)
  window.SachScan = { api, getSession, signOut, loadConversation: (id) => loadConversation(id) };

  // ===========================================================================
  // HELP PAGE — live verification chat
  // ===========================================================================
  const VERDICT_STYLE = {
    verified: { cls: 'bg-emerald-50 text-emerald-800 border-emerald-200', icon: 'check_circle', label: 'Verified' },
    false: { cls: 'bg-red-50 text-red-700 border-red-200', icon: 'cancel', label: 'False' },
    outdated: { cls: 'bg-amber-50 text-amber-800 border-amber-200', icon: 'history', label: 'Outdated' },
    partly_supported: { cls: 'bg-blue-50 text-blue-800 border-blue-200', icon: 'rule', label: 'Partly Supported' },
    cannot_be_confirmed: { cls: 'bg-neutral-100 text-neutral-700 border-neutral-300', icon: 'help', label: 'Cannot Be Confirmed' },
  };
  const VERDICT_LOCAL = {
    hi: { verified: 'सही पाया गया', false: 'गलत', outdated: 'पुरानी जानकारी', partly_supported: 'आंशिक रूप से सही', cannot_be_confirmed: 'पुष्टि नहीं हो सकी' },
    mr: { verified: 'खरे आढळले', false: 'खोटे', outdated: 'जुनी माहिती', partly_supported: 'अंशतः खरे', cannot_be_confirmed: 'पुष्टी होऊ शकली नाही' },
  };
  const INPUT_LABEL = { text: 'Text', image: 'Screenshot / Image', audio: 'Voice Note', pdf: 'PDF', url: 'Web Link', video: 'Video' };

  function verdictLabel(v, lang) {
    const base = VERDICT_STYLE[v] ? VERDICT_STYLE[v].label : v;
    const local = VERDICT_LOCAL[lang] && VERDICT_LOCAL[lang][v];
    return local ? base + ' · ' + local : base;
  }
  function chip(v, lang, extra) {
    const s = VERDICT_STYLE[v] || VERDICT_STYLE.cannot_be_confirmed;
    return `<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold border ${s.cls} ${extra || ''}">
      <span class="material-symbols-outlined text-[14px]">${s.icon}</span><span>${esc(verdictLabel(v, lang).toUpperCase())}</span></span>`;
  }

  let pendingFile = null;
  let currentConversation = null; // follow-up messages continue the same saved conversation

  function chatEls() {
    return {
      messages: $('chat-messages'), form: $('chat-form'), input: $('chat-input'), send: $('btn-send-chat'),
      typing: $('typing-indicator'), status: $('typing-status-text'), count: $('char-count'),
    };
  }
  function scrollChat() { const m = $('chat-messages'); if (m) m.scrollTop = m.scrollHeight; }
  function timeStr(when) { return (when ? new Date(when) : new Date()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }

  function appendUser(text, fileName, when) {
    const el = chatEls();
    const div = document.createElement('div');
    div.className = 'flex items-start justify-end gap-3';
    div.innerHTML = `
      <div class="chat-bubble-user p-4 rounded-2xl max-w-xl text-xs sm:text-sm leading-relaxed">
        ${fileName ? `<p class="flex items-center gap-1.5 font-semibold mb-1"><span class="material-symbols-outlined text-[16px]">attach_file</span>${esc(fileName)}</p>` : ''}
        ${text ? `<p class="whitespace-pre-wrap">${esc(text)}</p>` : ''}
        <div class="text-[10px] text-neutral-400 mt-1.5 text-right font-code-mono">${timeStr(when)}</div>
      </div>
      <div class="w-8 h-8 rounded-lg bg-neutral-800 text-white flex items-center justify-center flex-shrink-0 mt-0.5">
        <span class="material-symbols-outlined text-[18px]">person</span>
      </div>`;
    el.messages.appendChild(div);
    scrollChat();
  }

  function agentShell(inner) {
    const div = document.createElement('div');
    div.className = 'flex items-start gap-3';
    div.innerHTML = `
      <div class="w-8 h-8 rounded-lg bg-black text-white flex items-center justify-center flex-shrink-0 mt-0.5">
        <span class="material-symbols-outlined text-[18px]">verified_user</span>
      </div>
      <div class="chat-bubble-agent p-5 sm:p-6 rounded-2xl max-w-2xl text-xs sm:text-sm text-black space-y-4 leading-relaxed border border-black/10">${inner}</div>`;
    $('chat-messages').appendChild(div);
    scrollChat();
    return div;
  }

  function appendError(message) {
    agentShell(`
      <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border bg-amber-50 text-amber-800 border-amber-200">
        <span class="material-symbols-outlined text-[16px]">warning</span><span>COULD NOT CHECK THIS</span>
      </div>
      <p class="text-sm text-black leading-snug">${esc(message)}</p>`);
  }

  function renderSource(s) {
    const url = safeUrl(s.url);
    const name = esc(s.publisher || s.title || 'Source');
    return `<li class="pl-2 border-l-2 border-black/10">
      <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
        ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="font-semibold text-black underline hover:text-secondary">${name}</a>` : `<span class="font-semibold">${name}</span>`}
        ${s.date ? `<span class="font-code-mono text-neutral-500">${esc(s.date)}</span>` : ''}
      </div>
      ${s.title && s.title !== s.publisher ? `<div class="text-[11px] text-neutral-600">${esc(s.title)}</div>` : ''}
      ${s.quote ? `<div class="text-[11px] text-neutral-700 italic">“${esc(s.quote)}”</div>` : ''}
    </li>`;
  }

  function renderClaim(c, i, lang) {
    return `<div class="p-3 rounded-xl bg-white border border-black/10 space-y-1.5">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <span class="text-xs font-semibold text-black leading-snug flex-1 min-w-[12rem]">${i + 1}. ${esc(c.claim)}</span>
        ${chip(c.verdict, lang)}
      </div>
      ${c.explanation ? `<p class="text-xs text-neutral-800 leading-relaxed">${esc(c.explanation)}</p>` : ''}
      ${c.sources && c.sources.length ? `<ul class="space-y-1.5 pt-1">${c.sources.map(renderSource).join('')}</ul>` : ''}
    </div>`;
  }

  function appendResult(r, when) {
    const lang = r.language || 'en';
    const v = VERDICT_STYLE[r.overall_verdict] || VERDICT_STYLE.cannot_be_confirmed;
    const ai = r.ai_generation || { score: 0, indicators: [] };
    const meta = r.meta || {};
    const extractedBlock = r.input_type !== 'text' && r.extracted_text ? `
      <details class="text-xs">
        <summary class="cursor-pointer text-[11px] font-bold text-neutral-500 uppercase tracking-wider">What we read from the ${esc((INPUT_LABEL[r.input_type] || r.input_type).toLowerCase())}</summary>
        <p class="mt-1.5 p-2.5 rounded-lg bg-surface-container-low whitespace-pre-wrap text-neutral-800">${esc(r.extracted_text.slice(0, 1500))}${r.extracted_text.length > 1500 ? '…' : ''}</p>
      </details>` : '';
    const degraded = meta.degraded ? `
      <div class="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-[11px] flex items-start gap-1.5">
        <span class="material-symbols-outlined text-[15px]">info</span>
        <span>${meta.ai_configured === false ? 'AI evidence search is not configured on the server, so only offline checks ran.' : 'Some evidence sources were unavailable for this check, so parts of it may be marked "cannot be confirmed".'}</span>
      </div>` : '';
    const scam = r.scam_signals && r.scam_signals.length ? `
      <div>
        <span class="text-[11px] font-bold text-neutral-700 block mb-1">4. Manipulation &amp; Scam Red Flags:</span>
        <ul class="space-y-1 text-xs text-red-800 pl-4 list-disc">${r.scam_signals.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
      </div>` : '';
    const reply = r.share_safe_reply || '';

    const div = agentShell(`
      <div class="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-black/10">
        <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border ${v.cls}">
          <span class="material-symbols-outlined text-[16px]">${v.icon}</span>
          <span>${esc(verdictLabel(r.overall_verdict, lang).toUpperCase())}</span>
        </div>
        <span class="text-xs font-code-mono text-neutral-500 font-semibold">${r.claims && r.claims.length ? 'Confidence: ' + esc(r.confidence) + '%' : ''}${r.input_type && r.input_type !== 'text' ? ' • ' + esc(INPUT_LABEL[r.input_type] || r.input_type) : ''}</span>
      </div>

      <div>
        <span class="text-[11px] font-bold text-neutral-500 uppercase tracking-wider block mb-1">Bottom Line Verdict:</span>
        <p class="text-sm font-semibold text-black leading-snug">${esc(r.overall_summary)}</p>
      </div>
      ${degraded}
      ${extractedBlock}

      <div class="p-4 rounded-xl bg-surface-container-low border border-black/5 space-y-3">
        <div class="flex items-center gap-1.5 text-xs font-bold text-black uppercase tracking-wider">
          <span class="material-symbols-outlined text-[18px] text-secondary">analytics</span>
          <span>Forensic Summary: How We Checked It</span>
        </div>

        <div>
          <span class="text-[11px] font-bold text-neutral-700 block mb-1.5">1. Claim DNA Decomposition (${(r.claims || []).length} claim${(r.claims || []).length === 1 ? '' : 's'}):</span>
          <div class="space-y-2">${(r.claims || []).map((c, i) => renderClaim(c, i, lang)).join('') || '<p class="text-xs text-neutral-600">No specific, checkable fact was found in this message.</p>'}</div>
        </div>

        <div>
          <span class="text-[11px] font-bold text-neutral-700 block mb-1">2. AI Generation &amp; Stylometry Analysis (Score: ${esc(ai.score)}%):</span>
          <ul class="space-y-1 text-xs text-neutral-700 pl-4 list-disc">${(ai.indicators || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
          <p class="text-[10px] text-neutral-500 mt-1">Writing-style hint only, not proof of who wrote it.</p>
        </div>

        <div>
          <span class="text-[11px] font-bold text-neutral-700 block mb-1">3. Cross-Referenced Sources:</span>
          <div class="flex flex-wrap gap-1 mb-1.5 font-code-mono text-[10px]">
            ${(r.sources_checked || []).map((s) => `<span class="px-2 py-0.5 rounded bg-surface-container font-medium text-black">${esc(s)}</span>`).join('') || '<span class="text-neutral-500">No sources could be retrieved.</span>'}
          </div>
        </div>
        ${scam}
      </div>

      ${reply ? `
      <div class="p-3.5 rounded-xl bg-neutral-900 text-white">
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-[10px] font-sans font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1">
            <span class="material-symbols-outlined text-[14px]">reply</span>
            <span>Share-Safe Reply (Copy &amp; Paste to Group)</span>
          </span>
          <button type="button" class="btn-copy-reply text-[11px] px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 text-white font-sans transition-all flex items-center gap-1">
            <span class="material-symbols-outlined text-[13px]">content_copy</span><span>Copy</span>
          </button>
        </div>
        <p class="font-sans text-xs text-neutral-200 leading-relaxed italic">"${esc(reply)}"</p>
      </div>` : ''}

      <div class="text-[10px] text-neutral-400 text-right font-code-mono pt-1">
        Verified at ${timeStr(when)}${meta.elapsed_s != null && !when ? ' • ' + esc(meta.elapsed_s) + 's' : ''}${meta.cached ? ' • cached' : ''} • Saved to your history
      </div>`);

    const copyBtn = div.querySelector('.btn-copy-reply');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        const done = () => {
          copyBtn.innerHTML = '<span class="material-symbols-outlined text-[13px] text-emerald-400">check</span><span class="text-emerald-400">Copied!</span>';
          setTimeout(() => { copyBtn.innerHTML = '<span class="material-symbols-outlined text-[13px]">content_copy</span><span>Copy</span>'; }, 2000);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(reply).then(done).catch(() => {});
      });
    }
  }

  // Reads the NDJSON progress stream from the backend
  async function streamCheck(path, formData) {
    const el = chatEls();
    el.send && (el.send.disabled = true);
    el.typing && el.typing.classList.remove('hidden');
    if (el.status) el.status.textContent = 'Connecting to SachScan verification engine…';
    scrollChat();
    try {
      const res = await fetch(API + path, { method: 'POST', headers: await authHeaders(), body: formData });
      if (!res.ok) {
        let msg = 'Request failed (' + res.status + ')';
        try { msg = (await res.json()).detail || msg; } catch (e) { /* not JSON */ }
        if (res.status === 401) { toLogin(); return; }
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let gotResult = false;
      const handle = (line) => {
        if (!line.trim()) return;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { return; }
        if (ev.type === 'progress' && el.status) el.status.textContent = ev.message;
        else if (ev.type === 'claims' && el.status) el.status.textContent = `Found ${ev.claims.length} claim${ev.claims.length === 1 ? '' : 's'} — searching trusted sources…`;
        else if (ev.type === 'result') {
          gotResult = true;
          appendResult(ev.data);
        } else if (ev.type === 'error') { gotResult = true; appendError(ev.message); }
        else if (ev.type === 'conversation') { setCurrentConversation(ev.id); document.dispatchEvent(new CustomEvent('sachscan:saved', { detail: ev.id })); }
      };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) { handle(buf.slice(0, nl)); buf = buf.slice(nl + 1); }
      }
      handle(buf);
      if (!gotResult) appendError('The connection closed before a verdict was ready. Please try again.');
    } catch (err) {
      appendError(err && err.message && err.message !== 'Failed to fetch' ? err.message : 'Could not reach the SachScan server. Check your connection and try again.');
    } finally {
      el.typing && el.typing.classList.add('hidden');
      el.send && (el.send.disabled = false);
      scrollChat();
    }
  }

  function setCurrentConversation(id) {
    currentConversation = id;
    const url = new URL(location.href);
    if (id) url.searchParams.set('c', id); else url.searchParams.delete('c');
    history.replaceState(null, '', url.pathname + url.search);
  }

  const FILE_LABEL = { image: 'Screenshot', audio: 'Voice note', pdf: 'PDF', video: 'Video' };

  // Re-draw a saved conversation into the existing chat view (used on reload and by History)
  async function loadConversation(id) {
    const el = chatEls();
    if (!el.messages) return;
    try {
      const data = await api('/conversations/' + encodeURIComponent(id) + '/messages');
      // keep only the welcome bubble, then replay the saved exchange(s)
      Array.from(el.messages.children).slice(1).forEach((n) => n.remove());
      for (const m of data.messages || []) {
        if (m.role === 'user') {
          // file messages store the caption or file name as text; show them with the attachment label
          const fileLabel = FILE_LABEL[m.content_type];
          if (fileLabel) appendUser('', m.text || fileLabel, m.created_at);
          else appendUser(m.text || '', '', m.created_at);
        } else if (m.result_json) {
          appendResult(m.result_json, m.created_at);
        } else if (m.text) {
          appendError(m.text);
        }
      }
      setCurrentConversation(id);
    } catch (err) {
      if (err.status === 404) setCurrentConversation(null);
      else appendError(err.message);
    }
  }

  // ---------------------------------------------------------------------------
  // History dropdown (GET /conversations) + "Link a chat app" code
  // ---------------------------------------------------------------------------
  const PLATFORM_ICON = { web: 'language', whatsapp: 'chat', telegram: 'send', discord: 'forum', gmail: 'mail', extension: 'extension' };

  function relTime(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    if (s < 7 * 86400) return Math.floor(s / 86400) + ' d ago';
    return new Date(iso).toLocaleDateString();
  }

  async function renderHistory() {
    const list = $('history-list');
    if (!list) return;
    list.innerHTML = '<li class="px-4 py-3 text-neutral-500">Loading…</li>';
    try {
      const { conversations } = await api('/conversations?limit=50');
      if (!conversations.length) {
        list.innerHTML = '<li class="px-4 py-4 text-neutral-500">No saved checks yet. Your checks from the web, WhatsApp, Telegram, Discord and Gmail appear here.</li>';
        return;
      }
      list.innerHTML = conversations.map((c) => `
        <li class="group flex items-center gap-2 px-3 py-2.5 hover:bg-black/5 cursor-pointer ${c.id === currentConversation ? 'bg-black/5' : ''}" data-conv="${esc(c.id)}">
          <span class="material-symbols-outlined text-[16px] text-neutral-500" title="${esc(c.platform)}">${PLATFORM_ICON[c.platform] || 'chat'}</span>
          <span class="flex-1 min-w-0">
            <span class="block truncate font-medium text-black">${esc(c.title || 'Untitled check')}</span>
            <span class="block text-[10px] text-neutral-500 font-code-mono">${esc(c.platform)} • ${esc(relTime(c.updated_at))}</span>
          </span>
          <button type="button" class="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded text-neutral-400 hover:text-red-600" data-delete="${esc(c.id)}" title="Delete conversation">
            <span class="material-symbols-outlined text-[16px]">delete</span>
          </button>
        </li>`).join('');
    } catch (err) {
      list.innerHTML = `<li class="px-4 py-3 text-red-700">${esc(err.message)}</li>`;
    }
  }

  function initHistory() {
    const btn = $('btn-history'), panel = $('history-panel'), list = $('history-list');
    if (!btn || !panel) return;
    const close = () => { panel.classList.add('hidden'); btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const opening = panel.classList.contains('hidden');
      panel.classList.toggle('hidden');
      btn.setAttribute('aria-expanded', String(opening));
      if (opening) renderHistory();
    });
    document.addEventListener('click', (e) => { if (!panel.contains(e.target) && e.target !== btn) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    list.addEventListener('click', async (e) => {
      const del = e.target.closest('[data-delete]');
      if (del) {
        e.stopPropagation();
        if (!confirm('Delete this conversation from your history?')) return;
        try {
          await api('/conversations/' + encodeURIComponent(del.dataset.delete), { method: 'DELETE' });
          if (del.dataset.delete === currentConversation) { $('btn-clear-chat') && $('btn-clear-chat').click(); }
          renderHistory();
        } catch (err) { alert(err.message); }
        return;
      }
      const row = e.target.closest('[data-conv]');
      if (row) { close(); loadConversation(row.dataset.conv); }
    });

    const linkBtn = $('btn-link-app'), codeBox = $('history-link-code');
    if (linkBtn && codeBox) linkBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      codeBox.classList.remove('hidden');
      codeBox.textContent = 'Creating a code…';
      try {
        const r = await api('/link/start', { method: 'POST' });
        codeBox.innerHTML = `Send <strong class="font-code-mono text-black text-sm">link ${esc(r.code)}</strong> to the SachScan WhatsApp, Telegram or Discord bot within 10 minutes. Your chats there will then show up in this History.`;
      } catch (err) { codeBox.textContent = err.message; }
    });

    // keep the list fresh after each saved check
    document.addEventListener('sachscan:saved', () => { if (!panel.classList.contains('hidden')) renderHistory(); });
  }

  function setPendingFile(file) {
    const el = chatEls();
    pendingFile = file;
    if (el.count) el.count.textContent = file ? `📎 ${file.name || 'attachment'} attached — press Verify` : `${el.input.value.length} characters`;
  }

  function submitChat() {
    const el = chatEls();
    const text = el.input.value.trim();
    if (!text && !pendingFile) return;
    if (el.send && el.send.disabled) return;
    const fd = new FormData();
    if (text) fd.append('text', text);
    if (pendingFile) fd.append('file', pendingFile, pendingFile.name || 'upload');
    if (currentConversation) fd.append('conversation_id', currentConversation);
    appendUser(text, pendingFile ? (pendingFile.name || 'attachment') : '');
    el.input.value = '';
    setPendingFile(null);
    if (el.count) el.count.textContent = '0 characters';
    streamCheck('/api/check/stream', fd);
  }

  function initHelp() {
    // Intercept the chat form (also catches the sample buttons' synthetic submit events)
    window.addEventListener('submit', (e) => {
      if (!e.target || e.target.id !== 'chat-form') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      submitChat();
    }, true);

    onReady(async () => {
      const el = chatEls();
      if (!el.input) return;
      if (!(await getSession())) { toLogin(); return; }   // the checker requires sign-in
      const clear = $('btn-clear-chat');
      if (clear) clear.addEventListener('click', () => setCurrentConversation(null));
      const openId = new URLSearchParams(location.search).get('c');
      if (openId) loadConversation(openId);
      initHistory();

      // Enter sends, Shift+Enter adds a newline (as the hint under the box says)
      el.input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          submitChat();
        }
      });
      // Paste or drop screenshots / voice notes / PDFs straight into the chat box
      el.input.addEventListener('paste', (e) => {
        const f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
        if (f) { e.preventDefault(); setPendingFile(f); }
      });
      const zone = el.messages ? el.messages.parentElement : el.input;
      ['dragover', 'dragenter'].forEach((t) => zone.addEventListener(t, (e) => { if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) e.preventDefault(); }));
      zone.addEventListener('drop', (e) => {
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) { e.preventDefault(); setPendingFile(f); el.input.focus(); }
      });
      el.input.addEventListener('input', () => { if (pendingFile) setPendingFile(pendingFile); });

      // Content shared from WhatsApp via the PWA share target
      const params = new URLSearchParams(location.search);
      const shared = params.get('shared');
      if (shared) {
        history.replaceState(null, '', location.pathname);
        api('/api/shared/' + encodeURIComponent(shared))
          .then((p) => {
            appendUser(p.text || '', p.has_file ? (p.file_name || 'Shared file') : '');
            streamCheck('/api/shared/' + encodeURIComponent(shared) + '/check/stream', new FormData());
          })
          .catch((err) => appendError(err.message));
      }
    });
  }

  // ===========================================================================
  // LOGIN PAGE — "Continue with Google" (Supabase Auth, scopes: openid email profile)
  // ===========================================================================
  function initLogin() {
    function showAlert(type, title, message) {
      const box = $('auth-alert'), icon = $('alert-icon');
      if (!box) return;
      const map = {
        error: ['bg-red-50 border-red-200 text-red-900', 'error', 'text-red-600'],
        warning: ['bg-amber-50 border-amber-200 text-amber-900', 'warning', 'text-amber-600'],
      }[type] || ['bg-amber-50 border-amber-200 text-amber-900', 'info', 'text-amber-600'];
      box.className = 'mb-6 p-4 rounded-2xl text-xs border transition-all duration-200 ' + map[0];
      icon.textContent = map[1];
      icon.className = `material-symbols-outlined text-[20px] ${map[2]} shrink-0`;
      $('alert-title').textContent = title;
      $('alert-message').textContent = message;
      const action = $('alert-action-btn');
      if (action) action.classList.add('hidden');
    }

    onReady(async () => {
      const params = new URLSearchParams(location.search);
      const next = safeNext(params.get('next')) || '/help.html';
      const btn = $('btn-google-signin');
      const client = await supa();   // also completes the OAuth redirect (?code=...) if present
      if (!client) {
        showAlert('warning', 'Sign-in not configured', 'The server is missing its Supabase settings. See SETUP.md.');
        if (btn) btn.disabled = true;
        return;
      }
      if (await getSession()) { location.replace(next); return; }
      const err = params.get('error_description') || new URLSearchParams(location.hash.slice(1)).get('error_description');
      if (err) showAlert('error', 'Sign-in failed', err);
      if (params.get('reason') === 'expired') showAlert('warning', 'Session expired', 'Please sign in again.');
      client.auth.onAuthStateChange((event, session) => { if (session) location.replace(next); });

      if (btn) btn.addEventListener('click', async () => {
        btn.disabled = true;
        const { error } = await client.auth.signInWithOAuth({
          provider: 'google',
          options: { redirectTo: location.origin + '/login.html?next=' + encodeURIComponent(next) },
        });
        if (error) { btn.disabled = false; showAlert('error', 'Sign-in failed', error.message); }
      });
    });
  }

  // ===========================================================================
  // HEADER — avatar menu with "Sign out" when signed in
  // ===========================================================================
  function initHeaderAuth() {
    onReady(async () => {
      const header = $('main-navbar');
      if (!header || page === 'login') return;
      const session = await getSession();
      if (!session) return;
      const meta = session.user.user_metadata || {};
      const name = meta.full_name || meta.name || session.user.email || 'Account';
      const avatar = safeUrl(meta.avatar_url || meta.picture || '');
      const initial = esc((name.trim()[0] || '?').toUpperCase());

      // Desktop: replace the "Get Started" pill with the avatar button + menu
      // (the black "Get Started" pill: links to login.html on most pages, integrations.html on index)
      const getStarted = Array.from(header.querySelectorAll('a.bg-black.rounded-full, a[href="login.html"], a[href="/login.html"]'))
        .find((a) => !a.closest('#nav-mobile-drawer') && /get started|explore integrations/i.test(a.textContent));
      if (getStarted) {
        const wrap = document.createElement('div');
        wrap.className = 'relative';
        wrap.innerHTML = `
          <button type="button" id="user-menu-btn" class="flex items-center gap-2 pl-1 pr-3 py-1 rounded-full border border-black/15 bg-white/70 hover:bg-white transition-all shadow-sm" aria-haspopup="true" aria-expanded="false">
            ${avatar ? `<img src="${esc(avatar)}" alt="" referrerpolicy="no-referrer" class="w-7 h-7 rounded-full object-cover"/>`
                     : `<span class="w-7 h-7 rounded-full bg-black text-white text-xs font-bold flex items-center justify-center">${initial}</span>`}
            <span class="material-symbols-outlined text-[16px] text-black/60">expand_more</span>
          </button>
          <div id="user-menu" class="hidden absolute right-0 mt-2 w-60 rounded-2xl bg-white border border-black/10 shadow-xl p-2 z-50">
            <div class="px-3 py-2 border-b border-black/5 mb-1">
              <div class="text-sm font-semibold text-black truncate">${esc(name)}</div>
              <div class="text-[11px] text-neutral-500 truncate">${esc(session.user.email || '')}</div>
            </div>
            <a href="/help.html" class="flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-black hover:bg-black/5">
              <span class="material-symbols-outlined text-[18px]">fact_check</span>Check a message</a>
            <button type="button" data-signout class="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-red-600 hover:bg-red-50">
              <span class="material-symbols-outlined text-[18px]">logout</span>Sign out</button>
          </div>`;
        getStarted.replaceWith(wrap);
        const btn = wrap.querySelector('#user-menu-btn'), menu = wrap.querySelector('#user-menu');
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const open = menu.classList.toggle('hidden') === false;
          btn.setAttribute('aria-expanded', String(open));
        });
        document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) menu.classList.add('hidden'); });
      }
      // Mobile drawer: "Get Started" becomes "Sign out"
      Array.from(header.querySelectorAll('#nav-mobile-drawer a')).filter((a) => /get started/i.test(a.textContent)).forEach((a) => {
        a.textContent = 'Sign out';
        a.setAttribute('href', '#');
        a.setAttribute('data-signout', '');
      });
      header.addEventListener('click', (e) => {
        const so = e.target.closest && e.target.closest('[data-signout]');
        if (so) { e.preventDefault(); e.stopImmediatePropagation(); signOut(); }
      }, true);
    });
  }

  // ===========================================================================
  // SITE-WIDE — integration cards, footer links, live API status
  // ===========================================================================
  const APP_PATTERNS = [
    ['whatsapp', /whatsapp/i], ['gmail', /gmail|google workspace/i], ['discord', /discord/i],
    ['telegram', /telegram/i], ['reddit', /reddit/i], ['instagram', /instagram/i],
    ['threads', /threads/i], ['chrome', /chrome|chromium/i], ['x', /(^|\W)x(\W|$)|twitter/i],
  ];
  function appFromText(text) {
    for (const [id, rx] of APP_PATTERNS) if (rx.test(text || '')) return id;
    return null;
  }

  // Card links come from integrations.config.js (window.SACHSCAN_INTEGRATIONS)
  function cardConfig(id) {
    return (window.SACHSCAN_INTEGRATIONS || []).find((c) => c.id === id) || null;
  }

  function openExternal(url) {
    const a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function startGmailConnect() {
    if (!(await getSession())) { toLogin(); return; }
    try {
      const res = await api('/integrations/gmail/connect', { method: 'POST' });
      if (res && res.url) location.href = res.url;   // Google consent screen (gmail.readonly)
    } catch (err) {
      alert(err.status === 404 || err.status === 503 ? 'Gmail connection is not available yet.' : err.message);
    }
  }

  function openApp(appId) {
    const cfg = cardConfig(appId);
    if (!cfg || !cfg.url) return;
    if (appId === 'gmail') { startGmailConnect(); return; }
    if (cfg.url.startsWith('/')) location.href = cfg.url;
    else openExternal(cfg.url);
  }

  function setGmailConnected(connected, email) {
    document.querySelectorAll('[data-handle="gmail"]').forEach((el) => {
      el.textContent = connected ? 'Connected ✓' : 'Connect Gmail';
      if (connected && email) el.title = email;
    });
  }

  function initIntegrationCards() {
    window.addEventListener('click', (e) => {
      const card = e.target && e.target.closest ? e.target.closest('[data-app]') : null;
      if (!card || !card.matches('.app-card, .card-integration-3d')) return;
      openApp(card.getAttribute('data-app')); // the page's own handler still runs (card highlight)
    });
    onReady(async () => {
      document.querySelectorAll('.app-card[data-app], .card-integration-3d[data-app]').forEach((c) => {
        const cfg = cardConfig(c.getAttribute('data-app'));
        if (!cfg) return;
        c.style.cursor = 'pointer';
        const handle = c.querySelector('[data-handle]');
        if (handle && cfg.label) handle.textContent = cfg.label;
        // integrations.html: status pills become "Coming soon" when the account isn't set up yet
        const pill = c.querySelector('span.rounded-full.uppercase');
        if (cfg.status === 'coming_soon' && pill && !pill.hasAttribute('data-badge') && c.classList.contains('app-card')) {
          pill.textContent = 'Coming soon';
          pill.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-100 text-amber-800 border border-amber-200';
        }
      });
      // integrations.html?app=whatsapp -> highlight that card (from footer links)
      const want = new URLSearchParams(location.search).get('app');
      const target = want && document.querySelector(`.app-card[data-app="${CSS.escape(want)}"]`);
      if (target) {
        document.querySelectorAll('.app-card').forEach((c) => c.classList.remove('active-app'));
        target.classList.add('active-app');
        setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300);
      }
      // Gmail card shows "Connected ✓" for users who connected their inbox
      if (await getSession()) {
        try {
          const st = await api('/integrations/gmail/status', { allow401: true });
          setGmailConnected(!!(st && st.connected), st && st.email);
        } catch (e) { /* not available yet */ }
      }
    });
  }

  const FOOTER_PATHS = {
    technology: '/technology.html',
    'live-scanner': '/help.html',
    'benchmark-reports': '/index.html#benchmarks',
    'api-and-docs': '/api/docs',
    pricing: '/login.html?mode=register',
    'security-and-trust': '/legal/security',
    'privacy-policy': '/legal/privacy',
    'terms-of-service': '/legal/terms',
    'vulnerability-disclosure': '/legal/disclosure',
  };
  const TEXT_PATHS = [
    [/security architecture/i, '/legal/security'], [/privacy/i, '/legal/privacy'],
    [/terms of service/i, '/legal/terms'], [/responsible disclosure/i, '/legal/disclosure'],
  ];

  function initFooterLinks() {
    onReady(() => {
      document.querySelectorAll('a[href="#"]').forEach((a) => {
        if (a.id === 'link-forgot-pw') return; // handled by the login bridge
        const text = a.textContent.trim();
        const dataPath = a.getAttribute('data-path');
        let href = null;
        if (dataPath === 'integrations') {
          const appId = appFromText(text);
          if (appId === 'chrome') {
            a.addEventListener('click', (e) => { e.preventDefault(); e.stopImmediatePropagation(); openApp('chrome'); }, true);
            return;
          }
          href = '/integrations.html' + (appId ? '?app=' + appId : '');
        } else if (dataPath && FOOTER_PATHS[dataPath]) {
          href = FOOTER_PATHS[dataPath];
        } else {
          const hit = TEXT_PATHS.find(([rx]) => rx.test(text));
          if (hit) href = hit[1];
        }
        if (href) a.setAttribute('href', href);
      });
    });
  }

  function initApiStatus() {
    onReady(() => {
      const spans = Array.from(document.querySelectorAll('span')).filter((s) => /^API Status:/i.test(s.textContent.trim()));
      if (!spans.length) return;
      api('/api/health')
        .then((h) => {
          const p = h.providers || {};
          const ai = [p.gemini && 'Gemini', p.claude && 'Claude', p.perplexity && 'Perplexity'].filter(Boolean);
          const text = ai.length ? `API Status: Operational (${ai.join(' + ')})` : 'API Status: Online (offline checks only)';
          spans.forEach((s) => { s.textContent = text; });
        })
        .catch(() => spans.forEach((s) => { s.textContent = 'API Status: Unreachable'; }));
    });
  }

  // ---------------------------------------------------------------------------
  initFooterLinks();
  initApiStatus();
  initHeaderAuth();
  if (page === 'index' || page === 'integrations') initIntegrationCards();
  if (page === 'help') initHelp();
  if (page === 'login') initLogin();
})();
