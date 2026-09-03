// --------------------------------- Theme toggle ---------------------------------

(function initTheme() {
  const stored = (() => {
    try {
      return localStorage.getItem('theme');
    } catch (err) {
      return null;
    }
  })();
  if (stored === 'light' || stored === 'dark') {
    document.documentElement.setAttribute('data-theme', stored);
  }

  function toggleTheme() {
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const current = document.documentElement.getAttribute('data-theme') || (prefersDark ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try {
      localStorage.setItem('theme', next);
    } catch (err) {
      /* ignore — per-viewer convenience only */
    }
  }

  document.querySelectorAll('.theme-toggle').forEach((btn) => btn.addEventListener('click', toggleTheme));
})();

const form = document.getElementById('campaign-form');
const submitBtn = document.getElementById('submit-btn');
const formStatus = document.getElementById('form-status');
const trackerBody = document.getElementById('tracker-body');
const recipientList = document.getElementById('recipient-list');

const modal = document.getElementById('detail-modal');
const modalTitle = document.getElementById('modal-title');
const modalSubtitle = document.getElementById('modal-subtitle');
const modalClose = document.getElementById('modal-close');
const detailBody = document.getElementById('detail-body');

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// --------------------------- Recipient rows (+/-) ---------------------------

function makeRecipientRow() {
  const row = document.createElement('div');
  row.className = 'recipient-row';
  row.setAttribute('data-recipient-row', '');
  row.innerHTML = `
    <input type="email" class="recipient-input" placeholder="recipient@example.com" />
    <button type="button" class="icon-btn remove-btn" data-remove title="Remove recipient">&minus;</button>
    <button type="button" class="icon-btn add-btn" data-add title="Add recipient">&plus;</button>
  `;
  return row;
}

function refreshRecipientButtons() {
  const rows = recipientList.querySelectorAll('[data-recipient-row]');
  rows.forEach((row, idx) => {
    const addBtn = row.querySelector('[data-add]');
    const removeBtn = row.querySelector('[data-remove]');
    addBtn.style.visibility = idx === rows.length - 1 ? 'visible' : 'hidden';
    removeBtn.disabled = rows.length === 1;
  });
}

recipientList.addEventListener('click', (e) => {
  if (e.target.matches('[data-add]')) {
    const row = e.target.closest('[data-recipient-row]');
    row.after(makeRecipientRow());
    refreshRecipientButtons();
  } else if (e.target.matches('[data-remove]')) {
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

// --------------------------------- Validation --------------------------------

function clearErrors() {
  form.querySelectorAll('.error').forEach((el) => (el.textContent = ''));
  form.querySelectorAll('.invalid').forEach((el) => el.classList.remove('invalid'));
}

function setFieldError(fieldName, message) {
  const el = form.querySelector(`[data-error-for="${fieldName}"]`);
  if (el) el.textContent = message;
  const input = form.querySelector(`#${fieldName}`);
  if (input) input.classList.add('invalid');
}

function validateClientSide(payload) {
  clearErrors();
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
  const toEmailsErrorEl = form.querySelector('[data-error-for="toEmails"]');
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

// ------------------------------ Rendering helpers ------------------------------

function extractErrorMessages(data) {
  if (Array.isArray(data.errors)) return data.errors.join(' · ');
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || JSON.stringify(d)).join(' · ');
  if (typeof data.detail === 'string') return data.detail;
  return 'Something went wrong';
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function yesNo(value) {
  if (value === true) return '<span class="badge badge-yes">Yes</span>';
  if (value === false) return '<span class="badge badge-no">No</span>';
  return '<span class="badge badge-partial">Partial</span>';
}

function countBy(recipients, key) {
  return recipients.filter((r) => r[key]).length;
}

function renderCampaigns(campaigns) {
  if (!campaigns.length) {
    trackerBody.innerHTML = '<tr class="empty-row"><td colspan="8">No campaigns yet. Create one above to see it here.</td></tr>';
    return;
  }

  trackerBody.innerHTML = campaigns
    .map((c) => {
      const created = new Date(c.createdAt).toLocaleString();
      const delivered = countBy(c.recipients, 'delivered');
      const opened = countBy(c.recipients, 'opened');
      const bounced = countBy(c.recipients, 'bounced');
      return `
        <tr data-id="${c.id}" class="campaign-row">
          <td><strong>${escapeHtml(c.campaignName)}</strong></td>
          <td>${escapeHtml(c.fromEmail)}</td>
          <td>${c.recipients.length}</td>
          <td>${delivered}/${c.recipients.length}</td>
          <td>${opened}/${c.recipients.length}</td>
          <td>${bounced}/${c.recipients.length}</td>
          <td>${escapeHtml(created)}</td>
          <td><button class="delete-btn" data-id="${c.id}">Remove</button></td>
        </tr>
      `;
    })
    .join('');
}

async function loadCampaigns() {
  try {
    const res = await fetch('/api/campaigns');
    const data = await res.json();
    renderCampaigns(Array.isArray(data) ? data : data.campaigns || []);
  } catch (err) {
    trackerBody.innerHTML = '<tr class="empty-row"><td colspan="8">Failed to load campaigns.</td></tr>';
  }
}

// --------------------------------- Detail modal ---------------------------------

function openModal(campaign) {
  modalTitle.textContent = campaign.campaignName;
  modalSubtitle.textContent = `From: ${campaign.fromEmail} · ${campaign.recipients.length} recipient(s)`;

  detailBody.innerHTML = campaign.recipients
    .map(
      (r) => `
      <tr>
        <td>${escapeHtml(r.email)}</td>
        <td>${yesNo(r.delivered)}</td>
        <td>${yesNo(r.opened)}</td>
        <td>${yesNo(r.notOpened)}</td>
        <td>${yesNo(r.linkClicked)}</td>
        <td>${yesNo(r.bounced)}${r.bounceType ? ` <span class="bounce-type">(${escapeHtml(r.bounceType)})</span>` : ''}</td>
        <td>${yesNo(r.unsubscribed)}</td>
        <td>${yesNo(r.spamReported)}</td>
      </tr>
    `
    )
    .join('');

  modal.hidden = false;
}

function closeModal() {
  modal.hidden = true;
}

modalClose.addEventListener('click', closeModal);
modal.addEventListener('click', (e) => {
  if (e.target === modal) closeModal();
});

trackerBody.addEventListener('click', async (e) => {
  if (e.target.classList.contains('delete-btn')) {
    const id = e.target.getAttribute('data-id');
    e.target.disabled = true;
    try {
      await fetch(`/api/campaigns/${id}`, { method: 'DELETE' });
      await loadCampaigns();
    } catch (err) {
      e.target.disabled = false;
    }
    return;
  }

  const row = e.target.closest('.campaign-row');
  if (!row) return;
  const id = row.getAttribute('data-id');
  try {
    const res = await fetch(`/api/campaigns/${id}`);
    const data = await res.json();
    if (res.ok) openModal(data.campaign || data);
  } catch (err) {
    /* ignore */
  }
});

// ----------------------------------- Form submit -----------------------------------

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  formStatus.textContent = '';
  formStatus.className = 'form-status';

  const payload = {
    campaignName: form.campaignName.value,
    fromEmail: form.fromEmail.value,
    toEmails: collectRecipientEmails(),
    body: form.body.value,
  };

  if (!validateClientSide(payload)) {
    formStatus.textContent = 'Please fix the errors above.';
    formStatus.classList.add('error');
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Creating…';

  try {
    const res = await fetch('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      formStatus.textContent = extractErrorMessages(data);
      formStatus.classList.add('error');
      return;
    }

    formStatus.textContent = 'Campaign created successfully.';
    formStatus.classList.add('success');
    form.reset();
    clearErrors();
    recipientList.innerHTML = '';
    recipientList.appendChild(makeRecipientRow());
    refreshRecipientButtons();
    await loadCampaigns();
  } catch (err) {
    formStatus.textContent = 'Network error — is the server running?';
    formStatus.classList.add('error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Create Campaign';
  }
});

// ----------------------------------- Auth gate -----------------------------------

const authScreen = document.getElementById('auth-screen');
const appContent = document.getElementById('app-content');
const currentUserEmailEl = document.getElementById('current-user-email');
const logoutBtn = document.getElementById('logout-btn');
const authTabsBar = document.getElementById('auth-tabs');

const loginForm = document.getElementById('login-form');
const registerForm = document.getElementById('register-form');
const forgotPasswordForm = document.getElementById('forgot-password-form');
const resetPasswordForm = document.getElementById('reset-password-form');
const loginStatus = document.getElementById('login-status');
const registerStatus = document.getElementById('register-status');
const forgotPasswordStatus = document.getElementById('forgot-password-status');
const resetPasswordStatus = document.getElementById('reset-password-status');
const authTabs = document.querySelectorAll('[data-auth-tab]');
const allAuthForms = [loginForm, registerForm, forgotPasswordForm, resetPasswordForm];

function showAuthForm(formToShow, { showTabs = true } = {}) {
  allAuthForms.forEach((f) => (f.hidden = f !== formToShow));
  authTabsBar.hidden = !showTabs;
}

authTabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    authTabs.forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    showAuthForm(tab.dataset.authTab === 'login' ? loginForm : registerForm);
  });
});

document.getElementById('forgot-password-link').addEventListener('click', () => {
  showAuthForm(forgotPasswordForm, { showTabs: false });
});

document.getElementById('back-to-login-link').addEventListener('click', () => {
  authTabs.forEach((t) => t.classList.toggle('active', t.dataset.authTab === 'login'));
  showAuthForm(loginForm);
});

// Toggle password visibility (eye buttons next to each password field)
document.querySelectorAll('.toggle-password').forEach((btn) => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.toggleFor);
    input.type = input.type === 'password' ? 'text' : 'password';
  });
});

