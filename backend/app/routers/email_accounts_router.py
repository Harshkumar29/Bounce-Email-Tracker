import smtplib
import ssl
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..crypto import encrypt_secret
from ..db import get_db

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


class EmailAccountOut(BaseModel):
    id: uuid.UUID
    email_address: str
    provider: str
    is_verified: bool
    is_active: bool
    last_authenticated_at: datetime | None
    smtp_host: str | None = None
    smtp_port: int | None = None

    model_config = {"from_attributes": True}

    @staticmethod
    def from_account(account: "models.EmailAccount") -> "EmailAccountOut":
        out = EmailAccountOut.model_validate(account)
        if account.smtp_credential:
            out.smtp_host = account.smtp_credential.smtp_host
            out.smtp_port = account.smtp_credential.smtp_port
        return out


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
    accounts = _owned_query(db, current_user).order_by(models.EmailAccount.created_at.desc()).all()
    return [EmailAccountOut.from_account(a) for a in accounts]


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
    return EmailAccountOut.from_account(account)


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
        return ConnectResponse(status="already_connected", account=EmailAccountOut.from_account(existing))

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


class ConnectSmtpRequest(BaseModel):
    email: EmailStr
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    use_tls: bool = True

    @field_validator("smtp_host", "smtp_username", "smtp_password")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


def _test_smtp_connection(host: str, port: int, username: str, password: str, use_tls: bool) -> None:
    """Actually logs in — raises with a human-readable reason on failure,
    rather than silently saving credentials that don't work. Mirrors the
    connection logic in app/mailer.py so "it connects here" genuinely means
    "campaigns will send with this."""
    try:
        if use_tls:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(username, password)
        else:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
                smtp.login(username, password)
    except smtplib.SMTPAuthenticationError as exc:
        raise HTTPException(status_code=400, detail=f"SMTP authentication failed: {exc.smtp_error.decode(errors='replace') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}")
    except (smtplib.SMTPException, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not connect to {host}:{port} — {exc}")


@router.post("/smtp", response_model=EmailAccountOut)
def connect_smtp_account(
    payload: ConnectSmtpRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized = payload.email.strip().lower()

    # Live connection test BEFORE saving anything — a saved-but-broken
    # credential would fail silently at send time instead, hours later.
    _test_smtp_connection(payload.smtp_host, payload.smtp_port, payload.smtp_username, payload.smtp_password, payload.use_tls)

    account = (
        _owned_query(db, current_user)
        .filter(models.EmailAccount.normalized_email == normalized)
        .first()
    )
    is_new = account is None
    if not account:
        account = models.EmailAccount(
            user_id=current_user.id,
            email_address=payload.email,
            normalized_email=normalized,
            provider="smtp",
        )
        db.add(account)
        db.flush()
    elif account.provider != "smtp":
        raise HTTPException(
            status_code=400,
            detail=f"{payload.email} is already connected via {account.provider}. Disconnect it first to reconnect via SMTP.",
        )

    account.is_verified = True
    account.is_active = True
    account.last_authenticated_at = datetime.now(timezone.utc)
    db.flush()

    encrypted_password = encrypt_secret(payload.smtp_password)
    if account.smtp_credential:
        account.smtp_credential.smtp_host = payload.smtp_host
        account.smtp_credential.smtp_port = payload.smtp_port
        account.smtp_credential.smtp_username = payload.smtp_username
        account.smtp_credential.smtp_password_encrypted = encrypted_password
        account.smtp_credential.use_tls = payload.use_tls
    else:
        db.add(
            models.SmtpCredential(
                email_account_id=account.id,
                smtp_host=payload.smtp_host,
                smtp_port=payload.smtp_port,
                smtp_username=payload.smtp_username,
                smtp_password_encrypted=encrypted_password,
                use_tls=payload.use_tls,
            )
        )

    db.add(
        models.AuditLog(
            user_id=current_user.id,
            event_type="EMAIL_CONNECTION_COMPLETED" if is_new else "EMAIL_CONNECTION_UPDATED",
            detail=f"{normalized} via smtp",
        )
    )
    db.commit()
    db.refresh(account)
    return EmailAccountOut.from_account(account)
