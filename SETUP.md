# Bounce Email Tracker — full setup & configuration guide

> **Architecture update:** the backend is now a Python **FastAPI** service
> backed by **Neon Postgres** (`backend/`), replacing the old Node/JSON-file
> version (`server.js` is no longer used, kept only for reference). Schema:
> `campaigns`, `recipients`, `click_logs` (full per-click IP/User-Agent
> history) — see `backend/app/models.py`.

This app has three parts:

1. **Backend** (`backend/app/main.py`) — FastAPI + SQLAlchemy + Neon
   Postgres. Serves the landing page (`public/`), the campaign REST API, and
   the tracking endpoints recipients' mail clients hit
   (`/track/open/...`, `/track/click/...`, `/track/unsubscribe/...`). Fully
   testable in Postman — see `backend/postman_collection.json`.
2. **Python sender** (`python-sender/send_campaign.py`) — sends the campaign
   over real SMTP, embedding each recipient's encrypted tracking token, and
   reports delivered/bounced status back to the backend.
3. **Python bounce poller** (`python-sender/bounce_poller.py`) — IMAP-polls
   the sending mailbox for async Delivery Status Notifications and reports
   them back the same way.

All three talk over HTTP using the campaign ID and a shared API key.

---

## Step 1 — Run the backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Config lives in `backend/.env`:

```
DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require&channel_binding=require
API_KEY=dev-local-api-key
PUBLIC_BASE_URL=http://localhost:3000
```

`DATABASE_URL` is your Neon connection string (Neon console → **Connection
Details**). Tables are created automatically on startup
(`Base.metadata.create_all`) if they don't exist — no manual migration step
needed for this project's scale. A `TRACKING_SECRET` (AES-256 key) is
auto-generated into `backend/.tracking_secret` on first run, same role as
before: encrypts the `{campaignId, recipientId}` pair inside every tracking
token.

Open http://localhost:3000 — this is your landing page with the Email
Template form and Campaign Tracker, now reading/writing Neon Postgres.

**Testing with Postman:** import `backend/postman_collection.json` — it has
every endpoint (create/list/get/delete campaign, export, delivered/bounced/
spam reporting, open/click/unsubscribe tracking, click-log listing)
pre-built with `{{base_url}}`, `{{api_key}}`, `{{campaign_id}}`,
`{{recipient_id}}` variables. Create a campaign first, copy its `id` and a
recipient `id` from the response into those collection variables, then run
the rest.

Two values matter for real-world use, both settable as environment
variables in `backend/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://localhost:3000` | The host embedded into every tracking pixel/link/unsubscribe URL put inside outgoing emails. **Must be reachable by your recipients' mail clients** — see Step 2. |
| `API_KEY` | `dev-local-api-key` | Shared secret the Python sender must send as `x-api-key` to report delivered/bounced/spam. Change this before any real use. |

---

## Step 2 — Make the tracking URLs reachable (important)

A tracking pixel or click link only works if the **recipient's mail client**
(Gmail, Outlook, etc., possibly on a different network entirely) can reach
it. `http://localhost:3000` only works for tests you run on your own
machine — real recipients can't reach your localhost.

