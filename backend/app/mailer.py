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

Sends over plain SMTP. Each user can connect their own mailbox's SMTP
credentials (see routers/email_accounts_router.py's POST /email-accounts/smtp)
— a campaign whose From Email matches one of that user's connected SMTP
accounts sends through those credentials. Falls back to the global
SMTP_HOST/PORT/USER/PASS in .env if no matching connected account exists,
so a bare install with no per-user mailboxes still works — see SETUP.md.
"""

import html
import os
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from . import models
from .crypto import decrypt_secret
from .db import SessionLocal
from .models import now_utc

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_SMTP_HOST = os.environ.get("SMTP_HOST")
DEFAULT_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
DEFAULT_SMTP_USER = os.environ.get("SMTP_USER")
DEFAULT_SMTP_PASS = os.environ.get("SMTP_PASS")
DEFAULT_SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"

URL_RE = re.compile(r"(https?://[^\s<]+)")


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool


def _default_smtp_config() -> Optional[SmtpConfig]:
    if not (DEFAULT_SMTP_HOST and DEFAULT_SMTP_USER and DEFAULT_SMTP_PASS):
        return None
    return SmtpConfig(DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, DEFAULT_SMTP_USER, DEFAULT_SMTP_PASS, DEFAULT_SMTP_USE_TLS)


def resolve_smtp_config(db: Session, campaign: models.Campaign) -> Optional[SmtpConfig]:
    """A user's own connected SMTP mailbox (matched by From Email) takes
    priority over the install-wide default in .env."""
    if campaign.user_id:
        account = (
            db.query(models.EmailAccount)
            .filter(
                models.EmailAccount.user_id == campaign.user_id,
                models.EmailAccount.normalized_email == campaign.from_email.strip().lower(),
                models.EmailAccount.provider == "smtp",
                models.EmailAccount.is_active.is_(True),
            )
            .first()
        )
        if account and account.smtp_credential:
            cred = account.smtp_credential
            password = decrypt_secret(cred.smtp_password_encrypted)
            if password is not None:
                return SmtpConfig(cred.smtp_host, cred.smtp_port, cred.smtp_username, password, cred.use_tls)

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


def _send_via_smtp(
    campaign: models.Campaign, recipient: models.Recipient, html_body: str, config: SmtpConfig
) -> None:
    msg = EmailMessage()
    msg["Subject"] = campaign.campaign_name
    msg["From"] = campaign.from_email
    msg["To"] = recipient.email
    msg.set_content(campaign.body)
    msg.add_alternative(html_body, subtype="html")

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


def _send_one(campaign: models.Campaign, recipient: models.Recipient, tracking_urls: dict, config: SmtpConfig) -> None:
    html_body = build_html_body(
        campaign.body,
        tracking_urls["clickBaseUrl"],
        tracking_urls["unsubscribeUrl"],
        tracking_urls["openPixelUrl"],
    )
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

        config = resolve_smtp_config(db, campaign)
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
