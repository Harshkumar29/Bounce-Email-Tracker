# Bounce Email Tracker — full setup & configuration guide

This reflects the app as it actually exists today. If you read an older
copy of this file: the Node/JSON version, Render deployment, and the
Resend integration are all gone — this is the current, accurate picture.

---

## 1. What this app actually is now

A single **FastAPI** backend (`backend/`) backed by **Neon Postgres**
that does all of the following in one process:

- Serves the frontend (`public/index.html` / `app.js` — Tailwind CDN +
  Lucide icons, a dark dashboard UI)
- **User accounts**: register/login/logout, forgot/reset password, all via
  Secure+HttpOnly cookie sessions (`backend/app/auth.py`,
  `backend/app/routers/auth_router.py`) — campaigns are private per user
- **Connecting a Google mailbox via OAuth** (`backend/app/oauth/`) — identity
  only so far (Microsoft not implemented; needs its own Entra app
  registration first)
- **Campaigns**: create one (Campaign Name, From Email, multiple To Emails,
  Body) and it **sends automatically the instant you submit** — no separate
  script, no copying an ID (`backend/app/mailer.py`, run as a FastAPI
  `BackgroundTask` from `POST /api/campaigns`)
- **Tracking**: `/track/open/...`, `/track/click/...`, `/track/unsubscribe/...`
  — hit by the recipient's real mail client, encrypted per-recipient tokens
- **Dashboard**: aggregate delivered/opened/not-opened/clicked/bounced/
  unsubscribed/spam stats and a live activity feed, computed from real data
  across all your campaigns

Two small standalone Python scripts in `python-sender/` are optional
extras, not required for normal use — see §7.

**Live deployment:** Railway, at
`https://bounce-email-tracker-production.up.railway.app` (Render was tried
first but blocks outbound SMTP outright; Railway restricts it unpredictably
too — see §6 for the fix, self-hosting your own mail server).

---

