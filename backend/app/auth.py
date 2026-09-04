"""
Application login: registration, password hashing, and DB-backed sessions
(a Secure+HttpOnly cookie holding an opaque session id — not a JWT, so
logout/revocation is immediate by deleting the row).

This is deliberately separate from EmailAccount/OAuthCredential (see
models.py): a User is the person logged into this app; an EmailAccount is
a mailbox they've connected via OAuth. One User can own many EmailAccounts.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from . import models
from .db import get_db

SESSION_COOKIE_NAME = "session_id"
SESSION_LIFETIME = timedelta(days=14)

# Secure cookies require HTTPS. Defaults on (safe for Render); set
# COOKIE_SECURE=0 explicitly when testing over plain http://localhost,
# since PUBLIC_BASE_URL itself is often pointed at the https:// prod host
# even during local runs (to keep tracking-link tokens consistent).
_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"

_password_hasher = PasswordHash.recommended()  # Argon2id


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


def create_session(db: Session, user: models.User) -> models.AppSession:
    session = models.AppSession(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + SESSION_LIFETIME,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def set_session_cookie(response: Response, session: models.AppSession) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=str(session.id),
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> models.User:
    if not session_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Authentication required")

    session = db.get(models.AppSession, session_uuid)
    if not session or session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.get(models.User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required")

    return user


def log_event(db: Session, user_id: uuid.UUID | None, event_type: str, detail: str = "") -> None:
    db.add(models.AuditLog(user_id=user_id, event_type=event_type, detail=detail[:2000]))
    db.commit()
