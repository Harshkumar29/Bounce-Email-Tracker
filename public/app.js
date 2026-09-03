// ==========================================
// State & helpers
// ==========================================

let isAuthed = false;
let currentUser = null;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function extractErrorMessages(data) {
  if (Array.isArray(data.errors)) return data.errors.join(' · ');
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || JSON.stringify(d)).join(' · ');
  if (typeof data.detail === 'string') return data.detail;
  return 'Something went wrong';
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const color =
    type === 'error'
      ? 'bg-rose-500/20 border-rose-500 text-rose-200'
      : type === 'success'
      ? 'bg-emerald-500/20 border-emerald-500 text-emerald-200'
      : 'bg-blue-500/20 border-blue-500 text-blue-200';
  toast.className = `p-4 rounded-xl border backdrop-blur-md shadow-xl text-sm flex items-center gap-3 ${color} transition-all duration-300`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}

function setButtonLoading(button, isLoading, loadingLabel) {
  button.disabled = isLoading;
  const label = button.querySelector('.btn-label');
  if (isLoading) {
    button.dataset.originalLabel = label ? label.textContent : '';
    if (label) label.textContent = loadingLabel || 'Please wait…';
  } else if (label && button.dataset.originalLabel) {
    label.textContent = button.dataset.originalLabel;
  }
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

// ==========================================
// View navigation (SPA-style)
// ==========================================

const VIEWS = ['auth', 'dashboard', 'campaigns', 'composer', 'accounts'];

function navigate(viewName) {
  if (!isAuthed && viewName !== 'auth') viewName = 'auth';
  VIEWS.forEach((v) => document.getElementById(`view-${v}`).classList.add('hidden'));
  document.getElementById(`view-${viewName}`).classList.remove('hidden');

  if (viewName === 'dashboard') loadDashboardData();
  if (viewName === 'campaigns') loadCampaignsData();
  if (viewName === 'accounts') loadEmailAccounts();

  updateNav();
  refreshIcons();
}

function updateNav() {
  const authActions = document.getElementById('auth-actions');
  const navLinks = document.getElementById('nav-links');

  if (isAuthed && currentUser) {
    navLinks.classList.remove('hidden');
    navLinks.classList.add('flex');
    authActions.innerHTML = `
      <span class="text-xs text-slate-300 font-medium hidden sm:inline">${escapeHtml(currentUser.email)}</span>
      <button id="logout-btn" class="px-3 py-1.5 rounded-lg border border-slate-700 hover:bg-slate-800 text-slate-300 text-xs transition">Log Out</button>
    `;
    document.getElementById('logout-btn').addEventListener('click', logout);
  } else {
    navLinks.classList.add('hidden');
    navLinks.classList.remove('flex');
    authActions.innerHTML = `<button onclick="navigate('auth')" class="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 font-semibold text-xs transition">Sign In</button>`;
  }
}

// ==========================================
// Auth (cookie-session based — see backend/app/auth.py)
// ==========================================

async function checkAuth() {
  try {
    const res = await fetch('/auth/me');
    if (res.ok) {
      currentUser = await res.json();
      isAuthed = true;
      navigate('dashboard');
    } else {
      isAuthed = false;
      navigate('auth');
    }
  } catch (err) {
    isAuthed = false;
    navigate('auth');
  }
}

async function logout() {
  try {
    await fetch('/auth/logout', { method: 'POST' });
  } catch (err) {
    /* ignore */
  }
  isAuthed = false;
  currentUser = null;
  navigate('auth');
}

const authTabs = document.querySelectorAll('[data-auth-tab]');
const loginForm = document.getElementById('login-form');
const registerForm = document.getElementById('register-form');
const forgotPasswordForm = document.getElementById('forgot-password-form');
const resetPasswordForm = document.getElementById('reset-password-form');
const authTabsBar = document.getElementById('auth-tabs');
const allAuthForms = [loginForm, registerForm, forgotPasswordForm, resetPasswordForm];

function showAuthForm(formToShow, { showTabs = true } = {}) {
  allAuthForms.forEach((f) => f.classList.toggle('hidden', f !== formToShow));
  authTabsBar.classList.toggle('hidden', !showTabs);
}

authTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    authTabs.forEach((t) => {
      const active = t === tab;
      t.classList.toggle('text-blue-400', active);
      t.classList.toggle('border-b-2', active);
      t.classList.toggle('border-blue-500', active);
      t.classList.toggle('font-semibold', active);
      t.classList.toggle('text-slate-400', !active);
      t.classList.toggle('font-medium', !active);
    });
    showAuthForm(tab.dataset.authTab === 'login' ? loginForm : registerForm);
  });
});

