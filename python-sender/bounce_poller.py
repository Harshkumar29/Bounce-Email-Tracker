"""
bounce_poller.py

Catches ASYNC bounces — the ones send_campaign.py can't see, because they
arrive minutes later as a separate "Delivery Status Notification" (DSN)
email sent back to the sending mailbox, rather than as an SMTP-time
rejection. See SETUP.md ("Async bounces") for why this exists.

What it does:
    1. Logs into the sending mailbox over IMAP, READ-ONLY (never modifies
       or deletes anything in the real inbox).
    2. Finds bounce/DSN emails (RFC 3464 multipart/report messages, or a
       plain-text fallback for providers that don't send structured DSNs).
    3. Parses out the failed recipient address, whether it's a permanent
       (hard) or temporary (soft) failure, and the diagnostic reason.
    4. Matches that address against recipients across all campaigns and
       reports the bounce back to the tracker via the same
       POST /api/campaigns/:id/recipients/:rid/bounced endpoint
       send_campaign.py uses.
    5. Remembers which messages it already processed (in
       .bounce_poller_state.json, local to this folder) so re-running is
       safe and idempotent.

Usage:
    python bounce_poller.py              # single pass
    python bounce_poller.py --watch 60   # loop forever, poll every 60s
    python bounce_poller.py --dry-run    # parse & print, don't call the API

Config (env vars / .env, same file as send_campaign.py):
    IMAP_HOST   default: imap.gmail.com
    IMAP_PORT   default: 993
    IMAP_USER   default: SMTP_USER
    IMAP_PASS   default: SMTP_PASS
    IMAP_MAILBOX default: INBOX
    API_BASE, API_KEY — same as send_campaign.py
"""

import argparse
import email
import json
import os
import re
import sys
import time
from email.message import Message
from typing import Optional

import imaplib

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

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ.get("IMAP_USER") or os.environ.get("SMTP_USER")
IMAP_PASS = os.environ.get("IMAP_PASS") or os.environ.get("SMTP_PASS")
IMAP_MAILBOX = os.environ.get("IMAP_MAILBOX", "INBOX")

STATE_FILE = os.path.join(os.path.dirname(__file__), ".bounce_poller_state.json")

BOUNCE_SUBJECT_RE = re.compile(
    r"(delivery status notification|undelivered mail|mail delivery failed|"
    r"returned mail|failure notice|delivery has failed)",
    re.IGNORECASE,
)

# Fallback regex for bounces that aren't structured DSNs — pull an email
# address out of common plain-text bounce phrasing.
PLAIN_BOUNCE_ADDR_RE = re.compile(
    r"(?:to|recipient|address)[:\s]+<?([\w.+-]+@[\w.-]+\.\w+)>?", re.IGNORECASE
)


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processed_message_ids": []}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def parse_delivery_status_part(status_part: Message) -> list[dict]:
    """Parse an RFC 3464 message/delivery-status part into per-recipient dicts.

    Python's email parser already splits this content type into a list of
    Message objects — one "per-message fields" group, followed by one
    "per-recipient fields" group per recipient — so we read fields directly
    rather than re-parsing raw text."""
    groups = status_part.get_payload()
    if not isinstance(groups, list):
        groups = [groups]

    results = []
    for chunk in groups:
        if not isinstance(chunk, Message):
            continue
        final_recipient = chunk.get("Final-Recipient") or chunk.get("Original-Recipient")
        if not final_recipient:
            continue
        addr = final_recipient.split(";")[-1].strip().strip("<>")
        action = (chunk.get("Action") or "").strip().lower()
        status = (chunk.get("Status") or "").strip()
        diagnostic = (chunk.get("Diagnostic-Code") or "").strip()

        bounce_type = "soft"
        if status.startswith("5."):
            bounce_type = "hard"
        elif status.startswith("4."):
            bounce_type = "soft"
        elif action == "failed":
            bounce_type = "hard"

        results.append(
            {
                "recipient": addr,
                "bounce_type": bounce_type,
                "reason": diagnostic or status or action or "bounce (no detail)",
            }
        )
    return results


def extract_bounces_from_message(msg: Message) -> list[dict]:
    subject = msg.get("Subject", "") or ""

    # Preferred path: structured RFC 3464 report.
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "message/delivery-status":
                parsed = parse_delivery_status_part(part)
                if parsed:
                    return parsed

    # Fallback: plain-text bounce notice without a structured DSN part.
    if BOUNCE_SUBJECT_RE.search(subject):
        body_text = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text += payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode("utf-8", errors="replace")

        m = PLAIN_BOUNCE_ADDR_RE.search(body_text)
        if m:
            return [{"recipient": m.group(1), "bounce_type": "hard", "reason": subject}]

    return []


