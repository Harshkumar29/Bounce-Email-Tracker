"""
send_campaign.py

Sends a campaign created in the Bounce Email Tracker web app via real SMTP,
embedding the per-recipient tracking pixel, click-tracked links, and an
unsubscribe link that the Node server already generated (and encrypted)
for each recipient. After each send attempt, reports delivered/bounced
back to the tracker's API so the Campaign Tracker table stays accurate.

Usage:
    python send_campaign.py --campaign-id <CAMPAIGN_ID>

Configuration is read from environment variables (see .env.example in this
folder) or a local .env file if python-dotenv is installed:

    API_BASE        Base URL of the Node tracker server, e.g. http://localhost:3000
                     or your public ngrok/domain URL (must match PUBLIC_BASE_URL
                     configured on the server, since tracking links point there).
    API_KEY          Must match the API_KEY configured on the Node server.
    SMTP_HOST        e.g. smtp.gmail.com / smtp.office365.com / your provider
    SMTP_PORT        587 for STARTTLS (recommended), 465 for implicit TLS
    SMTP_USER        SMTP login username
    SMTP_PASS        SMTP login password / app password
    SMTP_USE_TLS     "1" to use STARTTLS on SMTP_PORT (default), "0" to use
                     implicit TLS (SMTP_SSL) instead

See SETUP.md at the project root for the full step-by-step walkthrough.
"""

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from typing import Optional
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    print("python-dotenv is not installed — .env will NOT be loaded.\n"
          "Run: pip install -r requirements.txt", file=sys.stderr)
else:
    load_dotenv()

API_BASE = os.environ.get("API_BASE", "http://localhost:3000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "dev-local-api-key")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") != "0"

URL_RE = re.compile(r"(https?://[^\s<]+)")


def fetch_campaign(campaign_id: str) -> dict:
    resp = requests.get(
        f"{API_BASE}/api/campaigns/{campaign_id}/export",
        headers={"x-api-key": API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def report(campaign_id: str, recipient_id: str, action: str, payload: Optional[dict] = None) -> None:
    try:
        requests.post(
            f"{API_BASE}/api/campaigns/{campaign_id}/recipients/{recipient_id}/{action}",
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            json=payload or {},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"  ! failed to report {action} for {recipient_id}: {exc}", file=sys.stderr)


def build_html_body(plain_body: str, click_base_url: str, unsubscribe_url: str, pixel_url: str) -> str:
    """Wrap the campaign body in minimal HTML, rewriting any plain URLs to go
    through the click-tracking redirect, and appending the tracking pixel +
    unsubscribe footer."""

    def wrap_link(match: re.Match) -> str:
        original = match.group(1)
        tracked = f"{click_base_url}?url={quote(original, safe='')}"
        return f'<a href="{tracked}">{html.escape(original)}</a>'

    body_html = html.escape(plain_body)
    body_html = URL_RE.sub(lambda m: wrap_link(m), body_html)
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


def send_one(campaign: dict, recipient: dict) -> None:
    msg = EmailMessage()
    msg["Subject"] = campaign["campaignName"]
    msg["From"] = campaign["fromEmail"]
    msg["To"] = recipient["email"]
    msg.set_content(campaign["body"])  # plain-text fallback
    msg.add_alternative(
        build_html_body(
            campaign["body"],
            recipient["clickBaseUrl"],
            recipient["unsubscribeUrl"],
            recipient["openPixelUrl"],
        ),
        subtype="html",
    )

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
        # The SMTP server rejected this recipient outright at send time —
        # this is an immediate/hard bounce.
        print(f"  x {recipient['email']}: hard bounce (recipient refused) — {exc}")
        report(campaign["id"], recipient["id"], "bounced", {"type": "hard", "reason": str(exc)})
        return

    except smtplib.SMTPResponseException as exc:
        # 4xx = temporary/soft bounce, 5xx = permanent/hard bounce
        bounce_type = "soft" if 400 <= exc.smtp_code < 500 else "hard"
        print(f"  x {recipient['email']}: {bounce_type} bounce — {exc.smtp_code} {exc.smtp_error}")
        report(campaign["id"], recipient["id"], "bounced", {"type": bounce_type, "reason": str(exc.smtp_error)})
        return

    except (smtplib.SMTPException, OSError) as exc:
        print(f"  x {recipient['email']}: send failed — {exc}")
        report(campaign["id"], recipient["id"], "bounced", {"type": "soft", "reason": str(exc)})
        return

    # SMTP accepted the message for delivery. This is NOT a delivery
    # guarantee (async bounces can still happen later — see SETUP.md), so
    # we mark it delivered optimistically here.
    print(f"  + {recipient['email']}: accepted by SMTP server")
    report(campaign["id"], recipient["id"], "delivered")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a campaign via SMTP and report tracking status.")
    parser.add_argument("--campaign-id", required=True, help="Campaign ID from the tracker web app")
    args = parser.parse_args()

    missing = [n for n in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if not os.environ.get(n)]
    if missing:
        print(f"Missing required env vars: {', '.join(missing)}. See .env.example.", file=sys.stderr)
        sys.exit(1)

    campaign = fetch_campaign(args.campaign_id)
    print(f"Sending '{campaign['campaignName']}' from {campaign['fromEmail']} to {len(campaign['recipients'])} recipient(s)...")

    for recipient in campaign["recipients"]:
        send_one(campaign, recipient)

    print("Done.")


if __name__ == "__main__":
    main()