document.getElementById('forgot-password-link').addEventListener('click', () => {
  showAuthForm(forgotPasswordForm, { showTabs: false });
});

document.getElementById('back-to-login-link').addEventListener('click', () => {
  document.querySelector('[data-auth-tab="login"]').click();
});

document.querySelectorAll('.toggle-password').forEach((btn) => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.toggleFor);
    input.type = input.type === 'password' ? 'text' : 'password';
  });
});

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = document.getElementById('login-status');
  status.textContent = '';
  status.className = 'text-xs text-center min-h-[1em]';
  const btn = document.getElementById('login-btn');
  setButtonLoading(btn, true, 'Signing in…');
  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: document.getElementById('loginEmail').value,
        password: document.getElementById('loginPassword').value,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = extractErrorMessages(data);
      status.classList.add('text-rose-400');
      return;
    }
    currentUser = data;
    isAuthed = true;
    showToast('Welcome back!', 'success');
    navigate('dashboard');
  } catch (err) {
    status.textContent = 'Network error — is the server running?';
    status.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

registerForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = document.getElementById('register-status');
  status.textContent = '';
  status.className = 'text-xs text-center min-h-[1em]';
  const btn = document.getElementById('register-btn');
  setButtonLoading(btn, true, 'Creating account…');
  try {
    const res = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: document.getElementById('registerEmail').value,
        password: document.getElementById('registerPassword').value,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = extractErrorMessages(data);
      status.classList.add('text-rose-400');
      return;
    }
    status.textContent = 'Account created — log in now.';
    status.classList.add('text-emerald-400');
    document.getElementById('loginEmail').value = document.getElementById('registerEmail').value;
    document.querySelector('[data-auth-tab="login"]').click();
  } catch (err) {
    status.textContent = 'Network error — is the server running?';
    status.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

forgotPasswordForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = document.getElementById('forgot-password-status');
  status.textContent = '';
  status.className = 'text-xs text-center min-h-[1em]';
  const btn = document.getElementById('forgot-password-btn');
  setButtonLoading(btn, true, 'Sending…');
  try {
    const res = await fetch('/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: document.getElementById('forgotEmail').value }),
    });
    const data = await res.json();
    status.textContent = res.ok ? data.message || 'If that email is registered, a reset link has been sent.' : extractErrorMessages(data);
    status.classList.add(res.ok ? 'text-emerald-400' : 'text-rose-400');
  } catch (err) {
    status.textContent = 'Network error — is the server running?';
    status.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

resetPasswordForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const status = document.getElementById('reset-password-status');
  status.textContent = '';
  status.className = 'text-xs text-center min-h-[1em]';
  const btn = document.getElementById('reset-password-btn');
  setButtonLoading(btn, true, 'Saving…');
  try {
    const res = await fetch('/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: resetPasswordForm.dataset.token,
        password: document.getElementById('newPassword').value,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = extractErrorMessages(data);
      status.classList.add('text-rose-400');
      return;
    }
    status.textContent = 'Password updated — log in with your new password.';
    status.classList.add('text-emerald-400');
    window.history.replaceState({}, '', window.location.pathname);
    setTimeout(() => document.querySelector('[data-auth-tab="login"]').click(), 1500);
  } catch (err) {
    status.textContent = 'Network error — is the server running?';
    status.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

function checkForResetToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('reset_token');
  if (!token) return false;
  resetPasswordForm.dataset.token = token;
  showAuthForm(resetPasswordForm, { showTabs: false });
  isAuthed = false;
  navigate('auth');
  return true;
}

// ==========================================
// Campaign composer (recipients +/-, validation, dispatch)
// ==========================================

const campaignForm = document.getElementById('campaign-form');
const recipientList = document.getElementById('recipient-list');

function makeRecipientRow() {
  const row = document.createElement('div');
  row.className = 'recipient-row grid grid-cols-[1fr,38px,38px] gap-2';
  row.setAttribute('data-recipient-row', '');
  row.innerHTML = `
    <input type="email" class="recipient-input input-dark" placeholder="recipient@example.com" />
    <button type="button" class="icon-btn remove-btn w-[38px] h-[38px] rounded-lg border border-slate-700 text-rose-400 hover:border-rose-400 flex items-center justify-center" data-remove title="Remove recipient">&minus;</button>
    <button type="button" class="icon-btn add-btn w-[38px] h-[38px] rounded-lg border border-slate-700 text-blue-400 hover:border-blue-400 flex items-center justify-center" data-add title="Add recipient">&plus;</button>
  `;
  return row;
}

function refreshRecipientButtons() {
  const rows = recipientList.querySelectorAll('[data-recipient-row]');
  rows.forEach((row, idx) => {
    row.querySelector('[data-add]').style.visibility = idx === rows.length - 1 ? 'visible' : 'hidden';
    row.querySelector('[data-remove]').disabled = rows.length === 1;
  });
}

recipientList.addEventListener('click', (e) => {
  if (e.target.closest('[data-add]')) {
    const row = e.target.closest('[data-recipient-row]');
    row.after(makeRecipientRow());
    refreshRecipientButtons();
  } else if (e.target.closest('[data-remove]')) {
    const rows = recipientList.querySelectorAll('[data-recipient-row]');
    if (rows.length === 1) return;
    e.target.closest('[data-recipient-row]').remove();
    refreshRecipientButtons();
  }
});

refreshRecipientButtons();

function collectRecipientEmails() {
  return Array.from(recipientList.querySelectorAll('.recipient-input')).map((i) => i.value.trim());
}

function clearFormErrors() {
  campaignForm.querySelectorAll('.error').forEach((el) => (el.textContent = ''));
  campaignForm.querySelectorAll('.invalid').forEach((el) => el.classList.remove('invalid'));
}

function setFieldError(fieldId, message) {
  const el = campaignForm.querySelector(`[data-error-for="${fieldId}"]`);
  if (el) el.textContent = message;
  const input = document.getElementById(fieldId);
  if (input) input.classList.add('invalid');
}

function validateCampaignForm(payload) {
  clearFormErrors();
  let valid = true;

  if (!payload.campaignName.trim()) {
    setFieldError('campaignName', 'Campaign name is required');
    valid = false;
  }
  if (!payload.fromEmail.trim()) {
    setFieldError('fromEmail', 'From Email is required');
    valid = false;
  } else if (!EMAIL_RE.test(payload.fromEmail.trim())) {
    setFieldError('fromEmail', 'Enter a valid email address');
    valid = false;
  }
  if (!payload.body.trim()) {
    setFieldError('body', 'Email body is required');
    valid = false;
  }

  const toEmails = payload.toEmails.filter(Boolean);
  const toEmailsErrorEl = campaignForm.querySelector('[data-error-for="toEmails"]');
  if (toEmails.length === 0) {
    if (toEmailsErrorEl) toEmailsErrorEl.textContent = 'At least one To Email is required';
    valid = false;
  } else {
    const invalidOnes = toEmails.filter((e) => !EMAIL_RE.test(e));
    const dupes = toEmails.filter((e, i) => toEmails.indexOf(e) !== i);
    if (invalidOnes.length) {
      if (toEmailsErrorEl) toEmailsErrorEl.textContent = `Invalid email(s): ${invalidOnes.join(', ')}`;
      valid = false;
    } else if (dupes.length) {
      if (toEmailsErrorEl) toEmailsErrorEl.textContent = `Duplicate recipient(s): ${[...new Set(dupes)].join(', ')}`;
      valid = false;
    }
  }

  return valid;
}

campaignForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formStatus = document.getElementById('form-status');
  formStatus.textContent = '';
  formStatus.className = 'text-xs';

  const payload = {
    campaignName: document.getElementById('campaignName').value,
    fromEmail: document.getElementById('fromEmail').value,
    toEmails: collectRecipientEmails(),
    body: document.getElementById('body').value,
  };

  if (!validateCampaignForm(payload)) {
    formStatus.textContent = 'Please fix the errors above.';
    formStatus.classList.add('text-rose-400');
    return;
  }

  const btn = document.getElementById('submit-btn');
  setButtonLoading(btn, true, 'Dispatching…');

  try {
    const res = await fetch('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      formStatus.textContent = extractErrorMessages(data);
      formStatus.classList.add('text-rose-400');
      return;
    }

    showToast(`Campaign "${data.campaignName}" dispatched successfully!`, 'success');
    campaignForm.reset();
    clearFormErrors();
    recipientList.innerHTML = '';
    recipientList.appendChild(makeRecipientRow());
    refreshRecipientButtons();
    navigate('campaigns');
  } catch (err) {
    formStatus.textContent = 'Network error — is the server running?';
    formStatus.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

// ==========================================
// Dashboard (aggregate stats + activity feed)
// ==========================================

async function loadDashboardData() {
  try {
    const res = await fetch('/api/campaigns');
    const campaigns = res.ok ? await res.json() : [];
    const allRecipients = campaigns.flatMap((c) => c.recipients.map((r) => ({ ...r, campaignName: c.campaignName })));

    const counts = {
      delivered: allRecipients.filter((r) => r.delivered).length,
      opened: allRecipients.filter((r) => r.opened).length,
      notOpened: allRecipients.filter((r) => r.notOpened).length,
      clicked: allRecipients.filter((r) => r.linkClicked).length,
      bounced: allRecipients.filter((r) => r.bounced).length,
      unsubscribed: allRecipients.filter((r) => r.unsubscribed).length,
      spam: allRecipients.filter((r) => r.spamReported === true).length,
    };

    document.getElementById('stat-delivered').textContent = counts.delivered;
    document.getElementById('stat-opened').textContent = counts.opened;
    document.getElementById('stat-not-opened').textContent = counts.notOpened;
    document.getElementById('stat-clicked').textContent = counts.clicked;
    document.getElementById('stat-bounced').textContent = counts.bounced;
    document.getElementById('stat-unsubscribed').textContent = counts.unsubscribed;
    document.getElementById('stat-spam').textContent = counts.spam;

    const openRate = counts.delivered ? Math.round((counts.opened / counts.delivered) * 100) : 0;
    const clickRate = counts.delivered ? Math.round((counts.clicked / counts.delivered) * 100) : 0;
    document.getElementById('stat-open-rate').textContent = `${openRate}% rate`;
    document.getElementById('stat-click-rate').textContent = `${clickRate}% rate`;

    const events = [];
    allRecipients.forEach((r) => {
      if (r.openedAt) events.push({ type: 'open', email: r.email, campaignName: r.campaignName, at: r.openedAt });
      if (r.linkClickedAt) events.push({ type: 'click', email: r.email, campaignName: r.campaignName, at: r.linkClickedAt });
      if (r.bouncedAt) events.push({ type: 'bounce', email: r.email, campaignName: r.campaignName, at: r.bouncedAt });
      if (r.unsubscribedAt) events.push({ type: 'unsubscribe', email: r.email, campaignName: r.campaignName, at: r.unsubscribedAt });
    });
    events.sort((a, b) => new Date(b.at) - new Date(a.at));

    const feed = document.getElementById('activity-feed');
    if (!events.length) {
      feed.innerHTML = '<div class="py-8 text-center text-slate-500">No telemetry recorded yet. Send a campaign to begin tracking.</div>';
    } else {
      const ICONS = { open: 'eye', click: 'mouse-pointer-click', bounce: 'alert-octagon', unsubscribe: 'user-x' };
      const COLORS = {
        open: 'bg-emerald-500/10 text-emerald-400',
        click: 'bg-purple-500/10 text-purple-400',
        bounce: 'bg-rose-500/10 text-rose-400',
        unsubscribe: 'bg-cyan-500/10 text-cyan-400',
      };
      const VERB = { open: 'opened', click: 'clicked a link in', bounce: 'bounced on', unsubscribe: 'unsubscribed from' };
      feed.innerHTML = events
        .slice(0, 20)
        .map(
          (ev) => `
        <div class="py-3 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 rounded-lg ${COLORS[ev.type]} flex items-center justify-center">
              <i data-lucide="${ICONS[ev.type]}" class="w-4 h-4"></i>
            </div>
            <div>
              <span class="font-medium text-slate-200">${escapeHtml(ev.email)}</span>
              <span class="text-xs text-slate-400 ml-2">${VERB[ev.type]} <strong>${escapeHtml(ev.campaignName)}</strong></span>
            </div>
          </div>
          <div class="text-xs text-slate-500">${new Date(ev.at).toLocaleString()}</div>
        </div>
      `
        )
        .join('');
    }
    refreshIcons();
  } catch (err) {
    /* ignore */
  }
}

// ==========================================
// Campaigns list + detail modal
// ==========================================

const detailModal = document.getElementById('detail-modal');

function yesNo(value) {
  if (value === true) return '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Yes</span>';
  if (value === false) return '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-slate-700/50 text-slate-400">No</span>';
  return '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20">Partial</span>';
}

function statusBadge(status) {
  const map = {
    Sent: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20',
    Sending: 'bg-amber-500/10 text-amber-400 border border-amber-500/20',
    Scheduled: 'bg-slate-700/50 text-slate-300 border border-slate-600/40',
  };
  return `<span class="text-[11px] px-2 py-0.5 rounded-full uppercase font-bold ${map[status] || map.Scheduled}">${escapeHtml(status)}</span>`;
}

async function loadCampaignsData() {
  const container = document.getElementById('campaigns-list');
  try {
    const res = await fetch('/api/campaigns');
    if (!res.ok) return;
    const campaigns = await res.json();

    if (!campaigns.length) {
      container.innerHTML = '<div class="glass-card rounded-2xl p-8 text-center text-slate-500">No campaigns created yet. Click "New Campaign" to create one.</div>';
      return;
    }

    container.innerHTML = campaigns
      .map((c) => {
        const delivered = c.recipients.filter((r) => r.delivered).length;
        return `
        <div class="glass-card rounded-2xl p-5 hover:border-slate-700 transition">
          <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-3">
            <div>
              <div class="flex items-center gap-2">
                <h3 class="font-semibold text-white campaign-row cursor-pointer" data-id="${c.id}">${escapeHtml(c.campaignName)}</h3>
                ${statusBadge(c.status)}
              </div>
              <p class="text-xs text-slate-400 mt-1">From: <span class="text-slate-200">${escapeHtml(c.fromEmail)}</span> &bull; ${delivered}/${c.recipients.length} delivered</p>
            </div>
            <div class="flex items-center gap-3 text-xs text-slate-400">
              <div><strong class="text-white">${c.recipients.length}</strong> recipients</div>
              <div>${new Date(c.createdAt).toLocaleDateString()}</div>
              <button class="campaign-row px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-white font-medium transition" data-id="${c.id}">Details</button>
              <button class="delete-btn text-rose-400 hover:text-rose-300 px-2" data-id="${c.id}">Delete</button>
            </div>
          </div>
        </div>
      `;
      })
      .join('');
    refreshIcons();
  } catch (err) {
    container.innerHTML = '<div class="glass-card rounded-2xl p-8 text-center text-slate-500">Failed to load campaigns.</div>';
  }
}

document.getElementById('campaigns-list').addEventListener('click', async (e) => {
  const deleteBtn = e.target.closest('.delete-btn');
  if (deleteBtn) {
    const id = deleteBtn.getAttribute('data-id');
    deleteBtn.disabled = true;
    try {
      await fetch(`/api/campaigns/${id}`, { method: 'DELETE' });
      showToast('Campaign deleted', 'info');
      await loadCampaignsData();
    } catch (err) {
      deleteBtn.disabled = false;
    }
    return;
  }

  const row = e.target.closest('.campaign-row');
  if (!row) return;
  await openDetailModal(row.getAttribute('data-id'));
});

async function openDetailModal(id) {
  try {
    const res = await fetch(`/api/campaigns/${id}`);
    if (!res.ok) throw new Error('Failed to load campaign');
    const c = await res.json();

    document.getElementById('modal-title').textContent = c.campaignName;
    document.getElementById('modal-subtitle').innerHTML = `From: <strong class="text-slate-200">${escapeHtml(c.fromEmail)}</strong> &bull; ${c.recipients.length} recipient(s) &bull; Created: ${new Date(c.createdAt).toLocaleString()}`;

    const counts = {
      delivered: c.recipients.filter((r) => r.delivered).length,
      opened: c.recipients.filter((r) => r.opened).length,
      notOpened: c.recipients.filter((r) => r.notOpened).length,
      clicked: c.recipients.filter((r) => r.linkClicked).length,
      bounced: c.recipients.filter((r) => r.bounced).length,
      unsubscribed: c.recipients.filter((r) => r.unsubscribed).length,
      spam: c.recipients.filter((r) => r.spamReported === true).length,
    };
    const statCards = [
      ['Delivered', counts.delivered, 'text-blue-400'],
      ['Opened', counts.opened, 'text-emerald-400'],
      ['Not Opened', counts.notOpened, 'text-amber-400'],
      ['Clicked', counts.clicked, 'text-purple-400'],
      ['Bounced', counts.bounced, 'text-rose-400'],
      ['Unsub', counts.unsubscribed, 'text-cyan-400'],
      ['Spam', counts.spam, 'text-yellow-400'],
    ];
    document.getElementById('modal-stats').innerHTML = statCards
      .map(
        ([label, value, color]) => `
        <div class="p-3 bg-slate-900/90 border border-slate-800 rounded-xl">
          <div class="text-[10px] ${color} font-semibold uppercase">${label}</div>
          <div class="text-xl font-bold text-white mt-1">${value}</div>
        </div>`
      )
      .join('');

    document.getElementById('detail-body').innerHTML = c.recipients
      .map(
        (r) => `
        <tr class="hover:bg-slate-800/40 transition">
          <td class="py-2.5 px-4 font-medium text-slate-200">${escapeHtml(r.email)}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.delivered)}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.opened)}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.notOpened)}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.linkClicked)}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.bounced)}${r.bounceType ? `<div class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(r.bounceType)}</div>` : ''}</td>
          <td class="py-2.5 px-3 text-center">${yesNo(r.unsubscribed)}</td>
          <td class="py-2.5 px-4 text-center">${yesNo(r.spamReported)}</td>
        </tr>`
      )
      .join('');

    detailModal.classList.remove('hidden');
    refreshIcons();
  } catch (err) {
    showToast('Failed to load campaign details', 'error');
  }
}

function closeDetailModal() {
  detailModal.classList.add('hidden');
}

document.getElementById('modal-close').addEventListener('click', closeDetailModal);
document.getElementById('modal-close-2').addEventListener('click', closeDetailModal);
detailModal.addEventListener('click', (e) => {
  if (e.target === detailModal) closeDetailModal();
});

// ==========================================
// Connected email accounts (Mailboxes)
// ==========================================

const connectForm = document.getElementById('connect-form');
const emailAccountsList = document.getElementById('email-accounts-list');
const emailAccountsMessage = document.getElementById('email-accounts-message');

async function loadEmailAccounts() {
  try {
    const res = await fetch('/email-accounts');
    if (!res.ok) return;
    const accounts = await res.json();
    if (!accounts.length) {
      emailAccountsList.innerHTML = '<li class="py-4 text-center text-slate-500 text-sm">No connected mailboxes found.</li>';
      return;
    }
    emailAccountsList.innerHTML = accounts
      .map((a) => {
        const detail = a.provider === 'smtp' && a.smtp_host ? `${escapeHtml(a.provider)} &middot; ${escapeHtml(a.smtp_host)}:${a.smtp_port}` : escapeHtml(a.provider);
        return `
      <li class="py-3 flex items-center justify-between" data-id="${a.id}">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-blue-500/10 text-blue-400 flex items-center justify-center font-bold text-[10px] uppercase">${escapeHtml(a.provider.slice(0, 2))}</div>
          <div>
            <span class="font-medium text-slate-200 text-sm">${escapeHtml(a.email_address)}</span>
            <span class="text-xs text-slate-400 ml-2">${detail} &middot; ${a.is_verified ? 'Verified' : 'Pending'}</span>
          </div>
        </div>
        <button class="text-xs text-rose-400 hover:text-rose-300" data-disconnect="${a.id}">Disconnect</button>
      </li>`;
      })
      .join('');
  } catch (err) {
    /* ignore */
  }
}

connectForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  emailAccountsMessage.textContent = '';
  emailAccountsMessage.className = 'text-xs min-h-[1em]';
  const email = document.getElementById('connectEmail').value.trim();
  const provider = document.getElementById('connectProvider').value;

  try {
    const res = await fetch('/email-accounts/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, provider }),
    });
    const data = await res.json();
    if (!res.ok) {
      emailAccountsMessage.textContent = extractErrorMessages(data);
      emailAccountsMessage.classList.add('text-rose-400');
      return;
    }
    if (data.status === 'already_connected') {
      emailAccountsMessage.textContent = `${email} is already connected.`;
      emailAccountsMessage.classList.add('text-emerald-400');
      await loadEmailAccounts();
      return;
    }
    if (data.authorization_url) window.location.href = data.authorization_url;
  } catch (err) {
    emailAccountsMessage.textContent = 'Network error — is the server running?';
    emailAccountsMessage.classList.add('text-rose-400');
  }
});

