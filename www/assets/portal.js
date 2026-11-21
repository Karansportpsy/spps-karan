/* SPPS athlete & guardian portal (Stage 13A.8). Shares the FastAPI backend with the
 * practitioner workspace and the Capacitor Android app. Security is enforced by the
 * server and PostgreSQL RLS; this file only renders what the API returns. */
'use strict';
(() => {
  const root = document.getElementById('portal');
  const toastEl = document.getElementById('toast');
  const theme = (() => { try { return localStorage.getItem('modelc-theme') || 'original'; } catch { return 'original'; } })();
  document.body.dataset.theme = theme;
  const state = { me: null, csrf: '', view: 'signin', data: {}, preview: null, message: '', freeAccess: false, directoryBooking: false, book: null };
  const esc = (v = '') => String(v ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const when = s => s ? new Date(s).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const SCOPES = {
    appointment_metadata: 'See appointment times', booking_management: 'Request and manage bookings',
    assigned_tasks: 'See assigned programme tasks', own_submissions: 'See task submissions',
    proxy_submission: 'Submit tasks on their behalf', released_assessment_summary: 'See released assessment summaries',
    consent_management: 'Give or withdraw consent for them',
  };
  const PURPOSE_LABELS = {
    CASE_LINK: 'Link with practitioner', APPOINTMENT_BOOKING: 'Appointment booking', VIDEO_CONSULTATION: 'Video consultation',
    PROGRAMME_PARTICIPATION: 'Programme participation', DATA_SHARING_SUMMARY: 'Summary sharing',
    PLATFORM_TERMS: 'Platform terms', PRIVACY_NOTICE: 'Privacy notice',
  };

  function notify(msg) { toastEl.textContent = msg; toastEl.classList.add('visible'); setTimeout(() => toastEl.classList.remove('visible'), 5000); }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && state.csrf) headers['X-CSRF-Token'] = state.csrf;
    let r;
    try { r = await fetch(path, { method, headers, credentials: 'same-origin', cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw Error('The SPPS server could not be reached. Check your connection and try again.'); }
    let data = {}; try { data = await r.json(); } catch { /* empty */ }
    if (!r.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? 'Please check the highlighted details.' : 'Request failed';
      const err = Error(detail); err.status = r.status; throw err;
    }
    return data;
  }

  const has = role => state.me?.roles?.includes(role);
  const btn = (label, action, cls = 'btn', extra = '') => `<button type="button" class="${cls}" data-action="${action}" ${extra}>${label}</button>`;
  const badge = (text, good) => `<span class="badge ${good ? 'good' : ''}">${esc(text)}</span>`;
  const field = (label, input) => `<div class="field"><label>${label}${input}</label></div>`;

  // ------------------------------------------------------------------ signed out
  function authView() {
    const tabs = [['signin', 'Sign in'], ['register', 'Create account'], ['verify', 'Verify email'], ['forgot', 'Forgot password']]
      .map(([v, l]) => `<button type="button" class="btn small ${state.view === v ? 'primary' : ''}" data-action="view" data-view="${v}" aria-pressed="${state.view === v}">${l}</button>`).join('');
    const forms = {
      signin: `<form data-form="signin">${field('Email', '<input name="email" type="email" autocomplete="username" required maxlength="250">')}
        ${field('Password', '<input name="password" type="password" autocomplete="current-password" required maxlength="200">')}
        <button class="btn primary" type="submit">Sign in</button></form>`,
      register: `<form data-form="register">
        <div class="field"><label>I am registering as<select name="role" required><option value="athlete">An athlete (18 or over)</option><option value="guardian">A parent or guardian</option></select></label></div>
        ${field('Your name', '<input name="display_name" required minlength="2" maxlength="120" autocomplete="name">')}
        ${field('Email', '<input name="email" type="email" required maxlength="250" autocomplete="email">')}
        ${field('Password (at least 12 characters)', '<input name="password" type="password" required minlength="12" maxlength="200" autocomplete="new-password">')}
        <label class="checkline"><input type="checkbox" name="adult_attested" required> I confirm I am 18 or over. Athletes under 18 are added by a verified parent or guardian.</label>
        <label class="checkline"><input type="checkbox" name="terms_accepted" required> I accept the <a href="/terms" target="_blank" rel="noopener">terms</a> and <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</label>
        <button class="btn primary" type="submit">Create account</button></form>`,
      verify: `<form data-form="verify">${field('Code from the link in your email (only if the link did not open)', '<input name="token" required minlength="20" maxlength="200" autocomplete="one-time-code">')}
        <button class="btn primary" type="submit">Verify email</button></form>
        <form data-form="resend" style="margin-top:14px">${field('Email (to send the link again)', '<input name="email" type="email" required maxlength="250">')}<button class="btn small" type="submit">Resend link</button></form>`,
      forgot: `<form data-form="forgot">${field('Email', '<input name="email" type="email" required maxlength="250">')}<button class="btn primary" type="submit">Send reset link</button></form>`,
      reset: `<form data-form="reset"><input type="hidden" name="token" value="${esc(state.data.resetToken || '')}">
        ${field('Reset code', `<input name="token_visible" value="${esc(state.data.resetToken || '')}" required minlength="20" maxlength="200">`)}
        ${field('New password (at least 12 characters)', '<input name="new_password" type="password" required minlength="12" maxlength="200" autocomplete="new-password">')}
        <button class="btn primary" type="submit">Set new password</button></form>`,
    };
    return `<main class="shell" style="max-width:560px;padding:32px 16px">
      <span class="eyebrow">SPPS · Athlete &amp; guardian portal</span>
      <h1 style="margin:6px 0 10px">Your sport psychology portal</h1>
      <p class="muted">See what your practitioner shares with you, manage consent and, for parents and guardians, act for a young athlete once your authority is verified.</p>
      <div class="inline-actions" role="tablist" style="margin:16px 0">${tabs}</div>
      ${state.message ? `<div class="card" data-portal-message><p>${esc(state.message)}</p></div>` : ''}
      <div class="card" data-view-panel="${state.view}">${forms[state.view] || forms.signin}</div>
      <p class="muted small" style="margin-top:18px">SPPS Version 1.0 - Free. Accounts require practitioner/admin approval. Not an emergency service. Practitioners sign in at <a href="/studio">the practitioner workspace</a>.</p>
    </main>`;
  }

  // ------------------------------------------------------------------ booking from the directory (phase 2)
  // /portal?practitioner=<id> opens this flow: pick a service, then a time, then a short note, then send.
  // The practitioner to book stays in the address (?practitioner=N), never in browser storage (portal contract).
  let bookPractitioner = null;
  const clearBookPractitioner = () => { bookPractitioner = null; if (/[?&]practitioner=/.test(location.search)) history.replaceState({}, '', '/portal'); };
  function bookSubjects() {
    const out = [];
    if (has('athlete')) out.push([state.me.person_id, `${state.me.display_name} (me)`]);
    for (const d of state.data.dependants || []) {
      const scopes = d.authority?.scopes || d.authority?.scope || [];
      const list = Array.isArray(scopes) ? scopes : String(scopes).split(',');
      if (d.authority?.status === 'VERIFIED' && list.includes('booking_management')) out.push([d.person_id, d.display_name]);
    }
    return out;
  }
  function bookingCard() {
    const b = state.book; if (!b) return '';
    if (!state.directoryBooking) return `<section class="card" data-section="directory-booking"><h2>Request an appointment</h2><p class="muted">Requesting appointments from the directory is not switched on yet.</p>${btn('Close', 'book-close', 'btn small')}</section>`;
    if (b.error) return `<section class="card" data-section="directory-booking"><h2>Request an appointment</h2><p>${esc(b.error)}</p>${btn('Close', 'book-close', 'btn small')}</section>`;
    if (!b.card) return `<section class="card" data-section="directory-booking"><p class="muted">Loading…</p></section>`;
    if (b.sent) return `<section class="card" data-section="directory-booking"><h2>Request sent</h2><p>${esc(b.sent.message)}</p><p class="small">${esc(b.sent.service)} · ${esc(new Date(b.sent.starts_at).toLocaleString('en-IN'))}</p><p class="muted small">${esc(b.sent.notice || '')}</p>${btn('Done', 'book-close', 'btn small primary')}</section>`;
    const c = b.card, services = c.services || [];
    const subjects = bookSubjects();
    const head = `<h2>Request an appointment with ${esc(c.display_name)}</h2><p class="muted small">Until they approve, the practitioner sees only the athlete's display name, the service and the time. Appointments are free.</p>`;
    if (!services.length) return `<section class="card" data-section="directory-booking">${head}<p>This practitioner has not listed any services yet.</p>${btn('Close', 'book-close', 'btn small')}</section>`;
    if (!subjects.length) return `<section class="card" data-section="directory-booking">${head}<p>${has('guardian') ? 'Add the young athlete below and wait until your guardian authority for them, including “Request and manage bookings”, is verified. Then you can request an appointment for them.' : 'Only an athlete account (18 or over) or a verified guardian can request appointments.'}</p>${btn('Close', 'book-close', 'btn small')}</section>`;
    const svc = services.find(x => String(x.service_id) === String(b.service_id));
    const step1 = `<form data-form="book-service">
        <fieldset class="stack"><legend>1. Choose a service</legend>${services.map(x => `<label class="checkline"><input type="radio" name="service_id" value="${x.service_id}" required ${String(b.service_id) === String(x.service_id) ? 'checked' : ''}> <span><b>${esc(x.name)}</b> · ${Number(x.duration_minutes)} min · ${x.mode === 'in_person' ? 'In person' : 'Online'}${x.short_description ? `<br><span class="small muted">${esc(x.short_description)}</span>` : ''}</span></label>`).join('')}</fieldset>
        <div class="field"><label>For<select name="subject_person_id">${subjects.map(([id, n]) => `<option value="${id}" ${String(b.subject) === String(id) ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label></div>
        <button class="btn" type="submit">Show available times</button></form>`;
    let step2 = '';
    if (svc && b.slots) {
      step2 = b.slots.length ? `<form data-form="book-send" style="margin-top:14px">
        <div class="field"><label>2. Choose a time<select name="slot_id" required>${b.slots.map(s => `<option value="${s.slot_id}">${esc(new Date(s.starts_at).toLocaleString('en-IN'))}</option>`).join('')}</select></label></div>
        ${field('3. A short note (optional, practical details only; leave out health or clinical information)', '<textarea name="note" maxlength="300" rows="2"></textarea>')}
        <input name="website" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px" value="">
        <label class="checkline"><input type="checkbox" name="consent_link" required> I consent to ${esc(c.display_name)} receiving this request and, once they approve it, being linked with ${b.subject === state.me.person_id ? 'me' : 'this athlete'} on SPPS. I can disconnect at any time.</label>
        <label class="checkline"><input type="checkbox" name="consent_booking" required> I consent to this appointment booking.</label>
        ${svc.mode === 'online' ? '<label class="checkline"><input type="checkbox" name="consent_video" required> I consent to the session taking place by video.</label>' : ''}
        <div class="inline-actions"><button class="btn primary" type="submit">4. Send request</button>${btn('Cancel', 'book-close', 'btn small')}</div></form>`
        : '<p class="muted" style="margin-top:14px">No open times for this service at the moment. Please check again later.</p>';
    }
    return `<section class="card" data-section="directory-booking">${head}${step1}${step2}</section>`;
  }
  async function openBooking(pid) {
    state.book = { pid, card: null };
    render();
    try { state.book.card = await api(`/api/v2/public/directory/${encodeURIComponent(pid)}`); }
    catch (err) { state.book.error = err.status === 404 ? 'This practitioner is not listed in the directory.' : err.message; }
    render();
  }

  // ------------------------------------------------------------------ signed in
  function linksCard() {
    const links = state.data.links || [];
    const rows = links.map(l => `<tr data-link-id="${l.link_id}"><td>${esc(l.practitioner_name)}</td><td>${esc(l.subject_display_name)}</td>
      <td>${badge(l.active ? 'Active' : l.status.replace(/_/g, ' ').toLowerCase(), l.active)}</td>
      <td>${['ACTIVE', 'PRACTITIONER_REVIEW', 'CONSENT_PENDING', 'REQUESTED'].includes(l.status) ? btn('Disconnect', 'revoke-link', 'btn small', `data-id="${l.link_id}"`) : ''}</td></tr>`).join('');
    const subjects = [];
    if (has('athlete')) subjects.push([state.me.person_id, `${state.me.display_name} (me)`]);
    for (const d of state.data.dependants || []) if (d.authority?.status === 'VERIFIED') subjects.push([d.person_id, d.display_name]);
    const p = state.preview;
    const connect = subjects.length ? `<form data-form="${p ? 'connect' : 'preview'}" style="margin-top:14px">
        ${field('Invitation code from your practitioner', `<input name="code" required minlength="12" maxlength="20" value="${esc(p?.code || '')}" ${p ? 'readonly' : ''} autocomplete="off" placeholder="ABCD-EFGH-JKLM">`)}
        <div class="field"><label>Connect for<select name="subject_person_id">${subjects.map(([id, n]) => `<option value="${id}" ${String(p?.subject) === String(id) ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label></div>
        ${p ? `<div class="card" data-preview-practitioner="${p.practitioner_id}"><p>This code was issued by <strong>${esc(p.practitioner_name)}</strong>.</p>
          <label class="checkline"><input type="checkbox" name="consent" required> I consent to linking with ${esc(p.practitioner_name)}. They can share programme tasks and released summaries; I can disconnect at any time. Private session notes are not shared.</label></div>
          <div class="inline-actions"><button class="btn primary" type="submit">Connect</button>${btn('Cancel', 'cancel-preview', 'btn small')}</div>`
          : '<button class="btn primary" type="submit">Check code</button>'}
      </form>` : '<p class="muted small">Connecting becomes available once a dependant\'s guardian authority is verified.</p>';
    return `<section class="card" data-section="links"><h2>Practitioner connections</h2>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Practitioner</th><th>For</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">No connections yet.</td></tr>'}</tbody></table></div>${connect}</section>`;
  }

  function dependantsCard() {
    const deps = state.data.dependants || [];
    const rows = deps.map(d => {
      const a = d.authority;
      let actions = '';
      if (!a || ['REVOKED', 'REJECTED', 'EXPIRED'].includes(a.status)) actions = btn('Request authority', 'show-authority', 'btn small primary', `data-id="${d.person_id}"`);
      if (a && a.status === 'PENDING_EVIDENCE') actions = btn('Add evidence', 'show-evidence', 'btn small primary', `data-id="${a.id}"`);
      if (a && ['PENDING_VERIFICATION', 'VERIFIED', 'PENDING_EVIDENCE'].includes(a.status)) actions += btn('Withdraw authority', 'revoke-authority', 'btn small', `data-id="${a.id}"`);
      if (a && a.status === 'VERIFIED') actions += btn('Consent history', 'show-consents', 'btn small', `data-id="${d.person_id}"`);
      return `<tr data-dependant-id="${d.person_id}"><td>${esc(d.display_name)}</td><td>${a ? badge(a.status.replace(/_/g, ' ').toLowerCase(), a.status === 'VERIFIED') : badge('no authority')}</td>
        <td>${a?.expires_at ? `Until ${when(a.expires_at)}` : ''}</td><td>${actions}</td></tr>`;
    }).join('');
    const scopeBoxes = Object.entries(SCOPES).map(([k, v]) => `<label class="checkline"><input type="checkbox" name="scope" value="${k}" ${['appointment_metadata', 'assigned_tasks', 'consent_management'].includes(k) ? 'checked' : ''}> ${esc(v)}</label>`).join('');
    const authForm = state.data.authorityFor ? `<form data-form="authority" class="card" style="margin-top:14px"><input type="hidden" name="dependant_person_id" value="${state.data.authorityFor}">
        <h3 style="margin-top:0">Request guardian authority</h3><p class="muted small">An independent SPPS reviewer checks your evidence before any access is granted.</p>
        ${scopeBoxes}${field('Evidence reference (e.g. the document you will show the reviewer)', '<input name="evidence_reference" maxlength="160" placeholder="Optional now — required before review">')}
        <div class="inline-actions"><button class="btn primary" type="submit">Submit request</button>${btn('Cancel', 'cancel-forms', 'btn small')}</div></form>` : '';
    const evidenceForm = state.data.evidenceFor ? `<form data-form="evidence" class="card" style="margin-top:14px"><input type="hidden" name="authority_id" value="${state.data.evidenceFor}">
        ${field('Evidence reference', '<input name="evidence_reference" required minlength="3" maxlength="160">')}
        <div class="inline-actions"><button class="btn primary" type="submit">Send for review</button>${btn('Cancel', 'cancel-forms', 'btn small')}</div></form>` : '';
    return `<section class="card" data-section="dependants"><h2>Young athletes I support</h2>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Authority</th><th>Valid</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">No dependants added.</td></tr>'}</tbody></table></div>
      ${authForm}${evidenceForm}
      <form data-form="dependant" style="margin-top:14px">${field('Add a young athlete (name you use for them)', '<input name="display_name" required minlength="2" maxlength="120">')}<button class="btn small" type="submit">Add</button></form></section>`;
  }

  function consentCard() {
    const c = state.data.consents;
    if (!c) return '';
    const rows = c.items.map(e => `<tr><td>${esc(PURPOSE_LABELS[e.purpose] || e.purpose)}</td><td>${e.practitioner_id ? esc(state.data.names?.[e.practitioner_id] || `Practitioner ${e.practitioner_id}`) : 'SPPS'}</td>
      <td>${badge(e.decision.toLowerCase(), e.decision === 'GRANTED')}${e.current ? '' : ' <span class="muted small">(superseded)</span>'}</td><td>${when(e.recorded_at)}${e.by_guardian ? ' · by guardian' : ''}</td>
      <td>${e.current && e.decision === 'GRANTED' ? btn('Withdraw', 'withdraw-consent', 'btn small', `data-purpose="${e.purpose}" data-practitioner="${e.practitioner_id ?? ''}" data-subject="${c.subject}"`) : ''}</td></tr>`).join('');
    return `<section class="card" data-section="consents"><div class="between"><h2>Consent history · ${esc(c.label)}</h2>${btn('Close', 'close-consents', 'btn small')}</div>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Purpose</th><th>With</th><th>Decision</th><th>When</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="muted">No consent decisions recorded.</td></tr>'}</tbody></table></div>
      <p class="muted small">Withdrawing consent to link immediately stops that practitioner's portal access. Records your practitioner must keep under their professional obligations are retained.</p></section>`;
  }

  const BOOKING_LABEL = {
    PENDING_APPROVAL: 'Waiting for practitioner approval', APPROVED_AWAITING_PAYMENT: 'Approved — payment due',
    CONFIRMED: 'Confirmed', DECLINED: 'Declined', RESCHEDULE_PROPOSED: 'New time proposed', EXPIRED: 'Expired',
    CANCELLED: 'Cancelled', REFUND_PENDING: 'Refund in progress', REFUNDED: 'Refunded', COMPLETED: 'Completed',
  };
  const NOTE_LABEL = {
    'booking.approved_payment_due': 'A booking was approved — payment is due', 'booking.confirmed': 'A booking is confirmed',
    'booking.declined': 'A booking request was declined', 'booking.expired': 'A booking expired',
    'booking.reschedule_proposed': 'Your practitioner proposed a new time', 'booking.refunded': 'A refund was issued',
    'booking.cancelled_by_practitioner': 'Your practitioner cancelled a booking',
    'booking.payment_refund_required': 'A late payment will be refunded',
    'programme.assigned': 'You have a new programme to review', 'programme.task_added': 'A task was added to your programme',
    'programme.feedback': 'Your practitioner left feedback', 'programme.paused': 'A programme was paused',
    'programme.accepted': 'A programme was resumed', 'programme.completed': 'A programme was completed',
    'programme.discontinued': 'A programme was discontinued', 'summary.released': 'Your practitioner shared a summary',
  };
  const rupees = p => `₹${(Number(p || 0) / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
  const dt = s => s ? new Date(s).toLocaleString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short' }) : '—';

  function portalVideo(b) {
    const now = Date.now();
    return b.can_manage && b.status === 'CONFIRMED' && b.video_requested && !b.video_closed
      && now >= Date.parse(b.starts_at) - 10 * 60000 && now <= Date.parse(b.ends_at) + 15 * 60000
      ? btn('Join video', 'join-video', 'btn small primary', `data-id="${b.booking_id}"`) : '';
  }
  setInterval(() => {
    for (const el of root.querySelectorAll('[data-portal-video]')) {
      const b = (state.data.bookings || []).find(x => String(x.booking_id) === el.dataset.portalVideo);
      if (b) { const html = portalVideo(b); if (el.innerHTML !== html) el.innerHTML = html; }
    }
  }, 1000);

  function appointmentsCard() {
    const active = (state.data.links || []).filter(l => l.active);
    const bookings = state.data.bookings || [];
    const rows = bookings.map(b => {
      let actions = '';
      actions += `<span data-portal-video="${b.booking_id}">${portalVideo(b)}</span>`;
      if (b.status === 'CONFIRMED' && b.video_closed) actions += '<span class="muted small" data-video-ended>Video ended</span>';
      if (!state.freeAccess && b.can_manage && b.status === 'APPROVED_AWAITING_PAYMENT') actions += btn(`Pay ${rupees(b.amount_paise)}`, 'pay-booking', 'btn small primary', `data-id="${b.booking_id}"`);
      if (b.can_manage && b.status === 'RESCHEDULE_PROPOSED' && Date.parse(b.starts_at) > Date.now()) actions += btn('Accept new time', 'reschedule-yes', 'btn small primary', `data-id="${b.booking_id}"`) + btn('Decline new time', 'reschedule-no', 'btn small', `data-id="${b.booking_id}"`);
      if (b.can_manage && ['PENDING_APPROVAL', 'APPROVED_AWAITING_PAYMENT', 'CONFIRMED'].includes(b.status)) actions += btn('Cancel', 'cancel-booking', 'btn small', `data-id="${b.booking_id}" data-paid="${b.paid}"`);
      const due = b.action_deadline && ['PENDING_APPROVAL', 'APPROVED_AWAITING_PAYMENT', 'RESCHEDULE_PROPOSED'].includes(b.status)
        ? `<br><span class="muted small">by ${esc(dt(b.action_deadline))}</span>` : '';
      const why = b.decision_reason ? `<br><span class="muted small">${esc(b.decision_reason)}</span>` : '';
      return `<tr data-booking-id="${b.booking_id}"><td data-label="Appointment">${esc(b.service_name || 'Appointment')}<br>${esc(dt(b.starts_at))} – ${esc(dt(b.ends_at))}<br><span class="small muted">${b.video_requested ? 'Online' : 'In person'} · local time</span></td><td data-label="For">${esc(b.subject_display_name)}</td>
        <td data-label="Status">${badge(BOOKING_LABEL[b.status] || b.status, b.status === 'CONFIRMED')}${due}${why}</td><td data-label="Actions">${actions}</td></tr>`;
    }).join('');
    const checkout = '';
    const slots = state.data.slots;
    const pickLink = active.length ? `<form data-form="load-slots" style="margin-top:14px"><div class="field"><label>Book with<select name="link_id">${active.map(l => `<option value="${l.link_id}" ${String(slots?.link) === String(l.link_id) ? 'selected' : ''}>${esc(l.practitioner_name)} — for ${esc(l.subject_display_name)}</option>`).join('')}</select></label></div><button class="btn small" type="submit">Show available times</button></form>`
      : '<p class="muted small">Connect with a practitioner to request appointments.</p>';
    let slotForm = '';
    if (slots && slots.items.length) {
      slotForm = `<form data-form="request-booking" class="card" style="margin-top:12px"><input type="hidden" name="link_id" value="${esc(slots.link)}">
        <div class="field"><label>Time<select name="slot_id" required>${slots.items.map(s => `<option value="${s.slot_id}">${esc(dt(s.starts_at))} · ${s.duration_minutes} min${state.freeAccess?' · Free appointment':' · '+esc(rupees(s.amount_paise))}</option>`).join('')}</select></label></div>
        ${field('Anything your practitioner should know (optional, not for emergencies)', '<input name="note" maxlength="300">')}
        <label class="checkline"><input type="checkbox" name="video_requested"> Hold this session by secure video</label>
        <label class="checkline"><input type="checkbox" name="booking_consent" required> I consent to booking appointments with this practitioner. No payment is required in SPPS Version 1.0 - Free.</label>
        <label class="checkline"><input type="checkbox" name="video_consent"> I consent to video consultations (needed for video sessions).</label>
        <button class="btn primary" type="submit">Request appointment</button></form>`;
    } else if (slots) {
      slotForm = '<p class="muted small" style="margin-top:12px">No open times right now.</p>';
    }
    const notes = (state.data.notifications || []).slice(0, 6).map(n => `<li class="${n.read ? 'muted' : ''}">${esc(NOTE_LABEL[n.kind] || n.kind)} · <span class="small">${esc(dt(n.created_at))}</span></li>`).join('');
    return `<section class="card" data-section="appointments"><h2>My appointments</h2>${checkout}
      <div class="table-wrap"><table class="data-table"><thead><tr><th>When</th><th>For</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="muted">No appointments yet.</td></tr>'}</tbody></table></div>${pickLink}${slotForm}
      ${notes ? `<h3>Updates</h3><ul data-notifications>${notes}</ul>` : ''}</section>`;
  }

  function programmeCard() {
    const data = state.data.participation || { programmes: [], summaries: [] };
    if (!data.programmes.length && !data.summaries.length) return '';
    const names = {};
    for (const l of state.data.links || []) names[l.practitioner_id] = l.practitioner_name;
    const progs = data.programmes.map(p => {
      const respond = p.status === 'ASSIGNED' && p.can_act ? `<form data-form="programme-respond" data-id="${p.id}" class="card" style="margin-top:8px">
          <label class="checkline"><input type="checkbox" name="consent" required> I agree to take part in this programme with ${esc(names[p.practitioner_id] || 'my practitioner')}. I can stop at any time.</label>
          <div class="inline-actions"><button class="btn small primary" type="submit" name="decision" value="accept">Accept programme</button><button class="btn small" type="submit" name="decision" value="decline" formnovalidate>Decline</button></div></form>` : '';
      const tasks = p.tasks.map(t => {
        const open = p.status === 'ACCEPTED' && p.can_act;
        const history = (t.submissions || []).slice(0, 3).map(s => `<li>${s.completed ? 'Completed' : 'Not completed'}${s.self_rating !== null ? ` · ${s.self_rating}/10` : ''} · ${esc(dt(s.submitted_at))}${(s.feedback || []).map(f => `<br><span class="small"><strong>Feedback:</strong> ${esc(f)}</span>`).join('')}</li>`).join('');
        return `<div class="card" data-portal-task="${t.id}"><div class="between"><strong>${esc(t.title)}</strong>${badge(t.frequency.replace('_', ' '))}</div>
          ${t.instructions ? `<p class="small">${esc(t.instructions)}</p>` : ''}<p class="muted small">${t.submission_count} check-in(s) so far</p>
          ${history ? `<ul>${history}</ul>` : ''}
          ${open ? `<form data-form="task-submit" data-id="${t.id}"><div class="field-grid"><label class="checkline"><input type="checkbox" name="completed" checked> I did this</label>
            <div class="field"><label>How did it go? (0–10)<input name="self_rating" type="number" min="0" max="10"></label></div></div>
            ${field('Reflection (optional)', '<input name="reflection" maxlength="1000">')}<button class="btn small primary" type="submit">Log check-in</button></form>` : ''}</div>`;
      }).join('');
      return `<section class="card" data-portal-programme="${p.id}"><span class="eyebrow">${esc(names[p.practitioner_id] || 'Practitioner')} · ${esc(p.status.toLowerCase())}</span>
        <h3 style="margin:4px 0">${esc(p.title)}</h3>${p.goal ? `<p class="small">${esc(p.goal)}</p>` : ''}${respond}${tasks}</section>`;
    }).join('');
    const sums = data.summaries.map(m => `<div class="card" data-portal-summary="${m.id}"><span class="eyebrow">${esc(m.kind.replace('_', ' '))} · ${esc(names[m.practitioner_id] || 'Practitioner')} · ${esc(dt(m.released_at))}</span>
      <h3 style="margin:4px 0">${esc(m.title)}</h3><p>${esc(m.body)}</p></div>`).join('');
    return `<section class="card" data-section="programmes"><h2>My programme</h2>${progs}${sums ? `<h3>Shared by your practitioner</h3>${sums}` : ''}</section>`;
  }

  function reviewCard() {
    const q = state.data.queue || [];
    const rows = q.map(x => `<tr data-authority-id="${x.authority_id}"><td>${esc(x.guardian_display_name)}<br><span class="muted small">${esc(x.guardian_email)}</span></td>
      <td>${esc(x.dependant_display_name)}</td><td>${x.scopes.map(s => esc(SCOPES[s] || s)).join('<br>')}</td><td>${esc(x.evidence_reference)}</td>
      <td><form data-form="decision" data-id="${x.authority_id}">${field('Reason', '<input name="reason" required minlength="5" maxlength="240">')}
        ${field('Valid for (days)', '<input name="valid_days" type="number" min="1" max="730" value="365">')}
        <div class="inline-actions"><button class="btn small primary" type="submit" name="decision" value="VERIFIED">Verify</button><button class="btn small" type="submit" name="decision" value="REJECTED">Reject</button></div></form></td></tr>`).join('');
    return `<section class="card" data-section="review"><h2>Guardian authority review queue</h2>
      <p class="muted small">You cannot review requests you made. Decisions are audited.</p>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Guardian</th><th>Young athlete</th><th>Requested scope</th><th>Evidence</th><th>Decision</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="muted">Nothing awaiting review.</td></tr>'}</tbody></table></div></section>`;
  }

  function homeView() {
    const me = state.me;
    return `<main class="shell" style="padding:24px 16px;display:grid;gap:18px">
      <div class="between" style="flex-wrap:wrap;gap:12px"><div><span class="eyebrow">SPPS portal · ${me.roles.map(esc).join(' · ')}</span><h1 style="margin:4px 0" data-portal-user="${me.account_id}">${esc(me.display_name)}</h1><span class="muted small">${esc(me.email)}</span></div>
        <div class="inline-actions">${btn('Account', 'toggle-account', 'btn small')}${btn('Sign out', 'logout', 'btn small primary')}</div></div>
      ${state.data.showAccount && window.SPPSPush ? SPPSPush.cardHtml() : ''}
      ${state.data.showAccount ? `<section class="card" data-section="account"><h2>Account</h2><form data-form="change-password">
        ${field('Current password', '<input name="current_password" type="password" required autocomplete="current-password">')}
        ${field('New password (at least 12 characters)', '<input name="new_password" type="password" required minlength="12" autocomplete="new-password">')}
        <button class="btn small" type="submit">Change password</button></form>
        <div class="inline-actions" style="margin-top:12px">${btn('Sign out on all devices', 'logout-all', 'btn small')}${has('athlete') ? btn('My consent history', 'show-consents', 'btn small', `data-id="${me.person_id}"`) : ''}</div></section>` : ''}
      ${bookingCard()}
      ${consentCard()}
      ${has('athlete') || has('guardian') ? programmeCard() : ''}
      ${has('athlete') || has('guardian') ? appointmentsCard() : ''}
      ${has('athlete') || has('guardian') ? linksCard() : ''}
      ${has('guardian') ? dependantsCard() : ''}
      ${has('reviewer') ? reviewCard() : ''}
    </main>`;
  }

  async function verifyPayment(bookingId, orderId, paymentId, signature) {
    const r = await api(`/api/portal/bookings/${bookingId}/verify-payment`, { method: 'POST', body: { order_id: orderId, payment_id: paymentId, signature } });
    state.data.checkout = null; await load(); render();
    notify(r.outcome === 'REFUND_REQUIRED' ? 'This booking could no longer be confirmed; your payment is being refunded.' : 'Payment received — appointment confirmed');
  }

  function razorpayCheckout(order) {
    // Razorpay TEST mode only (the server refuses live keys). Loaded on demand from the provider's origin.
    return new Promise((resolve, reject) => {
      const go = () => new window.Razorpay({
        key: order.key_id, order_id: order.order_id, amount: order.amount_paise, currency: order.currency, name: 'SPPS',
        handler: r => verifyPayment(order.booking_id, r.razorpay_order_id, r.razorpay_payment_id, r.razorpay_signature).then(resolve, reject),
        modal: { ondismiss: resolve },
      }).open();
      if (window.Razorpay) { go(); return; }
      const tag = document.createElement('script'); tag.src = 'https://checkout.razorpay.com/v1/checkout.js'; tag.onload = go;
      tag.onerror = () => reject(Error('Payment page could not load')); document.head.appendChild(tag);
    });
  }

  function render() { root.innerHTML = state.me ? homeView() : authView(); }

  async function load() {
    const tasks = [api('/api/portal/links').then(x => { state.data.links = x; }),
      api('/api/portal/bookings').then(x => { state.data.bookings = x; }),
      api('/api/portal/notifications').then(x => { state.data.notifications = x; }),
      api('/api/portal/programmes').then(x => { state.data.participation = x; })];
    if (has('guardian')) tasks.push(api('/api/portal/dependants').then(x => { state.data.dependants = x; }));
    if (has('reviewer')) tasks.push(api('/api/portal/review/authorities').then(x => { state.data.queue = x; }));
    await Promise.all(tasks);
  }

  // Background refresh (SPPS-ANDROID-001 follow-up): practitioner decisions appear without a reload.
  // Skips while the user edits any form (typed text, changed selects/checkboxes) or focuses a control,
  // and re-renders only when the server data actually changed.
  let portalRefreshing = false;
  function formDirty(f) {
    return [...f.elements].some(e => e.type === 'checkbox' || e.type === 'radio' ? e.checked !== e.defaultChecked
      // A single select with no `selected` attribute shows its first option by default.
      : e.tagName === 'SELECT' ? (e.multiple ? [...e.options].some(o => o.selected !== o.defaultSelected)
        : e.options.length > 0 && e.selectedIndex !== Math.max(0, [...e.options].findIndex(o => o.defaultSelected)))
      : 'defaultValue' in e && e.type !== 'submit' && e.type !== 'button' && e.value !== e.defaultValue);
  }
  function portalBusy() {
    const a = document.activeElement;
    if (a && root.contains(a) && a.matches('input,select,textarea')) return true;
    return [...root.querySelectorAll('form')].some(formDirty);
  }
  async function refreshPortal() {
    if (portalRefreshing || !state.me || state.view !== 'home' || document.visibilityState !== 'visible' || portalBusy()) return;
    portalRefreshing = true;
    try {
      const before = JSON.stringify(state.data);
      await load();
      if (state.me && state.view === 'home' && !portalBusy() && JSON.stringify(state.data) !== before) render();
    } catch (err) { if (err.status === 401) handleError(err); }
    finally { portalRefreshing = false; }
  }
  window.addEventListener('focus', refreshPortal);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refreshPortal(); });
  setInterval(refreshPortal, 30000);

  async function enter(me) {
    state.me = me; state.csrf = me.csrf; state.view = 'home'; state.message = ''; await load(); render();
    if (window.SPPSPush) SPPSPush.start({ scope: 'portal', csrf: me.csrf });
    const pid = bookPractitioner;
    if (pid && /^\d+$/.test(pid)) await openBooking(pid);
  }

  async function showConsents(subject) {
    const items = await api(`/api/portal/consents/${subject}`);
    const names = {};
    for (const l of state.data.links || []) names[l.practitioner_id] = l.practitioner_name;
    const dep = (state.data.dependants || []).find(d => String(d.person_id) === String(subject));
    state.data.names = names;
    state.data.consents = { subject, items, label: dep ? dep.display_name : 'me' };
    render();
    root.querySelector('[data-section="consents"]')?.scrollIntoView({ block: 'start' });
  }

  async function onAction(el) {
    const a = el.dataset.action;
    if (a === 'view') { state.view = el.dataset.view; state.message = ''; render(); return; }
    if (a === 'logout' || a === 'logout-all') {
      await api(a === 'logout' ? '/api/portal/auth/logout' : '/api/portal/auth/logout-all', { method: 'POST' });
      if (window.SPPSPush) SPPSPush.stop();
      Object.assign(state, { me: null, csrf: '', view: 'signin', data: {}, preview: null, message: 'You are signed out.' }); render(); return;
    }
    if (a === 'book-close') { state.book = null; clearBookPractitioner(); render(); return; }
    if (a === 'toggle-account') { state.data.showAccount = !state.data.showAccount; render(); return; }
    if (a === 'cancel-preview') { state.preview = null; render(); return; }
    if (a === 'cancel-forms') { state.data.authorityFor = null; state.data.evidenceFor = null; render(); return; }
    if (a === 'show-authority') { state.data.authorityFor = el.dataset.id; state.data.evidenceFor = null; render(); return; }
    if (a === 'show-evidence') { state.data.evidenceFor = el.dataset.id; state.data.authorityFor = null; render(); return; }
    if (a === 'close-consents') { state.data.consents = null; render(); return; }
    if (a === 'show-consents') { await showConsents(el.dataset.id); return; }
    if (a === 'pay-booking') {
      if (state.freeAccess) { notify('Appointments are free in SPPS Version 1.0 - Free.'); return; }
      const order = await api(`/api/portal/bookings/${el.dataset.id}/pay`, { method: 'POST' });
      if (order.checkout === 'razorpay') { await razorpayCheckout(order); return; }
      notify('Online payment is not available.'); return;
    }
    if (a === 'join-video') {
      const r = await api(`/api/portal/bookings/${el.dataset.id}/video-ticket`, { method: 'POST' });
      location.assign(r.room_url); return;
    }
    if (a === 'close-checkout') { state.data.checkout = null; render(); return; }
    if (a === 'cancel-booking') {
      const paid = el.dataset.paid === 'true';
      if (!confirm(state.freeAccess?'Cancel this free appointment?':(paid ? 'Cancel this confirmed appointment? Your payment will be refunded in full.' : 'Cancel this appointment request?'))) return;
      const r = await api(`/api/portal/bookings/${el.dataset.id}/cancel`, { method: 'POST', body: { reason: 'Cancelled by client' } });
      await load(); render(); notify(r.status === 'CANCELLED' ? 'Cancelled' : (state.freeAccess?'Appointment updated':'Cancelled — refund started')); return;
    }
    if (a === 'reschedule-yes' || a === 'reschedule-no') {
      await api(`/api/portal/bookings/${el.dataset.id}/reschedule-response`, { method: 'POST', body: { accept: a === 'reschedule-yes' } });
      await load(); render(); notify(a === 'reschedule-yes' ? 'New time accepted' : 'Proposed time declined'); return;
    }
    if (a === 'revoke-link') {
      if (!confirm('Disconnect from this practitioner? They will no longer share anything with you through the portal.')) return;
      await api(`/api/portal/links/${el.dataset.id}/revoke`, { method: 'POST' }); await load(); render(); notify('Disconnected'); return;
    }
    if (a === 'revoke-authority') {
      if (!confirm('Withdraw your guardian authority for this young athlete?')) return;
      await api(`/api/portal/guardian-authorities/${el.dataset.id}/revoke`, { method: 'POST' }); await load(); render(); notify('Authority withdrawn'); return;
    }
    if (a === 'withdraw-consent') {
      if (!confirm('Withdraw this consent? Any related portal access stops immediately.')) return;
      const practitioner = el.dataset.practitioner ? Number(el.dataset.practitioner) : null;
      await api('/api/portal/consents', { method: 'POST', body: { subject_person_id: Number(el.dataset.subject), purpose: el.dataset.purpose, practitioner_id: practitioner, decision: 'WITHDRAWN' } });
      await load(); await showConsents(el.dataset.subject); notify('Consent withdrawn'); return;
    }
  }

  async function onSubmit(form, submitter) {
    const f = Object.fromEntries(new FormData(form).entries());
    const kind = form.dataset.form;
    if (kind === 'signin') { await enter(await api('/api/portal/auth/login', { method: 'POST', body: { email: f.email, password: f.password } })); return; }
    if (kind === 'register') {
      const r = await api('/api/portal/auth/register', { method: 'POST', body: { email: f.email, password: f.password, display_name: f.display_name, role: f.role, adult_attested: !!f.adult_attested, terms_accepted: !!f.terms_accepted } });
      state.view = 'verify'; state.message = r.message + ' Open the confirmation link in that e-mail to finish. If the link does not open, paste the code at the end of it (after verify=) below.'; render(); return;
    }
    if (kind === 'verify') { await api('/api/portal/auth/verify-email', { method: 'POST', body: { token: f.token.trim() } }); state.view = 'signin'; state.message = 'Email verified. You can now sign in.'; render(); return; }
    if (kind === 'resend') { const r = await api('/api/portal/auth/resend-verification', { method: 'POST', body: { email: f.email } }); state.message = r.message; render(); return; }
    if (kind === 'forgot') { const r = await api('/api/portal/auth/password-reset/request', { method: 'POST', body: { email: f.email } }); state.view = 'reset'; state.message = r.message; render(); return; }
    if (kind === 'reset') { await api('/api/portal/auth/password-reset/confirm', { method: 'POST', body: { token: (f.token_visible || f.token).trim(), new_password: f.new_password } }); state.view = 'signin'; state.message = 'Password updated. Sign in with your new password.'; render(); return; }
    if (kind === 'change-password') { await api('/api/portal/auth/password/change', { method: 'POST', body: f }); form.reset(); notify('Password changed. Other devices were signed out.'); return; }
    if (kind === 'preview') {
      const r = await api('/api/portal/links/preview', { method: 'POST', body: { code: f.code } });
      state.preview = { ...r, code: f.code, subject: f.subject_person_id }; render(); return;
    }
    if (kind === 'connect') {
      const p = state.preview; const subject = Number(f.subject_person_id);
      await api('/api/portal/consents', { method: 'POST', body: { subject_person_id: subject, purpose: 'CASE_LINK', practitioner_id: p.practitioner_id, decision: 'GRANTED' } });
      await api('/api/portal/links/redeem', { method: 'POST', body: { code: p.code, subject_person_id: subject } });
      state.preview = null; await load(); render(); notify(`Request sent to ${p.practitioner_name}. It becomes active once they approve.`); return;
    }
    if (kind === 'programme-respond') {
      const accept = (submitter?.value || 'accept') === 'accept';
      const prog = state.data.participation.programmes.find(p => String(p.id) === String(form.dataset.id));
      if (accept) await api('/api/portal/consents', { method: 'POST', body: { subject_person_id: prog.person_id, purpose: 'PROGRAMME_PARTICIPATION', practitioner_id: prog.practitioner_id, decision: 'GRANTED' } });
      await api(`/api/portal/programmes/${form.dataset.id}/respond`, { method: 'POST', body: { accept } });
      await load(); render(); notify(accept ? 'Programme accepted' : 'Programme declined'); return;
    }
    if (kind === 'task-submit') {
      await api(`/api/portal/tasks/${form.dataset.id}/submit`, { method: 'POST', body: { completed: !!f.completed, self_rating: f.self_rating === '' || f.self_rating === undefined ? null : Number(f.self_rating), reflection: f.reflection || '' } });
      await load(); render(); notify('Check-in logged'); return;
    }
    if (kind === 'book-service') {
      const b = state.book; b.service_id = f.service_id; b.subject = Number(f.subject_person_id);
      b.slots = (await api(`/api/portal/directory/${b.pid}/slots?service_id=${encodeURIComponent(f.service_id)}`)).slots;
      try { b.formToken = (await api('/api/public/form-token')).form_token; } catch { b.formToken = ''; }
      render(); return;
    }
    if (kind === 'book-send') {
      const b = state.book; const pid = Number(b.pid);
      const svc = (b.card.services || []).find(x => String(x.service_id) === String(b.service_id));
      const consent = purpose => api('/api/portal/consents', { method: 'POST', body: { subject_person_id: b.subject, purpose, practitioner_id: pid, decision: 'GRANTED' } });
      await consent('CASE_LINK'); await consent('APPOINTMENT_BOOKING');
      if (svc && svc.mode === 'online') await consent('VIDEO_CONSULTATION');
      b.sent = await api('/api/portal/directory-bookings', { method: 'POST', body: {
        practitioner_id: pid, service_id: Number(b.service_id), slot_id: Number(f.slot_id), subject_person_id: b.subject,
        note: f.note || '', form_token: b.formToken || '', website: f.website || '' } });
      clearBookPractitioner();
      await load(); render(); notify('Appointment request sent for practitioner approval.'); return;
    }
    if (kind === 'load-slots') {
      state.data.slots = { link: f.link_id, items: await api(`/api/portal/links/${f.link_id}/slots`) }; render(); return;
    }
    if (kind === 'request-booking') {
      const link = (state.data.links || []).find(l => String(l.link_id) === String(f.link_id));
      const consentBody = purpose => ({ subject_person_id: link.person_id, purpose, practitioner_id: link.practitioner_id, decision: 'GRANTED' });
      await api('/api/portal/consents', { method: 'POST', body: consentBody('APPOINTMENT_BOOKING') });
      if (f.video_consent) await api('/api/portal/consents', { method: 'POST', body: consentBody('VIDEO_CONSULTATION') });
      await api('/api/portal/bookings', { method: 'POST', body: { link_id: Number(f.link_id), slot_id: Number(f.slot_id), video_requested: !!f.video_requested, note: f.note || '' } });
      state.data.slots = null; await load(); render(); notify(state.freeAccess?'Free appointment request sent for practitioner approval.':'Request sent. You will be asked to pay only after approval.'); return;
    }
    if (kind === 'dependant') { await api('/api/portal/dependants', { method: 'POST', body: { display_name: f.display_name } }); await load(); render(); notify('Added'); return; }
    if (kind === 'authority') {
      const scopes = [...form.querySelectorAll('input[name="scope"]:checked')].map(x => x.value);
      await api('/api/portal/guardian-authorities', { method: 'POST', body: { dependant_person_id: Number(f.dependant_person_id), scopes, evidence_reference: f.evidence_reference || null } });
      state.data.authorityFor = null; await load(); render(); notify('Authority request submitted for independent review'); return;
    }
    if (kind === 'evidence') { await api(`/api/portal/guardian-authorities/${f.authority_id}/evidence`, { method: 'POST', body: { evidence_reference: f.evidence_reference } }); state.data.evidenceFor = null; await load(); render(); notify('Sent for review'); return; }
    if (kind === 'decision') {
      const decision = submitter?.value || 'REJECTED';
      await api(`/api/portal/review/authorities/${form.dataset.id}/decision`, { method: 'POST', body: { decision, reason: f.reason, valid_days: decision === 'VERIFIED' ? Number(f.valid_days || 365) : null } });
      await load(); render(); notify(decision === 'VERIFIED' ? 'Authority verified' : 'Request rejected'); return;
    }
  }

  document.addEventListener('click', async e => {
    const el = e.target.closest('[data-action]'); if (!el) return;
    e.preventDefault();
    try { await onAction(el); } catch (err) { handleError(err); }
  });
  document.addEventListener('submit', async e => {
    const form = e.target.closest('form[data-form]'); if (!form) return;
    e.preventDefault();
    const submitter = e.submitter; const buttons = form.querySelectorAll('button'); buttons.forEach(b => { b.disabled = true; });
    try { await onSubmit(form, submitter); } catch (err) { handleError(err); buttons.forEach(b => { b.disabled = false; }); }
  });
  function handleError(err) {
    if (err.status === 401 && state.me) { if (window.SPPSPush) SPPSPush.stop(); Object.assign(state, { me: null, csrf: '', view: 'signin', data: {}, message: 'Your session ended. Please sign in again.' }); render(); return; }
    notify(err.message || 'Something went wrong');
  }

  (async () => {
    try { const runtime = await api('/api/runtime'); state.freeAccess = !!runtime.free_access; state.directoryBooking = !!runtime.v2_flags?.SPPS_DIRECTORY_BOOKING_ENABLED; } catch { /* use default until service is available */ }
    const params = new URLSearchParams(location.search);
    if (/^\d+$/.test(params.get('practitioner') || '')) {
      bookPractitioner = params.get('practitioner');
      state.message = 'Sign in, or create an account and verify your email, to request an appointment with this practitioner.';
      history.replaceState({}, '', `/portal?practitioner=${bookPractitioner}`);
    }
    if (params.get('reset')) { state.view = 'reset'; state.data.resetToken = params.get('reset'); }
    if (params.get('verify')) {
      try { await api('/api/portal/auth/verify-email', { method: 'POST', body: { token: params.get('verify') } }); state.message = 'Email verified. You can now sign in.'; }
      catch (err) { state.message = err.message; }
      history.replaceState({}, '', '/portal');
    }
    try { await enter(await api('/api/portal/auth/me')); }
    catch { render(); }
  })();
})();
