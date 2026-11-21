/* SPPS notifications (phase 4): the first-sign-in "turn on notifications" pop-up and the Settings card.
   Works for athletes and guardians (portal session) and for practitioners, admins and the owner (workspace sign-in).
   No browser storage is used: what the person answered lives on the server. Messages are content-free. */
(function () {
  'use strict';
  const S = { scope: '', csrf: '', info: null, busy: false, started: 0 };
  const BASE = { portal: '/api/portal/push', workspace: '/api/v2/push' };
  const LABEL = { appointments: 'Appointments and reminders', programme_tasks: 'Programme and task updates', account_security: 'Account and security (always on)' };
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function call(path, method, body) {
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && S.csrf) headers['X-CSRF-Token'] = S.csrf;
    const r = await fetch(BASE[S.scope] + path, { method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body) });
    let data = {}; try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) { const err = new Error(data.detail || 'Request failed'); err.status = r.status; throw err; }
    return data;
  }

  const webSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const nativePlugin = () => (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications) || null;
  const supported = () => !!nativePlugin() || webSupported();

  function keyBytes(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function registration() {
    return navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => navigator.serviceWorker.ready);
  }

  async function enableWeb() {
    if (!S.info || !S.info.public_key) throw new Error('Notifications are not set up on this server yet.');
    const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notifications are blocked in this browser. You can allow them in the browser settings.');
    const reg = await registration();
    const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: keyBytes(S.info.public_key) });
    await call('/subscribe', 'POST', { platform: 'web', subscription: sub.toJSON() });
  }

  async function enableNative() {
    const plugin = nativePlugin();
    const perm = await plugin.requestPermissions();
    if (perm.receive !== 'granted') throw new Error('Notifications are blocked for this app. You can allow them in Android settings.');
    const token = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('The phone did not return a notification token.')), 15000);
      plugin.addListener('registration', (t) => { clearTimeout(timer); resolve(t.value); });
      plugin.addListener('registrationError', () => { clearTimeout(timer); reject(new Error('This phone could not register for notifications.')); });
      plugin.register();
    });
    await call('/subscribe', 'POST', { platform: 'android', subscription: { token } });
  }

  async function turnOn() {
    if (nativePlugin()) await enableNative(); else await enableWeb();
    await refresh();
  }

  async function turnOffHere() {
    if (webSupported() && !nativePlugin()) {
      const reg = await navigator.serviceWorker.getRegistration('/');
      const sub = reg && await reg.pushManager.getSubscription();
      if (sub) { const endpoint = sub.endpoint; await sub.unsubscribe(); await call('/unsubscribe', 'POST', { endpoint }); }
    }
    await refresh();
  }

  async function refresh() {
    if (!S.scope) return null;
    try {
      const d = await call('/state', 'GET');
      S.info = d && d.prompt && Array.isArray(d.preferences) && Array.isArray(d.devices) ? d : null;
    } catch (e) { S.info = null; }
    paintCards();
    return S.info;
  }

  // ------------------------------------------------------------------ first-sign-in pop-up
  function onEscape(e) {
    if (e.key !== 'Escape') return;
    const later = document.querySelector('#spps-push-modal [data-push-act="later"]');
    if (later) later.click();
  }
  function closeModal() {
    document.removeEventListener('keydown', onEscape);
    const m = document.getElementById('spps-push-modal'); if (m) m.remove();
  }

  function showPrompt() {
    if (document.getElementById('spps-push-modal')) return;
    const wrap = document.createElement('div');
    wrap.id = 'spps-push-modal';
    wrap.setAttribute('role', 'dialog'); wrap.setAttribute('aria-modal', 'true'); wrap.setAttribute('aria-labelledby', 'spps-push-title');
    wrap.style.cssText = 'position:fixed;inset:0;z-index:9999;display:grid;place-items:center;background:rgba(10,20,30,.55);padding:16px';
    wrap.innerHTML = `<div style="background:var(--surface,#fff);color:var(--text,#14202b);max-width:420px;width:100%;border-radius:14px;padding:22px;box-shadow:0 12px 40px rgba(0,0,0,.3)">
      <h2 id="spps-push-title" style="margin:0 0 8px;font-size:1.2rem">Turn on notifications?</h2>
      <p style="margin:0 0 10px">Get a short alert when an appointment is requested, confirmed or about to start, and when something needs your attention.</p>
      <p style="margin:0 0 16px;font-size:.9rem;opacity:.8">Alerts never show names, times or health details. You can change this any time in Settings.</p>
      <p data-push-error role="alert" style="color:#b3261e;margin:0 0 10px;display:none"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap">
        <button type="button" data-push-act="later" class="btn small">Not now</button>
        <button type="button" data-push-act="allow" class="btn small primary">Turn on notifications</button></div></div>`;
    document.body.appendChild(wrap);
    const allow = wrap.querySelector('[data-push-act="allow"]');
    allow.focus();
    document.addEventListener('keydown', onEscape);
  }

  async function answer(a) {
    try { await call('/prompt', 'POST', { answer: a }); } catch (e) { /* the prompt may simply show again later */ }
  }

  document.addEventListener('click', async (event) => {
    const el = event.target.closest('[data-push-act]');
    if (!el || S.busy) return;
    const act = el.dataset.pushAct;
    const modal = document.getElementById('spps-push-modal');
    const err = (m) => {
      const box = (modal && modal.querySelector('[data-push-error]')) || (el.closest('[data-push-card]') || document).querySelector('[data-push-error]');
      if (box) { box.textContent = m; box.style.display = m ? 'block' : 'none'; }
    };
    S.busy = true; el.disabled = true;
    try {
      if (act === 'later') { await answer('NOT_NOW'); closeModal(); }
      else if (act === 'allow' || act === 'on') { await turnOn(); closeModal(); }
      else if (act === 'off') { await turnOffHere(); }
      else if (act === 'remove') { await call('/unsubscribe', 'POST', { device_id: Number(el.dataset.id) }); await refresh(); }
    } catch (e) { err(e.message || 'Something went wrong.'); }
    finally { S.busy = false; el.disabled = false; }
  });

  document.addEventListener('change', async (event) => {
    const el = event.target.closest('[data-push-pref]');
    if (!el) return;
    try { await call('/preferences', 'POST', { category: el.dataset.pushPref, enabled: el.checked }); await refresh(); }
    catch (e) { el.checked = !el.checked; }
  });

  // ------------------------------------------------------------------ Settings / Account card
  function cardHtml() {
    return '<section class="card" data-push-card aria-live="polite"><h2>Notifications</h2><p class="muted">Loading…</p></section>';
  }

  function fillCard(card) {
    const info = S.info;
    if (!S.scope) { card.innerHTML = ''; return; }
    if (!info) { card.innerHTML = '<h2>Notifications</h2><p class="muted">Notifications are not switched on for this site.</p>'; return; }
    const here = supported() ? (Notification.permission === 'denied' && !nativePlugin()
      ? '<p class="muted">Notifications are blocked in this browser. Allow them in the browser settings, then come back.</p>'
      : `<div class="inline-actions"><button type="button" class="btn small primary" data-push-act="on">Turn on for this device</button>
         <button type="button" class="btn small" data-push-act="off">Turn off for this device</button></div>`)
      : '<p class="muted">This browser cannot show notifications. You will still receive e-mail. On iPhone, add SPPS to the Home Screen first.</p>';
    const prefs = info.preferences.map((p) => `<label style="display:flex;gap:8px;align-items:center;margin:6px 0">
      <input type="checkbox" data-push-pref="${esc(p.category)}" ${p.enabled ? 'checked' : ''} ${p.locked ? 'disabled' : ''}>
      <span>${esc(LABEL[p.category] || p.category)}</span></label>`).join('');
    const devices = info.devices.length ? `<ul style="list-style:none;padding:0;margin:8px 0">${info.devices.map((d) =>
      `<li style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin:4px 0">
        <span>${d.platform === 'android' ? 'Android app' : 'Web browser'} · added ${esc(new Date(d.created_at).toLocaleDateString())}</span>
        <button type="button" class="btn small" data-push-act="remove" data-id="${Number(d.device_id)}">Remove</button></li>`).join('')}</ul>`
      : '<p class="muted">No device is receiving notifications yet.</p>';
    card.innerHTML = `<h2>Notifications</h2>
      <p class="muted">Alerts are short and never contain names, times or health details. E-mail is still sent for important updates.</p>
      ${here}<p data-push-error role="alert" style="color:#b3261e;display:none"></p>
      <h3 style="margin:14px 0 4px;font-size:1rem">What to be told about</h3>${prefs}
      <h3 style="margin:14px 0 4px;font-size:1rem">Your devices</h3>${devices}`;
  }

  function paintCards() { document.querySelectorAll('[data-push-card]').forEach(fillCard); }

  new MutationObserver(() => {
    document.querySelectorAll('[data-push-card]').forEach((c) => { if (!c.dataset.pushFilled || c.textContent.trim() === 'NotificationsLoading…') { c.dataset.pushFilled = '1'; fillCard(c); } });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ------------------------------------------------------------------ lifecycle
  async function start(opts) {
    if (!opts || !BASE[opts.scope]) return;
    S.scope = opts.scope; S.csrf = opts.csrf || ''; S.started = Date.now();
    const info = await refresh();
    if (!info) return;
    if (info.prompt.should_prompt && supported() && (nativePlugin() || Notification.permission !== 'denied')) showPrompt();
  }
  function stop() { S.scope = ''; S.csrf = ''; S.info = null; closeModal(); paintCards(); }

  window.SPPSPush = { start, stop, cardHtml, refresh };
})();