const smtpForm = document.getElementById('smtp-form');

smtpForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  emailAccountsMessage.textContent = '';
  emailAccountsMessage.className = 'text-xs min-h-[1em]';
  const btn = document.getElementById('smtp-connect-btn');
  setButtonLoading(btn, true, 'Testing connection…');

  const payload = {
    email: document.getElementById('smtpEmail').value.trim(),
    smtp_host: document.getElementById('smtpHost').value.trim(),
    smtp_port: parseInt(document.getElementById('smtpPort').value, 10),
    smtp_username: document.getElementById('smtpUsername').value.trim(),
    smtp_password: document.getElementById('smtpPassword').value,
    use_tls: document.getElementById('smtpTls').checked,
  };

  try {
    const res = await fetch('/email-accounts/smtp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      emailAccountsMessage.textContent = extractErrorMessages(data);
      emailAccountsMessage.classList.add('text-rose-400');
      return;
    }
    showToast(`Connected ${data.email_address} — campaigns from this address will send through it.`, 'success');
    smtpForm.reset();
    document.getElementById('smtpPort').value = '587';
    document.getElementById('smtpTls').checked = true;
    await loadEmailAccounts();
  } catch (err) {
    emailAccountsMessage.textContent = 'Network error — is the server running?';
    emailAccountsMessage.classList.add('text-rose-400');
  } finally {
    setButtonLoading(btn, false);
  }
});

emailAccountsList.addEventListener('click', async (e) => {
  const id = e.target.getAttribute('data-disconnect');
  if (!id) return;
  e.target.disabled = true;
  try {
    await fetch(`/email-accounts/${id}`, { method: 'DELETE' });
    showToast('Mailbox disconnected', 'info');
    await loadEmailAccounts();
  } catch (err) {
    e.target.disabled = false;
  }
});

(function showOAuthRedirectResult() {
  const params = new URLSearchParams(window.location.search);
  if (params.has('connected')) {
    showToast(`Connected ${params.get('connected')} successfully.`, 'success');
  } else if (params.has('oauth_error')) {
    const err = params.get('oauth_error');
    const messages = {
      identity_mismatch: `You signed in as a different account than the one you tried to connect (requested ${params.get('requested')}, authenticated as ${params.get('authenticated')}).`,
      already_linked_elsewhere: 'That email is already connected to a different account.',
    };
    showToast(messages[err] || `Connection failed: ${err}`, 'error');
  }
  if (params.has('connected') || params.has('oauth_error')) {
    window.history.replaceState({}, '', window.location.pathname);
  }
})();

// ==========================================
// Boot
// ==========================================

refreshIcons();
if (!checkForResetToken()) {
  checkAuth();
}
