import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas
from .crypto import decrypt_token, encrypt_token
from .db import Base, SessionLocal, engine, get_db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.environ.get("API_KEY", "dev-local-api-key")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/")

app = FastAPI(title="Bounce Email Tracker API")

# Creates tables on startup if they don't exist yet (idempotent — safe to
# run every boot). For real migrations later, swap this for Alembic.
Base.metadata.create_all(bind=engine)

TRANSPARENT_GIF = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c00000000010001"
    "000002024401003b"
)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def tracking_urls(token: str) -> dict:
    return {
        "openPixelUrl": f"{PUBLIC_BASE_URL}/track/open/{token}.gif",
        "clickBaseUrl": f"{PUBLIC_BASE_URL}/track/click/{token}",
        "unsubscribeUrl": f"{PUBLIC_BASE_URL}/track/unsubscribe/{token}",
    }


def recipient_to_out(r: models.Recipient) -> dict:
    return {
        "id": r.id,
        "email": r.email,
        "delivered": r.delivered,
        "deliveredAt": r.delivered_at,
        "opened": r.opened,
        "openedAt": r.opened_at,
        "notOpened": bool(r.delivered and not r.opened and not r.bounced),
        "linkClicked": r.link_clicked,
        "linkClickedAt": r.link_clicked_at,
        "bounced": r.bounced,
        "bounceType": r.bounce_type,
        "bouncedAt": r.bounced_at,
        "unsubscribed": r.unsubscribed,
        "unsubscribedAt": r.unsubscribed_at,
        "spamReported": r.spam_reported,
        **tracking_urls(r.token),
    }


def campaign_to_out(c: models.Campaign) -> dict:
    return {
        "id": c.id,
        "campaignName": c.campaign_name,
        "fromEmail": c.from_email,
        "body": c.body,
        "status": c.status,
        "createdAt": c.created_at,
        "recipients": [recipient_to_out(r) for r in c.recipients],
    }


# --------------------------------------------------------------------------
# Campaign CRUD
# --------------------------------------------------------------------------