**For real-world testing**, expose your local server with a tunnel, e.g. [ngrok](https://ngrok.com):

```bash
ngrok http 3000
```

Take the `https://xxxx.ngrok-free.app` URL it gives you, set it in
`backend/.env` as `PUBLIC_BASE_URL`, and restart the backend:

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

**For production**, deploy the backend behind a real domain with a TLS
certificate (e.g. via a reverse proxy + Let's Encrypt) and set
`PUBLIC_BASE_URL` to `https://mail.yourdomain.com`. Never send tracking
links over plain `http://` in production — most mail clients/proxies will
flag or block insecure content.

---

## Step 3 — Configure the Python sender

```bash
cd python-sender
pip install -r requirements.txt
copy .env.example .env      # PowerShell: Copy-Item .env.example .env
```

Edit `.env`:

```
API_BASE=https://xxxx.ngrok-free.app     # same value as PUBLIC_BASE_URL above
API_KEY=dev-local-api-key                 # same value as Node's API_KEY
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-sending-address@gmail.com
SMTP_PASS=your-app-password
SMTP_USE_TLS=1
```

Notes on SMTP credentials:
- Gmail/Google Workspace: you need an **App Password** (requires 2FA enabled
  on the account), not your normal login password.
- Any transactional provider (SES, SendGrid, Mailgun, Postmark, etc.) also
  works here via their SMTP relay — you just point `SMTP_HOST`/`SMTP_USER`/
  `SMTP_PASS` at them. See Step 6 for why this is worth doing for anything
  beyond testing.
- For real deliverability (avoiding spam folders), the sending domain in
  `fromEmail` should have proper **SPF, DKIM, and DMARC** DNS records
  configured — this is independent of this app and is required by every
  major mail provider.

---

## Step 4 — Create a campaign

1. On the landing page, fill in **Campaign Name**, **From Email**, one or
   more **To Emails** (use the `+`/`-` buttons to add/remove recipients),
   and the **Email Body**.
2. Click **Create Campaign** — it appears immediately in the **Campaign
   Tracker** table below with status "Scheduled" and 0/N delivered.
3. Get its campaign ID either from the tracker (inspect the row, or add a
   quick `console.log` / check `data/campaigns.json`) or via:
   ```bash
   curl http://localhost:3000/api/campaigns
   ```

---

## Step 5 — Send it

```bash
cd python-sender
python send_campaign.py --campaign-id <CAMPAIGN_ID>
```

For each recipient, this:
1. Fetches the campaign + that recipient's encrypted tracking token from
   the Node API (`GET /api/campaigns/:id/export`).
2. Builds an HTML email with:
   - Any `http(s)://` link in the body rewritten to route through
     `/track/click/<token>?url=<original>` (records the click, then
     redirects the recipient to the real URL).
   - A 1×1 tracking pixel `<img>` pointing at `/track/open/<token>.gif`
     (records the open — see the "Opened" caveat below).
   - An "Unsubscribe" footer link pointing at `/track/unsubscribe/<token>`.
3. Sends via SMTP. Depending on the outcome, reports back to the Node API:
   - Accepted by the SMTP server → `POST .../delivered`
   - Rejected immediately (`SMTPRecipientsRefused`, 5xx response) →
     `POST .../bounced` with `type: "hard"`
   - Temporary failure (4xx response, connection error) →
     `POST .../bounced` with `type: "soft"`

Then in the web app, click the campaign's row in the Campaign Tracker to
open the detail view — it shows Delivered / Opened / Not Opened / Link
Clicked / Bounced / Unsubscribed / Spam per recipient, exactly matching
what actually happened.

---

## Step 6 — What each tracked status actually means (and its limits)

| Status | How it's tracked here | Reliability |
|---|---|---|
| **Delivered** | SMTP server accepted the message at send time (no exception raised) | Good, but not a 100% guarantee — see "async bounces" below |
| **Opened** | Recipient's mail client fetched the tracking pixel | Good but not universal — many clients (Apple Mail Privacy Protection, image-blocking clients) either always prefetch (false positive) or never load images (false negative) |
| **Not Opened** | Derived: delivered = true, opened = false, bounced = false | As reliable as "Opened" |
| **Link Clicked** | Recipient's client requested the click-tracking redirect | Reliable when it fires; doesn't fire if the recipient never clicks |
| **Bounced** | Either an immediate SMTP rejection (hard) or a 4xx temporary failure (soft) at send time | Only catches *synchronous* bounces — see below for async bounces |
| **Unsubscribed** | Recipient clicked the unsubscribe link | Reliable |
| **Spam** | **Not auto-detectable** with raw SMTP | Only becomes available if you switch to a real ESP (see below) and register for their spam-complaint feedback loop |

### Async bounces (the gap in "hard SMTP catch")

Some bounces don't happen at send time — e.g. a full mailbox, a greylisting
delay, or a bounce generated minutes later by the recipient's server. Raw
SMTP (what `send_campaign.py` uses) can't see these. Two ways to close this
gap:

- **Bounce mailbox + IMAP polling** — implemented here as
  `python-sender/bounce_poller.py`. It logs into the sending mailbox
  **read-only** over IMAP, finds Delivery Status Notification (RFC 3464)
  emails, parses out the failed recipient + hard/soft type + SMTP
  diagnostic, matches it to the right campaign recipient, and reports it
  via the same `POST /api/campaigns/:id/recipients/:id/bounced` endpoint
  `send_campaign.py` uses. It tracks which messages it already processed
  in `.bounce_poller_state.json` so re-running is idempotent.

  ```bash
  cd python-sender
  python bounce_poller.py              # one pass
  python bounce_poller.py --watch 60   # loop forever, poll every 60s
  python bounce_poller.py --dry-run    # parse & print only, no API calls
  ```

  Verified against a real Gmail bounce: sending to a nonexistent address
  produced a genuine "Delivery Status Notification (Failure)" email a few
  minutes later, which the poller correctly parsed (recipient, hard/soft
  type, and the full `550 5.1.1 ...` diagnostic) and reported — flipping
  that recipient to `bounced: true` in the tracker. Re-running reported
  zero new bounces, confirming idempotency.

  Caveat: since there's no unique return-path per recipient, matching is by
  email address + recency across campaigns — good enough for most cases,
  but if the same address appears in multiple un-resolved campaigns
  concurrently it could match the wrong one. A production setup would use
  a unique `Return-Path`/VERP address per send to remove this ambiguity.
- **Switch to a transactional ESP** (Amazon SES, SendGrid, Mailgun,
  Postmark): they detect bounces (and spam complaints) server-side and can
  push them to you as a webhook in real time. You'd add one more endpoint to
  `server.js`, e.g. `POST /webhooks/ses`, that verifies the payload and
  calls the same internal bounce/spam recording logic already in
  `handleApi`. This is the standard production approach and also gives you
  real spam-complaint tracking via their feedback loop — something raw SMTP
  fundamentally cannot provide.

For a real production system, the ESP + webhook route is strongly
recommended over the bounce-mailbox approach.

---

## Security checklist before any real-world use

- [ ] Set a strong random `API_KEY` (not the dev default) and keep it out of
      version control.
- [ ] Don't commit `data/.tracking_secret` or `python-sender/.env`.
- [ ] Serve `PUBLIC_BASE_URL` over HTTPS in production.
- [ ] Rate-limit `/track/*` and the reporting endpoints if exposed publicly.
- [ ] Make sure your unsubscribe link actually suppresses future sends
      (check `unsubscribed` before including a recipient in your next
      `send_campaign.py` run — CAN-SPAM/GDPR compliance).
