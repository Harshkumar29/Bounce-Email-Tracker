"""
Sends a campaign's emails immediately after creation, in-process, so the
UI's "Create Campaign" button is the only action needed — no copying a
campaign ID into a separate script.

Runs as a FastAPI BackgroundTask (see main.py's create_campaign), which
executes after the HTTP response has already been sent, in the same
process/worker. That's the right fit for this app's volume (personal/small
campaigns); if you ever need this to survive a worker restart mid-send or
scale to a large recipient list, move this to a real task queue (Celery/RQ)
instead — a BackgroundTask is fire-and-forget within one process.

Three ways a campaign can actually get sent, tried in this order per
campaign (matched by From Email against the owning user's connected
accounts):
  1. Gmail API, via a Google-connected account with the gmail.send scope
     granted — no password ever touches this app. This is what makes
     "every user connects their own mailbox" actually scale: App Passwords
     don't (see SETUP.md's note on why this exists).
  2. A per-user custom SMTP mailbox (routers/email_accounts_router.py's
     POST /email-accounts/smtp).
  3. The install-wide SMTP_HOST/PORT/USER/PASS default in .env, so a bare
     install with no per-user mailboxes connected still works.
"""

import base64
import html
import logging
import os
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from . import models
from .crypto import decrypt_secret, encrypt_secret
from .db import SessionLocal
from .models import now_utc

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger(__name__)

DEFAULT_SMTP_HOST = os.environ.get("SMTP_HOST")
DEFAULT_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
DEFAULT_SMTP_USER = os.environ.get("SMTP_USER")
DEFAULT_SMTP_PASS = os.environ.get("SMTP_PASS")
DEFAULT_SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

URL_RE = re.compile(r"(https?://[^\s<]+)")


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool


@dataclass
class GoogleSendConfig:
    access_token: str


SendConfig = Union[SmtpConfig, GoogleSendConfig]


def _default_smtp_config() -> Optional[SmtpConfig]:
    if not (DEFAULT_SMTP_HOST and DEFAULT_SMTP_USER and DEFAULT_SMTP_PASS):
        return None
    return SmtpConfig(DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, DEFAULT_SMTP_USER, DEFAULT_SMTP_PASS, DEFAULT_SMTP_USE_TLS)


def _refresh_google_access_token(db: Session, account: models.EmailAccount) -> Optional[str]:
    """Returns a currently-valid access token, refreshing it first if it's
    expired (or about to be) and a refresh token is on file. Persists the
    refreshed token back to the DB so the next send doesn't re-refresh."""
    cred = account.oauth_credential
    if not cred:
        return None

    if cred.expires_at and cred.expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
        return decrypt_secret(cred.access_token_encrypted)

    refresh_token = decrypt_secret(cred.refresh_token_encrypted) if cred.refresh_token_encrypted else None
    if not refresh_token or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        # No refresh token on file (shouldn't happen — access_type=offline
        # always requests one) or the app's own OAuth client isn't
        # configured. Either way, fall through to whatever's next.
        return decrypt_secret(cred.access_token_encrypted)

    try:
        resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError:
        return None

    access_token = data.get("access_token")
    if not access_token:
        return None

    cred.access_token_encrypted = encrypt_secret(access_token)
    if "expires_in" in data:
        cred.expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    db.commit()
    return access_token


def resolve_send_config(db: Session, campaign: models.Campaign) -> Optional[SendConfig]:
    """Priority per campaign, matched by From Email against the owning
    user's connected accounts: Gmail API (no password ever needed) > the
    user's own SMTP mailbox > the install-wide SMTP default."""
    normalized_from = campaign.from_email.strip().lower()

    if campaign.user_id:
        google_account = (
            db.query(models.EmailAccount)
            .filter(
                models.EmailAccount.user_id == campaign.user_id,
                models.EmailAccount.normalized_email == normalized_from,
                models.EmailAccount.provider == "google",
                models.EmailAccount.is_active.is_(True),
            )
            .first()
        )
        log.info(
            "resolve_send_config: campaign=%s from=%s google_account=%s scopes=%s",
            campaign.id, normalized_from,
            google_account.id if google_account else None,
            google_account.oauth_credential.scopes if google_account and google_account.oauth_credential else None,
        )
        if (
            google_account
            and google_account.oauth_credential
            and google_account.oauth_credential.scopes
            and GMAIL_SEND_SCOPE in google_account.oauth_credential.scopes
        ):
            token = _refresh_google_access_token(db, google_account)
            if token:
                log.info("resolve_send_config: campaign=%s -> GoogleSendConfig", campaign.id)
                return GoogleSendConfig(access_token=token)
            log.warning("resolve_send_config: campaign=%s google token refresh failed, falling back", campaign.id)
            # Token refresh failed (revoked/expired refresh token) — fall
            # through to SMTP rather than silently not sending at all.

        smtp_account = (
            db.query(models.EmailAccount)
            .filter(
                models.EmailAccount.user_id == campaign.user_id,
                models.EmailAccount.normalized_email == normalized_from,
                models.EmailAccount.provider == "smtp",
                models.EmailAccount.is_active.is_(True),
            )
            .first()
        )
        if smtp_account and smtp_account.smtp_credential:
            cred = smtp_account.smtp_credential
            password = decrypt_secret(cred.smtp_password_encrypted)
            if password is not None:
                log.info("resolve_send_config: campaign=%s -> SmtpConfig (user mailbox %s)", campaign.id, smtp_account.id)
                return SmtpConfig(cred.smtp_host, cred.smtp_port, cred.smtp_username, password, cred.use_tls)

    log.info("resolve_send_config: campaign=%s -> default SMTP fallback (no matching connected mailbox for %s)", campaign.id, normalized_from)
    return _default_smtp_config()


