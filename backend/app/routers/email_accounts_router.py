import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..db import get_db

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


class EmailAccountOut(BaseModel):
    id: uuid.UUID
    email_address: str
    provider: str
    is_verified: bool
    is_active: bool
    last_authenticated_at: datetime | None

    model_config = {"from_attributes": True}


def _owned_query(db: Session, user: models.User):
    # Every query in this router goes through this helper — the critical
    # rule from the integration doc (#6): always scope by user_id, never
    # trust a bare id from the URL.
    return db.query(models.EmailAccount).filter(models.EmailAccount.user_id == user.id)


@router.get("", response_model=list[EmailAccountOut])
def list_email_accounts(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _owned_query(db, current_user).order_by(models.EmailAccount.created_at.desc()).all()


@router.get("/{account_id}", response_model=EmailAccountOut)
def get_email_account(
    account_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _owned_query(db, current_user).filter(models.EmailAccount.id == account_id).first()
    if not account:
        # 404, not 403 — don't disclose whether the id exists at all
        # (doc #18's recommended security-oriented approach).
        raise HTTPException(status_code=404, detail="Email account not found")
    return account


@router.delete("/{account_id}")
def disconnect_email_account(
    account_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _owned_query(db, current_user).filter(models.EmailAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Email account not found")

    db.add(models.AuditLog(user_id=current_user.id, event_type="EMAIL_DISCONNECTED", detail=account.email_address))
    db.delete(account)
    db.commit()
    return {"ok": True}


class ConnectRequest(BaseModel):
    email: str
    provider: str  # 'google' (only one implemented so far — see oauth/providers.py)


class ConnectResponse(BaseModel):
    status: str  # "already_connected" | "authentication_required"
    provider: str | None = None
    authorization_url: str | None = None
    account: EmailAccountOut | None = None


@router.post("/connect", response_model=ConnectResponse)
def connect_email_account(
    payload: ConnectRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized = payload.email.strip().lower()

    existing = (
        _owned_query(db, current_user)
        .filter(models.EmailAccount.normalized_email == normalized, models.EmailAccount.is_active.is_(True))
        .first()
    )
    if existing:
        return ConnectResponse(status="already_connected", account=existing)

    # Deliberately does NOT check whether this address is connected to a
    # DIFFERENT user here and report that fact — per doc #25, that would be
    # an email-enumeration oracle. The real conflict check happens at the
    # OAuth callback (see oauth_router.py), once identity is *proven*, not
    # from the bare string the user typed.
    from ..oauth.router import build_authorization_url  # local import avoids a cycle

    try:
        auth_url = build_authorization_url(
            db, user=current_user, provider_name=payload.provider, requested_email=normalized
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ConnectResponse(status="authentication_required", provider=payload.provider, authorization_url=auth_url)