## 2. Local setup

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000 --app-dir backend
```

(Running from the repo root with `--app-dir backend` — rather than `cd
backend` first — matters if you ever deploy this the same way Railway does;
locally either works.)

Open http://localhost:3000 — you'll land on the login/register screen.

### `backend/.env` — every variable, what it's for

Copy `backend/.env.example` to `backend/.env` and fill in real values.
Never commit the real `.env` (already gitignored).

| Variable | Required? | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | Neon (or any Postgres) connection string. Get it from the Neon console → Connection Details. Tables are created automatically on startup (`Base.metadata.create_all`) — no manual migration step. |
| `API_KEY` | Yes | Shared secret the optional `python-sender/` scripts present as `x-api-key` for the few endpoints that aren't user-session-gated (`/api/campaigns/:id/export`, delivered/bounced/spam reporting). Not used by the normal browser UI flow at all. |
| `PUBLIC_BASE_URL` | Yes | Host embedded into every tracking pixel/link/unsubscribe URL, and into the Google OAuth redirect URI. **Must include the scheme** (`https://...`) — a bare host silently breaks tracking links (there's a guard in `backend/app/config.py` that auto-prepends `https://` if you forget, but don't rely on it). Must be publicly reachable by recipients' mail clients — `localhost` only works for tests on your own machine. |
| `TRACKING_SECRET` | Recommended | AES-256 key (64 hex chars) encrypting the `{campaignId, recipientId}` pair inside every tracking token. Auto-generated into `backend/.tracking_secret` if left unset — but that file **won't survive a redeploy** on most hosts, silently invalidating every previously-sent email's tracking links. Set it explicitly. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `OAUTH_ENCRYPTION_KEY` | Recommended | Same idea, separate key, for encrypting OAuth access/refresh tokens at rest (`backend/.oauth_secret` if unset). Deliberately a different key from `TRACKING_SECRET` so rotating one doesn't invalidate the other. |
| `COOKIE_SECURE` | Situational | `1` (default) marks the session cookie `Secure` — required for any real `https://` deployment. Set to `0` **only** when testing over plain `http://localhost`, since real browsers refuse to store a `Secure` cookie over plain HTTP. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional | Enables the "Connect email" → Google OAuth flow. Leave both unset and that feature just returns a clean `400` instead of crashing. See §5 for how to get these. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_USE_TLS` | Yes (for sending) | Plain SMTP credentials `mailer.py` uses to actually send campaigns. See §6 — this is the part that needs a host that doesn't block outbound SMTP, or your own mail server. |

---

## 3. Using the app

1. **Register** an account, then **log in**.
2. *(Optional)* **Mailboxes** tab → connect a Google account via OAuth —
   see §5. This doesn't yet change how campaigns are sent (that's still via
   the `SMTP_*` credentials below); it's identity-linking only for now.
3. **New Campaign** tab → fill in Campaign Name, From Email, one or more To
   Emails (`+`/`-` to add/remove), and the Body.
4. Click **Dispatch Campaign** — this both creates the campaign row *and*
   sends it, via a background task, using the `SMTP_*` credentials in
   `backend/.env`. No separate step.
5. **Campaigns** tab shows every campaign as a card with a status pill
   (`Scheduled` → `Sending` → `Sent`) — click **Details** for the
   per-recipient breakdown (Delivered/Opened/Not Opened/Clicked/Bounced/
   Unsubscribed/Spam).
6. **Dashboard** tab shows aggregate stats across all your campaigns plus a
   live feed of open/click/bounce/unsubscribe events, newest first.

**Testing via Postman instead of the UI:** import
`backend/postman_collection.json` — every endpoint is pre-built with
`{{base_url}}`/`{{api_key}}`/`{{campaign_id}}`/`{{recipient_id}}`
variables. Note the campaign endpoints now require a logged-in session
cookie, not just the API key — log in via `/auth/login` in a browser or
carry the `session_id` cookie through in Postman.

---

## 4. Deploying (Railway)

The repo includes `railway.json` and a root-level `requirements.txt` —
**both exist for non-obvious reasons, don't remove them**:

- Root `requirements.txt` (a full, self-contained copy of
  `backend/requirements.txt`, not a `-r backend/requirements.txt`
  reference): Railway's builder (Railpack) only auto-detects "this is a
  Python app" from files at the **repo root**. With nothing there but
  `public/index.html`, it misdetects the whole repo as a static site and
  serves it with Caddy — meaning the actual backend never runs at all. A
  `-r backend/requirements.txt` reference doesn't work either: Railpack
  copies only `requirements.txt` into an early build layer before the rest
  of the repo exists, so the reference target isn't there yet. Self-contained
  is the only combination that survives both problems.
- `railway.json`: explicitly sets `startCommand` to
  `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir backend`.
  In Railway's dashboard, **do not** also set a Root Directory to `backend`
  — that would exclude `public/` from the build entirely, breaking the
  frontend (`main.py` resolves `public/` relative to its own file path,
  which only works if the whole repo is present).

Set every variable from §2's table in Railway's **Variables** tab, with
`PUBLIC_BASE_URL` set to your actual Railway domain (Settings → Networking
→ Public Networking — generate one if none exists; note `*.railway.internal`
is Railway's **private** network address and is never usable here).

---

## 5. Google OAuth setup (for the "Connect email" feature)

1. **console.cloud.google.com** → create/select a project.
2. **APIs & Services → OAuth consent screen** → "External" (or "Internal"
   for a Workspace org) → add scopes `openid`, `.../auth/userinfo.email`,
   `.../auth/userinfo.profile` → under **Test users**, add every Gmail
   address you'll test with (required while the app is in "Testing" mode).
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → type **Web application**.
4. Under **Authorized redirect URIs**, add exactly:
   ```
   <PUBLIC_BASE_URL>/oauth/google/callback
   ```
   e.g. `https://bounce-email-tracker-production.up.railway.app/oauth/google/callback`
   — must match byte-for-byte (scheme, no trailing slash) or you get
   `Error 400: redirect_uri_mismatch`.
5. Copy the **Client ID** and **Client Secret** into `GOOGLE_CLIENT_ID` /
   `GOOGLE_CLIENT_SECRET`.

Microsoft isn't implemented — it needs its own Entra ID app registration
first, the same blocker Google had before these credentials existed. The
provider abstraction (`backend/app/oauth/providers.py`) is ready for it;
adding `MicrosoftProvider` there is the only new code needed once you have
Entra credentials.

