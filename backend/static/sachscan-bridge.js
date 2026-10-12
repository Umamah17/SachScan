/**
 * SachScan API bridge
 * -------------------
 * Injected by the backend into every HTML page at serve time, so the frontend files
 * stay untouched. It intercepts the existing forms/buttons (capture phase, before the
 * pages' own demo handlers run), calls the real API, and renders results with the same
 * markup and Tailwind classes the pages already use.
 */
(function () {
  'use strict';

  const API = (window.SACHSCAN_API_BASE || '').replace(/\/$/, '');
  const TOKEN_KEY = 'sachscan_token';
  const SESSION_KEY = 'sachscan_session_user'; // same key login.html already reads

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* private mode */ } },
  };
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
  function authHeaders() {
    const t = store.get(TOKEN_KEY);
    return t ? { Authorization: 'Bearer ' + t } : {};
  }
  async function api(path, opts = {}) {
    const headers = Object.assign({}, authHeaders(), opts.headers || {});
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const res = await fetch(API + path, Object.assign({}, opts, { headers, body }));
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const err = new Error((data && data.detail) || ('Request failed (' + res.status + ')'));
      err.status = res.status;
      throw err;
    }
    return data;
  }
  function onReady(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  // Save session in the same shape the page's own script expects
  function saveSession(token, user) {
    if (token) store.set(TOKEN_KEY, token);
    if (user) store.set(SESSION_KEY, JSON.stringify({
      name: user.name, email: user.email, scansLeft: user.scansLeft,
      scansTotal: user.scansTotal, apiKeyHint: user.apiKeyHint, provider: user.provider,
    }));
  }
  function clearSession() { store.del(TOKEN_KEY); store.del(SESSION_KEY); }

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

  function chatEls() {
    return {
      messages: $('chat-messages'), form: $('chat-form'), input: $('chat-input'), send: $('btn-send-chat'),
      typing: $('typing-indicator'), status: $('typing-status-text'), count: $('char-count'),
    };
  }
  function scrollChat() { const m = $('chat-messages'); if (m) m.scrollTop = m.scrollHeight; }
  function timeStr() { return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }

  function appendUser(text, fileName) {
    const el = chatEls();
    const div = document.createElement('div');
    div.className = 'flex items-start justify-end gap-3';
    div.innerHTML = `
      <div class="chat-bubble-user p-4 rounded-2xl max-w-xl text-xs sm:text-sm leading-relaxed">
        ${fileName ? `<p class="flex items-center gap-1.5 font-semibold mb-1"><span class="material-symbols-outlined text-[16px]">attach_file</span>${esc(fileName)}</p>` : ''}
        ${text ? `<p class="whitespace-pre-wrap">${esc(text)}</p>` : ''}
        <div class="text-[10px] text-neutral-400 mt-1.5 text-right font-code-mono">${timeStr()}</div>
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

  function appendResult(r) {
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
        Verified at ${timeStr()}${meta.elapsed_s != null ? ' • ' + esc(meta.elapsed_s) + 's' : ''}${meta.cached ? ' • cached' : ''}${meta.scans_left != null ? ' • ' + esc(meta.scans_left) + ' scans left' : ''} • Not stored on our servers
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
      const res = await fetch(API + path, { method: 'POST', headers: authHeaders(), body: formData });
      if (!res.ok) {
        let msg = 'Request failed (' + res.status + ')';
        try { msg = (await res.json()).detail || msg; } catch (e) { /* not JSON */ }
        if (res.status === 401) clearSession();
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
          if (ev.data.meta && ev.data.meta.scans_left != null) updateStoredScans(ev.data.meta.scans_left);
        } else if (ev.type === 'error') { gotResult = true; appendError(ev.message); }
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

  function updateStoredScans(left) {
    try {
      const s = JSON.parse(store.get(SESSION_KEY) || 'null');
      if (s) { s.scansLeft = left; store.set(SESSION_KEY, JSON.stringify(s)); }
    } catch (e) { /* ignore */ }
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

    onReady(() => {
      const el = chatEls();
      if (!el.input) return;

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
  // LOGIN PAGE — real accounts
  // ===========================================================================
  function initLogin() {
    let cfg = { oauth: {}, demoSSO: true, freeScans: 500 };
    api('/api/config').then((c) => { cfg = c || cfg; }).catch(() => {});

    function showAlert(type, title, message, registerEmail) {
      const box = $('auth-alert'), icon = $('alert-icon'), btn = $('alert-action-btn');
      if (!box) return;
      box.classList.remove('hidden', 'bg-red-50', 'border-red-200', 'text-red-900', 'bg-emerald-50', 'border-emerald-200', 'text-emerald-900', 'bg-amber-50', 'border-amber-200', 'text-amber-900');
      const map = {
        error: ['bg-red-50 border-red-200 text-red-900', 'error', 'text-red-600'],
        success: ['bg-emerald-50 border-emerald-200 text-emerald-900', 'check_circle', 'text-emerald-600'],
        warning: ['bg-amber-50 border-amber-200 text-amber-900', 'warning', 'text-amber-600'],
      }[type] || ['bg-amber-50 border-amber-200 text-amber-900', 'warning', 'text-amber-600'];
      map[0].split(' ').forEach((c) => box.classList.add(c));
      icon.textContent = map[1];
      icon.className = `material-symbols-outlined text-[20px] ${map[2]} shrink-0`;
      $('alert-title').textContent = title;
      $('alert-message').textContent = message;
      if (registerEmail !== undefined) {
        btn.classList.remove('hidden');
        btn.onclick = () => {
          $('tab-register').click();
          if (registerEmail) { $('reg-email').value = registerEmail; $('reg-name').focus(); }
        };
      } else {
        btn.classList.add('hidden');
      }
    }
    function hideAlert() { const b = $('auth-alert'); if (b) b.classList.add('hidden'); }

    function renderSuccess(user) {
      hideAlert();
      $('form-login').classList.add('hidden');
      $('form-register').classList.add('hidden');
      $('tab-login').parentElement.classList.add('hidden');
      $('auth-title').textContent = 'Authenticated Session';
      $('auth-subtitle').textContent = 'Your SachScan cryptographic keys and quota are ready.';
      $('auth-success-card').classList.remove('hidden');
      $('success-user-name').textContent = `Welcome, ${user.name}!`;
      $('success-user-email').textContent = user.email;
      $('success-scans-left').textContent = `${user.scansLeft} / ${user.scansTotal || cfg.freeScans} scans remaining`;
      if (user.apiKeyHint) {
        const span = Array.from(document.querySelectorAll('#auth-success-card span.font-mono')).find((s) => /sach_live_sec_/.test(s.textContent));
        if (span) { span.textContent = user.apiKeyHint; span.title = 'Your developer API key (send as X-API-Key header)'; }
      }
    }
    function showLoggedOut() {
      $('auth-success-card').classList.add('hidden');
      $('tab-login').parentElement.classList.remove('hidden');
      $('tab-login').click();
    }
    function busy(btn, on) {
      if (!btn) return;
      btn.disabled = on;
      btn.style.opacity = on ? '0.6' : '';
    }

    async function doLogin() {
      hideAlert();
      const email = $('login-email').value.trim().toLowerCase();
      const password = $('login-password').value;
      if (!email || !password) return showAlert('error', 'Missing Information', 'Please provide both your email and password to sign in.');
      const btn = $('btn-login-submit');
      busy(btn, true);
      try {
        const res = await api('/api/auth/login', { method: 'POST', body: { email, password, remember: !!($('login-remember') || {}).checked } });
        saveSession(res.token, res.user);
        showAlert('success', 'Sign In Successful', 'Authenticating your session...');
        setTimeout(() => renderSuccess(res.user), 600);
      } catch (err) {
        if (err.status === 404) showAlert('error', 'Account Not Registered', err.message, email);
        else if (err.status === 401) showAlert('error', 'Incorrect Password', err.message);
        else showAlert('error', 'Sign In Failed', err.message);
      } finally { busy(btn, false); }
    }

    async function doRegister() {
      hideAlert();
      const name = $('reg-name').value.trim();
      const email = $('reg-email').value.trim().toLowerCase();
      const password = $('reg-password').value;
      const confirm = $('reg-confirm-password').value;
      if (!name || !email || !password) return showAlert('error', 'Incomplete Form', 'Please fill in all required fields.');
      if (password.length < 6) return showAlert('error', 'Weak Password', 'Password must be at least 6 characters long.');
      if (password !== confirm) return showAlert('error', 'Password Mismatch', 'Your confirmation password does not match.');
      if ($('reg-terms') && !$('reg-terms').checked) return showAlert('error', 'Terms Required', 'Please accept the Terms of Service to continue.');
      const btn = $('btn-register-submit');
      busy(btn, true);
      try {
        const res = await api('/api/auth/register', { method: 'POST', body: { name, email, password } });
        saveSession(res.token, res.user);
        showAlert('success', 'Registration Complete!', `Welcome ${res.user.name}! Your free tier with ${res.user.scansTotal} monthly scans has been activated.`);
        setTimeout(() => renderSuccess(res.user), 800);
      } catch (err) {
        if (err.status === 409) {
          showAlert('warning', 'Account Already Exists', err.message);
          setTimeout(() => { $('tab-login').click(); $('login-email').value = email; }, 1200);
        } else showAlert('error', 'Registration Failed', err.message);
      } finally { busy(btn, false); }
    }

    async function doSSO(provider) {
      if (cfg.oauth && cfg.oauth[provider]) {
        location.href = API + `/api/auth/oauth/${provider}/start`;
        return;
      }
      try {
        const res = await api('/api/auth/demo-sso', { method: 'POST', body: { provider } });
        saveSession(res.token, res.user);
        renderSuccess(res.user);
      } catch (err) { showAlert('error', 'Sign In Failed', err.message); }
    }

    async function doForgot() {
      const email = prompt('Enter your registered email to receive a password recovery link:', $('login-email').value || '');
      if (!email) return;
      try {
        await api('/api/auth/forgot', { method: 'POST', body: { email } });
        showAlert('success', 'Recovery Link Sent', `If an account exists for "${email}", instructions to reset your password have been dispatched.`);
      } catch (err) { showAlert('error', 'Could Not Send Link', err.message); }
    }

    async function doReset(token) {
      const pw = prompt('Choose a new password (at least 6 characters):');
      if (!pw) return;
      if (pw.length < 6) return showAlert('error', 'Weak Password', 'Password must be at least 6 characters long.');
      if (prompt('Confirm the new password:') !== pw) return showAlert('error', 'Password Mismatch', 'Your confirmation password does not match.');
      try {
        const res = await api('/api/auth/reset', { method: 'POST', body: { token, password: pw } });
        saveSession(res.token, res.user);
        renderSuccess(res.user);
      } catch (err) { showAlert('error', 'Reset Failed', err.message); }
    }

    // Capture-phase interception so the page's localStorage demo handlers never run
    window.addEventListener('submit', (e) => {
      const id = e.target && e.target.id;
      if (id !== 'form-login' && id !== 'form-register') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (id === 'form-login') doLogin(); else doRegister();
    }, true);

    window.addEventListener('click', (e) => {
      const t = e.target && e.target.closest ? e.target.closest('#btn-oauth-google, #btn-oauth-github, #link-forgot-pw, #btn-sign-out') : null;
      if (!t) return;
      if (t.id === 'btn-sign-out') {
        // Let the page's own handler reset the UI; just end the server session too
        const token = store.get(TOKEN_KEY);
        if (token) fetch(API + '/api/auth/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + token } }).catch(() => {});
        store.del(TOKEN_KEY);
        return;
      }
      e.preventDefault();
      e.stopImmediatePropagation();
      if (t.id === 'link-forgot-pw') doForgot();
      else doSSO(t.id === 'btn-oauth-google' ? 'google' : 'github');
    }, true);

    onReady(() => {
      const hash = new URLSearchParams(location.hash.slice(1));
      if (hash.get('sso') || hash.get('reset') || hash.get('sso_error')) history.replaceState(null, '', location.pathname + location.search);
      if (hash.get('sso')) store.set(TOKEN_KEY, hash.get('sso'));
      if (hash.get('sso_error')) showAlert('error', 'Sign In Failed', 'Single sign-on was cancelled or failed. Please try again.');
      if (hash.get('reset')) { doReset(hash.get('reset')); return; }

      const token = store.get(TOKEN_KEY);
      if (!token) {
        // A session from the old browser-only demo has no server account: sign it out
        if (store.get(SESSION_KEY)) { store.del(SESSION_KEY); showLoggedOut(); }
        return;
      }
      api('/api/auth/me')
        .then((res) => { saveSession(null, res.user); renderSuccess(res.user); })
        .catch((err) => {
          if (err.status === 401) { clearSession(); showLoggedOut(); showAlert('warning', 'Session Expired', 'Please sign in again.'); }
        });
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

  let linksPromise = null;
  function integrationLinks() {
    if (!linksPromise) {
      linksPromise = api('/api/integrations')
        .then((res) => Object.fromEntries((res.integrations || []).map((i) => [i.id, i.link])))
        .catch(() => ({}));
    }
    return linksPromise;
  }

  function openApp(appId) {
    if (appId === 'chrome') {
      // No browser extension exists; installing the SachScan app (PWA) is the real equivalent.
      if (window.triggerSachScanInstall) window.triggerSachScanInstall();
      return;
    }
    // Open a blank tab synchronously (keeps the click's popup permission), then point it at the link.
    const win = window.open('', '_blank');
    integrationLinks().then((links) => {
      const url = links[appId];
      if (!url) { if (win) win.close(); return; }
      if (win) { try { win.opener = null; } catch (e) { /* ignore */ } win.location.href = url; } else location.href = url;
    });
  }

  function initIntegrationCards() {
    integrationLinks(); // prefetch so the click feels instant
    // integrations.html cards carry data-app; index.html cards are identified by their title
    window.addEventListener('click', (e) => {
      const card = e.target && e.target.closest ? e.target.closest('.app-card[data-app], .card-integration-3d') : null;
      if (!card) return;
      const appId = card.getAttribute('data-app') || appFromText((card.querySelector('h3, h2') || card).textContent);
      if (appId) openApp(appId); // the page's own handler still runs (card highlight)
    });
    onReady(() => {
      document.querySelectorAll('.app-card[data-app], .card-integration-3d').forEach((c) => {
        c.style.cursor = 'pointer';
        const id = c.getAttribute('data-app') || appFromText((c.querySelector('h3, h2') || c).textContent);
        if (id) c.title = id === 'chrome' ? 'Install the SachScan app' : 'Open in ' + (c.querySelector('h3, h2') || c).textContent.trim();
      });
      // integrations.html?app=whatsapp -> highlight that card (from footer links)
      const want = new URLSearchParams(location.search).get('app');
      const target = want && document.querySelector(`.app-card[data-app="${CSS.escape(want)}"]`);
      if (target) {
        document.querySelectorAll('.app-card').forEach((c) => c.classList.remove('active-app'));
        target.classList.add('active-app');
        setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300);
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
  if (page === 'index' || page === 'integrations') initIntegrationCards();
  if (page === 'help') initHelp();
  if (page === 'login') initLogin();
})();