function setButtonLoading(button, isLoading) {
  button.disabled = isLoading;
  button.classList.toggle('loading', isLoading);
}

function showApp(user) {
  authScreen.hidden = true;
  appContent.hidden = false;
  currentUserEmailEl.textContent = user.email;
  loadCampaigns();
  loadEmailAccounts();
}

function showAuthScreen() {
  authScreen.hidden = false;
  appContent.hidden = true;
}

async function checkAuth() {
  try {
    const res = await fetch('/auth/me');
    if (res.ok) {
      showApp(await res.json());
    } else {
      showAuthScreen();
    }
  } catch (err) {
    showAuthScreen();
  }
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  loginStatus.textContent = '';
  loginStatus.className = 'form-status';
  const btn = document.getElementById('login-btn');
  setButtonLoading(btn, true);
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
      loginStatus.textContent = extractErrorMessages(data);
      loginStatus.classList.add('error');
      return;
    }
    showApp(data);
  } catch (err) {
    loginStatus.textContent = 'Network error — is the server running?';
    loginStatus.classList.add('error');
  } finally {
    setButtonLoading(btn, false);
  }
});

registerForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  registerStatus.textContent = '';
  registerStatus.className = 'form-status';
  const btn = document.getElementById('register-btn');
  setButtonLoading(btn, true);
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
      registerStatus.textContent = extractErrorMessages(data);
      registerStatus.classList.add('error');
      return;
    }
    registerStatus.textContent = 'Account created — log in now.';
    registerStatus.classList.add('success');
    document.getElementById('loginEmail').value = document.getElementById('registerEmail').value;
    document.querySelector('[data-auth-tab="login"]').click();
  } catch (err) {
    registerStatus.textContent = 'Network error — is the server running?';
    registerStatus.classList.add('error');
  } finally {
    setButtonLoading(btn, false);
  }
});

forgotPasswordForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  forgotPasswordStatus.textContent = '';
  forgotPasswordStatus.className = 'form-status';
  const btn = document.getElementById('forgot-password-btn');
  setButtonLoading(btn, true);
  try {
    const res = await fetch('/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: document.getElementById('forgotEmail').value }),
    });
    const data = await res.json();
    forgotPasswordStatus.textContent = res.ok
      ? data.message || 'If that email is registered, a reset link has been sent.'
      : extractErrorMessages(data);
    forgotPasswordStatus.classList.add(res.ok ? 'success' : 'error');
  } catch (err) {
    forgotPasswordStatus.textContent = 'Network error — is the server running?';
    forgotPasswordStatus.classList.add('error');
  } finally {
    setButtonLoading(btn, false);
  }
});

resetPasswordForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  resetPasswordStatus.textContent = '';
  resetPasswordStatus.className = 'form-status';
  const btn = document.getElementById('reset-password-btn');
  setButtonLoading(btn, true);
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
      resetPasswordStatus.textContent = extractErrorMessages(data);
      resetPasswordStatus.classList.add('error');
      return;
    }
    resetPasswordStatus.textContent = 'Password updated — log in with your new password.';
    resetPasswordStatus.classList.add('success');
    window.history.replaceState({}, '', window.location.pathname);
    setTimeout(() => {
      authTabs.forEach((t) => t.classList.toggle('active', t.dataset.authTab === 'login'));
      showAuthForm(loginForm);
    }, 1500);
  } catch (err) {
    resetPasswordStatus.textContent = 'Network error — is the server running?';
    resetPasswordStatus.classList.add('error');
  } finally {
    setButtonLoading(btn, false);
  }
});

