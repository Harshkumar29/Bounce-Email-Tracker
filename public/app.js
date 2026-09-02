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

loadCampaigns();