---

## 6. Actually sending email — the SMTP restriction problem

**Both Render and Railway restrict outbound SMTP** on their free tiers
(Render blocks it outright; Railway's restriction has shown up as
`[Errno 101] Network is unreachable` in testing — a real OS-level network
failure, not a credentials bug). Two ways around this:

### Option A — a transactional email API (quick, but has real trade-offs)

Services like Resend, SendGrid, Mailgun, SES send over plain HTTPS,
sidestepping the SMTP restriction entirely. **This app tried Resend and
reverted it** — worth knowing why before going back down that path:
- Their free/sandbox tier only delivers to **the API account's own email
  address** until you verify a domain you own — not usable for real
  campaigns to real recipients until then.
- Domain verification requires DNS records at a domain **you actually
  own** — you cannot verify a host-provided subdomain like
  `*.up.railway.app` or `*.onrender.com`, since you don't control that DNS
  zone.

If you want to go this route again: get a real domain (see the "no domain
available" options discussed in-repo — free options are limited and
mostly unreliable now; a cheap purchased domain is the practical path),
verify it with the provider, then re-add an HTTPS-API branch to
`mailer.py`'s `_send_one()`.

### Option B — self-host your own mail server (what this app is currently configured for)

Point `SMTP_HOST`/`SMTP_USER`/`SMTP_PASS` at a mail server you run
yourself, on a domain you own — no third-party sandbox restriction, no
recipient allowlist, real SPF/DKIM/DMARC under your control.

Full step-by-step (Ubuntu/Debian VPS with root SSH):

1. **DNS** (at your domain registrar): `A` record for `mail.yourdomain.com`
   → your server's IP; `MX` record for `@` → `mail.yourdomain.com`;
   `TXT` SPF record `v=spf1 mx a ip4:<server-ip> -all`; `TXT` DMARC record
   at `_dmarc` → `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`. Also
   ask your **VPS provider** (not DNS) to set the **PTR/reverse-DNS**
   record for your IP to `mail.yourdomain.com` — Gmail/Outlook reject mail
   from servers with no matching PTR.
2. **Check outbound port 25 isn't blocked** by your VPS provider (`nc -zv
   smtp.gmail.com 25` from the server) — if it is, ask their support to
   unblock it (routine request at DigitalOcean/Vultr/Linode/etc.).
3. **Install Postfix**: `sudo apt install postfix` (choose "Internet Site",
   system mail name = your domain).
4. **TLS cert**: `sudo certbot certonly --standalone -d mail.yourdomain.com`.
5. **SASL auth** so this app can log in with a username/password: create a
   system user (`sudo useradd -m -s /usr/sbin/nologin mailer && sudo passwd
   mailer`), install `sasl2-bin`, configure `saslauthd` with
   `MECHANISMS="pam"`.
6. **Configure Postfix** (`/etc/postfix/main.cf` + `/etc/postfix/master.cf`)
   for TLS + SASL on the submission port (587), only relaying for
   authenticated senders.
7. **DKIM**: install `opendkim`/`opendkim-tools`, `opendkim-genkey` for your
   domain, add the generated public key as a `TXT` record at
   `mail._domainkey`, wire it into Postfix as a milter.
8. **Firewall**: `sudo ufw allow 587/tcp` (and `25/tcp` if you need inbound
   mail too).
9. **Test**: `swaks --to you@gmail.com --from mailer@yourdomain.com --server
   mail.yourdomain.com:587 --auth LOGIN --auth-user mailer --auth-password
   '...' --tls`, then check the received message's headers ("Show
   original" in Gmail) for `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.
10. Set in `backend/.env` (and wherever it's deployed):
    ```
    SMTP_HOST=mail.yourdomain.com
    SMTP_PORT=587
    SMTP_USER=mailer
    SMTP_PASS=<the password you set>
    SMTP_USE_TLS=1
    ```

**What owning your domain does and doesn't unlock:** you can send `From`
any address `@yourdomain.com` freely once this is set up — you own that
domain's SPF/DKIM records. You still can never legitimately send `From` an
address on a domain you don't own (`gmail.com`, someone else's domain,
etc.) — every major receiving mail server checks this independently, and
no hosting choice changes that; deliberately spoofing a From address you
don't control is what SPF/DKIM/DMARC exist to stop.