def _wrap_link(match: re.Match, click_base_url: str) -> str:
    original = match.group(1)
    tracked = f"{click_base_url}?url={quote(original, safe='')}"
    return f'<a href="{tracked}">{html.escape(original)}</a>'


def build_html_body(plain_body: str, click_base_url: str, unsubscribe_url: str, pixel_url: str) -> str:
    body_html = html.escape(plain_body)
    body_html = URL_RE.sub(lambda m: _wrap_link(m, click_base_url), body_html)
    body_html = body_html.replace("\n", "<br>")

    return f"""\
<html>
  <body style="font-family:Arial,sans-serif;font-size:14px;color:#1c2333;">
    <div>{body_html}</div>
    <hr style="margin:24px 0;border:none;border-top:1px solid #e3e7f0;">
    <p style="font-size:11px;color:#9aa1b3;">
      Don't want these emails?
      <a href="{unsubscribe_url}" style="color:#9aa1b3;">Unsubscribe</a>
    </p>
    <img src="{pixel_url}" width="1" height="1" alt="" style="display:none;">
  </body>
</html>"""


def _mark_bounced(recipient: models.Recipient, bounce_type: str, reason: str) -> None:
    recipient.bounced = True
    recipient.bounce_type = bounce_type
    recipient.bounce_reason = reason[:2000]
    recipient.bounced_at = now_utc()


def _mark_delivered(recipient: models.Recipient) -> None:
    recipient.delivered = True
    recipient.delivered_at = now_utc()


def _build_message(campaign: models.Campaign, recipient: models.Recipient, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = campaign.campaign_name
    msg["From"] = campaign.from_email
    msg["To"] = recipient.email
    msg.set_content(campaign.body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _send_via_smtp(
    campaign: models.Campaign, recipient: models.Recipient, html_body: str, config: SmtpConfig
) -> None:
    msg = _build_message(campaign, recipient, html_body)

    try:
        if config.use_tls:
            with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(config.username, config.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(config.host, config.port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(config.username, config.password)
                smtp.send_message(msg)

    except smtplib.SMTPRecipientsRefused as exc:
        _mark_bounced(recipient, "hard", str(exc))
        return
    except smtplib.SMTPResponseException as exc:
        bounce_type = "soft" if 400 <= exc.smtp_code < 500 else "hard"
        _mark_bounced(recipient, bounce_type, str(exc.smtp_error))
        return
    except (smtplib.SMTPException, OSError) as exc:
        _mark_bounced(recipient, "soft", str(exc))
        return

    _mark_delivered(recipient)


def _send_via_gmail_api(
    campaign: models.Campaign, recipient: models.Recipient, html_body: str, config: GoogleSendConfig
) -> None:
    msg = _build_message(campaign, recipient, html_body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    try:
        resp = httpx.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {config.access_token}", "Content-Type": "application/json"},
            json={"raw": raw},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        _mark_bounced(recipient, "soft", f"Gmail API request failed: {exc}")
        return

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            detail = resp.text
        # 400/403/404 are the recipient/message being rejected outright;
        # 401 (expired/revoked token) and 5xx are worth retrying later.
        bounce_type = "hard" if resp.status_code in (400, 403, 404) else "soft"
        _mark_bounced(recipient, bounce_type, f"Gmail API {resp.status_code}: {detail}")
        return

    _mark_delivered(recipient)


def _send_one(campaign: models.Campaign, recipient: models.Recipient, tracking_urls: dict, config: SendConfig) -> None:
    html_body = build_html_body(
        campaign.body,
        tracking_urls["clickBaseUrl"],
        tracking_urls["unsubscribeUrl"],
        tracking_urls["openPixelUrl"],
    )
    if isinstance(config, GoogleSendConfig):
        _send_via_gmail_api(campaign, recipient, html_body, config)
    else:
        _send_via_smtp(campaign, recipient, html_body, config)


def send_plain_email(to_email: str, subject: str, body_text: str) -> bool:
    """One-off transactional email (password reset, etc.) — always uses the
    install-wide default SMTP config, never a per-user mailbox, since it
    isn't tied to any campaign/user context. Returns False (logs, doesn't
    raise) if SMTP isn't configured or the send fails."""
    config = _default_smtp_config()
    if not config:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.username
    msg["To"] = to_email
    msg.set_content(body_text)

    try:
        if config.use_tls:
            with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(config.username, config.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(config.host, config.port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(config.username, config.password)
                smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False


def send_campaign_background(campaign_id: uuid.UUID, public_base_url: str) -> None:
    db = SessionLocal()
    try:
        campaign = db.get(models.Campaign, campaign_id)
        if not campaign:
            return

        config = resolve_send_config(db, campaign)
        if not config:
            return  # no per-user mailbox and no default configured — leave "Scheduled"

        campaign.status = "Sending"
        db.commit()

        for recipient in campaign.recipients:
            tracking_urls = {
                "openPixelUrl": f"{public_base_url}/track/open/{recipient.token}.gif",
                "clickBaseUrl": f"{public_base_url}/track/click/{recipient.token}",
                "unsubscribeUrl": f"{public_base_url}/track/unsubscribe/{recipient.token}",
            }
            _send_one(campaign, recipient, tracking_urls, config)
            db.commit()

        campaign.status = "Sent"
        db.commit()
    finally:
        db.close()
