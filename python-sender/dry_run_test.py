"""
One-off local dry run: creates a campaign via the live Node API, then
exercises send_campaign.py's logic with smtplib mocked out (so no real
email is sent), verifying it correctly reports delivered/bounced back to
the tracker for different SMTP outcomes. Delete this file if you don't
need it — it's a test harness, not part of the shipped sender.
"""

import os
import smtplib
from unittest import mock

import requests

os.environ.setdefault("API_BASE", "http://localhost:3000")
os.environ.setdefault("API_KEY", "dev-local-api-key")
os.environ["SMTP_HOST"] = "smtp.dryrun-demo-domain123.com"
os.environ["SMTP_USER"] = "sender@dryrun-demo-domain123.com"
os.environ["SMTP_PASS"] = "unused"

import send_campaign  # noqa: E402

API_BASE = os.environ["API_BASE"]
API_KEY = os.environ["API_KEY"]

resp = requests.post(
    f"{API_BASE}/api/campaigns",
    json={
        "campaignName": "Dry Run Campaign",
        "fromEmail": "sender@dryrun-demo-domain123.com",
        "toEmails": ["ok@dryrun-demo-domain123.com", "refused@dryrun-demo-domain123.com", "soft@dryrun-demo-domain123.com"],
        "body": "Test body with a link https://example.com/x",
    },
    timeout=10,
)
resp.raise_for_status()
campaign = resp.json()
print("Created campaign:", campaign["id"])

recipients = {r["email"]: r for r in campaign["recipients"]}


class FakeSMTP:
    def __init__(self, host, port, timeout=30):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        pass

    def send_message(self, msg):
        to_addr = msg["To"]
        if to_addr == "refused@dryrun-demo-domain123.com":
            raise smtplib.SMTPRecipientsRefused({to_addr: (550, b"No such user")})
        if to_addr == "soft@dryrun-demo-domain123.com":
            raise smtplib.SMTPResponseException(450, "Mailbox temporarily unavailable")
        return {}


with mock.patch("send_campaign.smtplib.SMTP", FakeSMTP):
    for recipient in campaign["recipients"]:
        send_campaign.send_one(campaign, recipient)

detail = requests.get(f"{API_BASE}/api/campaigns/{campaign['id']}", timeout=10).json()
print()
print("Resulting tracker state:")
for r in detail["recipients"]:
    print(f"  {r['email']}: delivered={r['delivered']} bounced={r['bounced']} bounceType={r['bounceType']}")

assert next(r for r in detail["recipients"] if r["email"] == "ok@dryrun-demo-domain123.com")["delivered"] is True
assert next(r for r in detail["recipients"] if r["email"] == "refused@dryrun-demo-domain123.com")["bounced"] is True
assert next(r for r in detail["recipients"] if r["email"] == "refused@dryrun-demo-domain123.com")["bounceType"] == "hard"
assert next(r for r in detail["recipients"] if r["email"] == "soft@dryrun-demo-domain123.com")["bounced"] is True
assert next(r for r in detail["recipients"] if r["email"] == "soft@dryrun-demo-domain123.com")["bounceType"] == "soft"
print()
print("ALL ASSERTIONS PASSED")

requests.delete(f"{API_BASE}/api/campaigns/{campaign['id']}", timeout=10)
print("Cleaned up dry-run campaign.")