@app.get("/api/campaigns", response_model=list[schemas.CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.execute(
        select(models.Campaign).order_by(models.Campaign.created_at.desc())
    ).scalars().all()
    return [campaign_to_out(c) for c in campaigns]


@app.post("/api/campaigns", response_model=schemas.CampaignOut, status_code=201)
def create_campaign(payload: schemas.CampaignCreate, db: Session = Depends(get_db)):
    campaign = models.Campaign(
        campaign_name=payload.campaignName,
        from_email=payload.fromEmail,
        body=payload.body,
        status="Scheduled",
    )
    db.add(campaign)
    db.flush()  # get campaign.id before building recipient tokens

    for email in payload.toEmails:
        recipient_id = uuid.uuid4()
        token = encrypt_token({"campaignId": str(campaign.id), "recipientId": str(recipient_id)})
        db.add(models.Recipient(id=recipient_id, campaign_id=campaign.id, email=email, token=token))

    db.commit()
    db.refresh(campaign)
    return campaign_to_out(campaign)


@app.get("/api/campaigns/{campaign_id}", response_model=schemas.CampaignOut)
def get_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = db.get(models.Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign_to_out(campaign)


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = db.get(models.Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(campaign)
    db.commit()
    return {"ok": True}


@app.get("/api/campaigns/{campaign_id}/export", response_model=schemas.CampaignOut)
def export_campaign(
    campaign_id: uuid.UUID, db: Session = Depends(get_db), _=Depends(require_api_key)
):
    campaign = db.get(models.Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign_to_out(campaign)


# --------------------------------------------------------------------------
# Recipient status reporting (delivered / bounced / spam) — API-key gated,
# called by send_campaign.py / bounce_poller.py / an ESP webhook.
# --------------------------------------------------------------------------


@app.post("/api/campaigns/{campaign_id}/recipients/{recipient_id}/delivered")
def report_delivered(
    campaign_id: uuid.UUID,
    recipient_id: uuid.UUID,
    db: Session = Depends(get_db),
    _=Depends(require_api_key),
):
    recipient = _get_recipient(db, campaign_id, recipient_id)
    recipient.delivered = True
    recipient.delivered_at = datetime.now(timezone.utc)
    db.commit()
    return recipient_to_out(recipient)


@app.post("/api/campaigns/{campaign_id}/recipients/{recipient_id}/bounced")
def report_bounced(
    campaign_id: uuid.UUID,
    recipient_id: uuid.UUID,
    payload: schemas.BounceReport,
    db: Session = Depends(get_db),
    _=Depends(require_api_key),
):
    recipient = _get_recipient(db, campaign_id, recipient_id)
    recipient.bounced = True
    recipient.bounce_type = "soft" if payload.type == "soft" else "hard"
    recipient.bounce_reason = (payload.reason or "")[:2000]
    recipient.bounced_at = datetime.now(timezone.utc)
    db.commit()
    return recipient_to_out(recipient)


@app.post("/api/campaigns/{campaign_id}/recipients/{recipient_id}/spam")
def report_spam(
    campaign_id: uuid.UUID,
    recipient_id: uuid.UUID,
    db: Session = Depends(get_db),
    _=Depends(require_api_key),
):
    recipient = _get_recipient(db, campaign_id, recipient_id)
    recipient.spam_reported = True
    recipient.spam_reported_at = datetime.now(timezone.utc)
    db.commit()
    return recipient_to_out(recipient)


def _get_recipient(db: Session, campaign_id: uuid.UUID, recipient_id: uuid.UUID) -> models.Recipient:
    recipient = db.get(models.Recipient, recipient_id)
    if not recipient or recipient.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Recipient not found")
    return recipient


# --------------------------------------------------------------------------
# Public tracking endpoints — hit by the recipient's mail client. Never
# authenticated; never error loudly on a bad/tampered token.
# --------------------------------------------------------------------------


def _find_recipient_by_token(db: Session, token: str) -> Optional[models.Recipient]:
    decoded = decrypt_token(token)
    if not decoded:
        return None
    try:
        recipient_id = uuid.UUID(decoded["recipientId"])
        campaign_id = uuid.UUID(decoded["campaignId"])
    except (KeyError, ValueError):
        return None
    recipient = db.get(models.Recipient, recipient_id)
    if not recipient or recipient.campaign_id != campaign_id:
        return None
    return recipient


@app.get("/track/open/{token}.gif")
@app.get("/track/open/{token}")
def track_open(token: str, db: Session = Depends(get_db)):
    recipient = _find_recipient_by_token(db, token)
    if recipient and not recipient.opened:
        recipient.opened = True
        recipient.opened_at = datetime.now(timezone.utc)
        db.commit()
    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/track/click/{token}")
def track_click(token: str, request: Request, url: Optional[str] = None, db: Session = Depends(get_db)):
    target = url or PUBLIC_BASE_URL
    recipient = _find_recipient_by_token(db, token)
    if recipient:
        recipient.link_clicked = True
        recipient.link_clicked_at = datetime.now(timezone.utc)
        db.add(
            models.ClickLog(
                recipient_id=recipient.id,
                destination_url=target,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        )
        db.commit()
    return RedirectResponse(url=target, status_code=302)


@app.get("/track/unsubscribe/{token}", response_class=HTMLResponse)
def track_unsubscribe(token: str, db: Session = Depends(get_db)):
    recipient = _find_recipient_by_token(db, token)
    if recipient:
        recipient.unsubscribed = True
        recipient.unsubscribed_at = datetime.now(timezone.utc)
        db.commit()
    return (
        "<!doctype html><meta charset='utf-8'><title>Unsubscribed</title>"
        "<body style=\"font-family:system-ui;padding:40px;text-align:center;color:#1c2333\">"
        "<h2>You have been unsubscribed</h2>"
        "<p>You will not receive further emails from this campaign.</p></body>"
    )


@app.get("/api/campaigns/{campaign_id}/recipients/{recipient_id}/clicks")
def list_click_logs(campaign_id: uuid.UUID, recipient_id: uuid.UUID, db: Session = Depends(get_db)):
    recipient = _get_recipient(db, campaign_id, recipient_id)
    logs = db.execute(
        select(models.ClickLog)
        .where(models.ClickLog.recipient_id == recipient.id)
        .order_by(models.ClickLog.clicked_at.desc())
    ).scalars().all()
    return [
        {
            "id": log.id,
            "destinationUrl": log.destination_url,
            "clickedAt": log.clicked_at,
            "ipAddress": log.ip_address,
            "userAgent": log.user_agent,
        }
        for log in logs
    ]


# --------------------------------------------------------------------------
# Static frontend (public/) — same UI as before, now served by FastAPI.
# --------------------------------------------------------------------------

PUBLIC_DIR = Path(__file__).resolve().parent.parent.parent / "public"
if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