def find_recipient_for_email(campaigns: list[dict], address: str) -> Optional[tuple]:
    """Find the most recent, not-yet-bounced recipient matching this address
    across all campaigns (we don't have a per-recipient unique return-path,
    so we match on address + recency)."""
    candidates = []
    for campaign in campaigns:
        for recipient in campaign.get("recipients", []):
            if recipient["email"].lower() == address.lower() and not recipient.get("bounced"):
                candidates.append((campaign, recipient))
    if not candidates:
        return None
    candidates.sort(key=lambda cr: cr[0].get("createdAt", ""), reverse=True)
    return candidates[0]


def report_bounce(campaign_id: str, recipient_id: str, bounce_type: str, reason: str, dry_run: bool) -> None:
    print(f"  -> reporting bounce: campaign={campaign_id} recipient={recipient_id} type={bounce_type} reason={reason[:120]!r}")
    if dry_run:
        return
    try:
        resp = requests.post(
            f"{API_BASE}/api/campaigns/{campaign_id}/recipients/{recipient_id}/bounced",
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            json={"type": bounce_type, "reason": reason},
            timeout=15,
        )
        resp.raise_for_status()
        print("     reported OK")
    except requests.RequestException as exc:
        print(f"     ! failed to report bounce: {exc}", file=sys.stderr)


def fetch_campaigns(dry_run: bool) -> list[dict]:
    if dry_run:
        # Dry-run still needs real campaign data to match against, but
        # doesn't require the API key check the mutating endpoints enforce.
        resp = requests.get(f"{API_BASE}/api/campaigns", timeout=15)
    else:
        resp = requests.get(f"{API_BASE}/api/campaigns", timeout=15)
    resp.raise_for_status()
    return resp.json()


def poll_once(dry_run: bool) -> int:
    if not IMAP_USER or not IMAP_PASS:
        print("Missing IMAP_USER/IMAP_PASS (or SMTP_USER/SMTP_PASS fallback) in .env", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    processed = set(state.get("processed_message_ids", []))

    print(f"Connecting to {IMAP_HOST}:{IMAP_PORT} as {IMAP_USER} (read-only)...")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(IMAP_USER, IMAP_PASS)
        imap.select(IMAP_MAILBOX, readonly=True)

        # Search broadly for anything that looks like a bounce; we filter
        # more precisely once we parse each candidate message.
        status, data = imap.search(None, '(OR (FROM "mailer-daemon") (SUBJECT "Delivery Status Notification"))')
        if status != "OK":
            print("IMAP search failed:", data, file=sys.stderr)
            return 0

        message_nums = data[0].split()
        print(f"Found {len(message_nums)} candidate bounce message(s) in {IMAP_MAILBOX}.")

        new_bounces = 0
        campaigns = fetch_campaigns(dry_run)

        for num in message_nums:
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            message_id = msg.get("Message-ID") or f"num-{num.decode()}"

            if message_id in processed:
                continue

            bounces = extract_bounces_from_message(msg)
            if not bounces:
                processed.add(message_id)
                continue

            print(f"\nBounce email: {msg.get('Subject')!r} ({message_id})")
            for b in bounces:
                match = find_recipient_for_email(campaigns, b["recipient"])
                if not match:
                    print(f"  ? no matching un-bounced recipient found for {b['recipient']} — skipping")
                    continue
                campaign, recipient = match
                report_bounce(campaign["id"], recipient["id"], b["bounce_type"], b["reason"], dry_run)
                new_bounces += 1

            processed.add(message_id)

        if not dry_run:
            state["processed_message_ids"] = list(processed)
            save_state(state)
        else:
            print("(dry-run: not persisting processed-message state)")
        print(f"\nDone. {new_bounces} new bounce(s) reported.")
        return new_bounces

    finally:
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll a mailbox for async bounce notifications.")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="Loop forever, polling every SECONDS")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print, but don't call the tracker API")
    args = parser.parse_args()

    if args.watch:
        print(f"Watching every {args.watch}s. Ctrl+C to stop.")
        while True:
            try:
                poll_once(args.dry_run)
            except Exception as exc:
                print(f"Poll failed: {exc}", file=sys.stderr)
            time.sleep(args.watch)
    else:
        poll_once(args.dry_run)


if __name__ == "__main__":
    main()