logoutBtn.addEventListener('click', async () => {
  try {
    await fetch('/auth/logout', { method: 'POST' });
  } catch (err) {
    /* ignore */
  }
  showAuthScreen();
});

// If the page was opened from a password-reset email link (?reset_token=...),
// show that form immediately and skip the normal "already logged in?" check
// — otherwise a still-valid session elsewhere would jump straight past the
// reset form into the app.
function checkForResetToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('reset_token');
  if (!token) return false;
  resetPasswordForm.dataset.token = token;
  showAuthForm(resetPasswordForm, { showTabs: false });
  showAuthScreen();
  return true;
}

// ----------------------------------- Email accounts -----------------------------------

const connectForm = document.getElementById('connect-form');
const emailAccountsList = document.getElementById('email-accounts-list');
const emailAccountsMessage = document.getElementById('email-accounts-message');

async function loadEmailAccounts() {
  try {
    const res = await fetch('/email-accounts');
    if (!res.ok) return;
    const accounts = await res.json();
    if (!accounts.length) {
      emailAccountsList.innerHTML = '<li class="email-account-row" style="justify-content:center;color:var(--muted)">No email accounts connected yet.</li>';
      return;
    }
    emailAccountsList.innerHTML = accounts
      .map(
        (a) => `
        <li class="email-account-row" data-id="${a.id}">
          <span>${escapeHtml(a.email_address)}<span class="provider-tag">${escapeHtml(a.provider)} · ${a.is_verified ? 'Verified' : 'Pending'}</span></span>
          <button type="button" class="delete-btn" data-disconnect="${a.id}">Disconnect</button>
        </li>`
      )
      .join('');
  } catch (err) {
    /* ignore */
  }
}

connectForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  emailAccountsMessage.textContent = '';
  emailAccountsMessage.className = 'form-status';
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
      emailAccountsMessage.classList.add('error');
      return;
    }
    if (data.status === 'already_connected') {
      emailAccountsMessage.textContent = `${email} is already connected.`;
      emailAccountsMessage.classList.add('success');
      await loadEmailAccounts();
      return;
    }
    if (data.authorization_url) {
      window.location.href = data.authorization_url;
    }
  } catch (err) {
    emailAccountsMessage.textContent = 'Network error — is the server running?';
    emailAccountsMessage.classList.add('error');
  }
});

emailAccountsList.addEventListener('click', async (e) => {
  const id = e.target.getAttribute('data-disconnect');
  if (!id) return;
  e.target.disabled = true;
  try {
    await fetch(`/email-accounts/${id}`, { method: 'DELETE' });
    await loadEmailAccounts();
  } catch (err) {
    e.target.disabled = false;
  }
});

// Surface the OAuth callback's redirect result (?connected=... / ?oauth_error=...)
(function showOAuthRedirectResult() {
  const params = new URLSearchParams(window.location.search);
  if (params.has('connected')) {
    emailAccountsMessage.textContent = `Connected ${params.get('connected')} successfully.`;
    emailAccountsMessage.classList.add('success');
  } else if (params.has('oauth_error')) {
    const err = params.get('oauth_error');
    const messages = {
      identity_mismatch: `You signed in as a different account than the one you tried to connect (requested ${params.get('requested')}, authenticated as ${params.get('authenticated')}).`,
      already_linked_elsewhere: 'That email is already connected to a different account.',
    };
    emailAccountsMessage.textContent = messages[err] || `Connection failed: ${err}`;
    emailAccountsMessage.classList.add('error');
  }
  if (params.has('connected') || params.has('oauth_error')) {
    window.history.replaceState({}, '', window.location.pathname);
  }
})();

if (!checkForResetToken()) {
  checkAuth();
}
