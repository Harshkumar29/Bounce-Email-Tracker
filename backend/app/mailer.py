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

Sends over plain SMTP only. Point SMTP_HOST/PORT/USER/PASS (in .env) at
your own mail server for unrestricted sending — see SETUP.md.
"""

import html
import os
import re
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from . import models
from .db import SessionLocal
from .models import now_utc

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"

URL_RE = re.compile(r"(https?://[^\s<]+)")


def _is_sending_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


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


def _send_via_smtp(campaign: models.Campaign, recipient: models.Recipient, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = campaign.campaign_name
    msg["From"] = campaign.from_email
    msg["To"] = recipient.email
    msg.set_content(campaign.body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(SMTP_USER, SMTP_PASS)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(SMTP_USER, SMTP_PASS)
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


def _send_one(campaign: models.Campaign, recipient: models.Recipient, tracking_urls: dict) -> None:
    html_body = build_html_body(
        campaign.body,
        tracking_urls["clickBaseUrl"],
        tracking_urls["unsubscribeUrl"],
        tracking_urls["openPixelUrl"],
    )
    _send_via_smtp(campaign, recipient, html_body)


def send_plain_email(to_email: str, subject: str, body_text: str) -> bool:
    """One-off transactional email (password reset, etc.) — separate from
    the campaign-sending path above since it isn't tracked and has no
    recipient/token. Returns False (logs, doesn't raise) if SMTP isn't
    configured or the send fails, so callers can degrade gracefully."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.set_content(body_text)

    try:
        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(SMTP_USER, SMTP_PASS)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(SMTP_USER, SMTP_PASS)
                smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False


def send_campaign_background(campaign_id: uuid.UUID, public_base_url: str) -> None:
    if not _is_sending_configured():
        return  # misconfigured — leave campaign as "Scheduled"; nothing was sent

    db = SessionLocal()
    try:
        campaign = db.get(models.Campaign, campaign_id)
        if not campaign:
            return

        campaign.status = "Sending"
        db.commit()

        for recipient in campaign.recipients:
            tracking_urls = {
                "openPixelUrl": f"{public_base_url}/track/open/{recipient.token}.gif",
                "clickBaseUrl": f"{public_base_url}/track/click/{recipient.token}",
                "unsubscribeUrl": f"{public_base_url}/track/unsubscribe/{recipient.token}",
            }
            _send_one(campaign, recipient, tracking_urls)
            db.commit()

        campaign.status = "Sent"
        db.commit()
    finally:
        db.close()