A brand-new sending domain also has **no reputation yet** — expect some
early mail to land in spam regardless of correct SPF/DKIM/DMARC, until
Gmail/Outlook build trust in your domain from real, wanted sending volume.

---

## 7. The optional `python-sender/` scripts

These predate the backend's auto-send (§3, step 4) and are **not needed
for normal use** — kept for manual testing / edge cases:

- **`send_campaign.py`** — manually sends (or re-sends) one campaign by ID
  via `GET /api/campaigns/:id/export` + SMTP, reporting delivered/bounced
  back via the API-key-gated endpoints. Re-running it re-sends to *every*
  recipient regardless of prior delivery status — don't run it on a
  campaign the backend already auto-sent unless you mean to double-send.
  ```bash
  cd python-sender
  pip install -r requirements.txt
  copy .env.example .env    # fill in API_BASE, API_KEY, SMTP_*
  python send_campaign.py --campaign-id <CAMPAIGN_ID>
  ```
- **`bounce_poller.py`** — the backend has no automated way to catch
  *async* bounces (a full mailbox, a delayed rejection minutes after
  send — see the "Async bounces" note below). This script logs into the
  sending mailbox **read-only** over IMAP, parses Delivery Status
  Notification emails, and reports the bounce back via the API. Verified
  against a real Gmail bounce: correctly parsed the recipient, hard/soft
  type, and full SMTP diagnostic, and re-running reports zero new bounces
  (idempotent — tracked in `.bounce_poller_state.json`).
  ```bash
  cd python-sender
  python bounce_poller.py              # one pass
  python bounce_poller.py --watch 60   # loop forever, poll every 60s
  python bounce_poller.py --dry-run    # parse & print only, no API calls
  ```
  Caveat: matching a bounce to a recipient is by email address + recency
  across campaigns (no unique return-path per send yet) — fine for typical
  use, but could match the wrong campaign if the same address appears in
  multiple concurrently-unresolved sends.

---

## 8. What each tracked status actually means (and its limits)

| Status | How it's tracked | Reliability |
|---|---|---|
| **Delivered** | SMTP server accepted the message at send time | Good, but not a guarantee — misses async bounces (§7) |
| **Opened** | Recipient's mail client fetched the tracking pixel | Not universal — Apple Mail Privacy Protection always prefetches (false positive); image-blocking clients never load it (false negative) |
| **Not Opened** | Derived: delivered = true, opened = false, bounced = false | As reliable as "Opened" |
| **Link Clicked** | Recipient's client requested the click-tracking redirect | Reliable when it fires |
| **Bounced** | Immediate SMTP rejection (hard) or 4xx (soft) at send time, or a later async DSN parsed by `bounce_poller.py` | Only synchronous bounces are automatic; async needs the poller running |
| **Unsubscribed** | Recipient clicked the unsubscribe link | Reliable |
| **Spam** | **Not auto-detectable** over raw SMTP — no protocol signal exists for it | Only ever becomes available via an ESP's spam-complaint feedback loop (a reason to reconsider Option A in §6 for production use, despite its trade-offs) |

---

## Security checklist before any real-world use

- [ ] Set strong random values for `API_KEY`, `TRACKING_SECRET`,
      `OAUTH_ENCRYPTION_KEY` (not defaults) — none of these belong in
      version control (already gitignored: `backend/.env`,
      `backend/.tracking_secret`, `backend/.oauth_secret`,
      `python-sender/.env`).
- [ ] `COOKIE_SECURE=1` (default) on any real deployment — only `0` for
      local `http://localhost`.
- [ ] `PUBLIC_BASE_URL` must be `https://` in production.
- [ ] Google OAuth: keep the app in "Testing" mode with an explicit test-user
      list until you're ready for a real audience, since it's currently only
      using identity scopes.
- [ ] Rate-limit `/track/*` and the reporting endpoints if this ever handles
      real external traffic at scale.
- [ ] Respect `unsubscribed` before including a recipient in any future
      send (CAN-SPAM/GDPR compliance) — not currently enforced automatically
      if you reuse `send_campaign.py` manually.
