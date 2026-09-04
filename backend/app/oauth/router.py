"""
OAuth start/callback endpoints, plus the account-linking-attack guard from
fastapi_email_authentication_integration.md #15: the callback only trusts
the PROVIDER's authenticated identity, never the email string the user
originally typed. If they don't match, the connection is refused outright.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..config import PUBLIC_BASE_URL
from ..crypto import encrypt_secret
from ..db import get_db
from .pkce import code_challenge_s256, generate_code_verifier, generate_state
from .providers import get_provider

router = APIRouter(prefix="/oauth", tags=["oauth"])

log = logging.getLogger(__name__)

STATE_LIFETIME = timedelta(minutes=10)


def build_authorization_url(db: Session, user: models.User, provider_name: str, requested_email: str) -> str:
    provider = get_provider(provider_name)  # raises ValueError if unconfigured — caller returns 4xx

    state = generate_state()
    code_verifier = generate_code_verifier()
    code_challenge = code_challenge_s256(code_verifier)

    db.add(
        models.OAuthState(
            state=state,
            user_id=user.id,
            provider=provider_name,
            requested_email=requested_email,
            code_verifier=code_verifier,
            expires_at=datetime.now(timezone.utc) + STATE_LIFETIME,
        )
    )
    db.commit()

    redirect_uri = f"{PUBLIC_BASE_URL}/oauth/{provider_name}/callback"
    return provider.get_authorization_url(state, code_challenge, redirect_uri)


@router.get("/{provider_name}/start")
def oauth_start(
    provider_name: str,
    email: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Browser-redirect variant of POST /email-accounts/connect, for a plain
    <a href> link instead of a fetch() call."""
    try:
        url = build_authorization_url(db, current_user, provider_name, email.strip().lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(url)


@router.get("/{provider_name}/callback")
async def oauth_callback(
    provider_name: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        return RedirectResponse(f"/?oauth_error={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    oauth_state = db.get(models.OAuthState, state)
    if not oauth_state or oauth_state.provider != provider_name:
        # Unknown/reused/wrong-provider state — refuse outright (CSRF guard).
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if oauth_state.expires_at < datetime.now(timezone.utc):
        db.delete(oauth_state)
        db.commit()
        raise HTTPException(status_code=400, detail="OAuth state expired, please retry")

    # One-time use: consume the state row now, whatever happens next.
    user_id = oauth_state.user_id
    requested_email = oauth_state.requested_email
    code_verifier = oauth_state.code_verifier
    db.delete(oauth_state)
    db.commit()

    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")

    provider = get_provider(provider_name)
    redirect_uri = f"{PUBLIC_BASE_URL}/oauth/{provider_name}/callback"

    token_result = await provider.exchange_code(code, code_verifier, redirect_uri)
    identity = await provider.get_identity(token_result.access_token)
    log.info(
        "oauth_callback: provider=%s email=%s scopes=%s has_refresh=%s",
        provider_name, identity.email, token_result.scopes, bool(token_result.refresh_token),
    )

    # --- The account-linking-attack guard (doc #15) ---
    # Compare what the PROVIDER says was authenticated against what the
    # user originally asked to connect. A mismatch is refused, full stop.
    authenticated_email = identity.email.strip().lower()
    if requested_email and authenticated_email != requested_email:
        db.add(
            models.AuditLog(
                user_id=user.id,
                event_type="EMAIL_CONNECTION_FAILED",
                detail=f"requested={requested_email} authenticated={authenticated_email} (mismatch)",
            )
        )
        db.commit()
        return RedirectResponse(
            f"/?oauth_error=identity_mismatch&requested={requested_email}&authenticated={authenticated_email}"
        )

    # --- Prevent the same provider account being linked to two different
    # application users (doc #21's second UNIQUE constraint) ---
    conflict = (
        db.query(models.EmailAccount)
        .filter(
            models.EmailAccount.provider == provider_name,
            models.EmailAccount.provider_account_id == identity.account_id,
            models.EmailAccount.user_id != user.id,
        )
        .first()
    )
    if conflict:
        db.add(
            models.AuditLog(
                user_id=user.id,
                event_type="EMAIL_CONNECTION_FAILED",
                detail=f"{authenticated_email} already linked to another application user",
            )
        )
        db.commit()
        return RedirectResponse("/?oauth_error=already_linked_elsewhere")

    account = (
        db.query(models.EmailAccount)
        .filter(models.EmailAccount.user_id == user.id, models.EmailAccount.normalized_email == authenticated_email)
        .first()
    )
    if not account:
        account = models.EmailAccount(
            user_id=user.id,
            email_address=identity.email,
            normalized_email=authenticated_email,
            provider=provider_name,
        )
        db.add(account)
        db.flush()

    account.provider_account_id = identity.account_id
    account.is_verified = True
    account.is_active = True
    account.last_authenticated_at = datetime.now(timezone.utc)
    db.flush()

    credential = (
        db.query(models.OAuthCredential).filter(models.OAuthCredential.email_account_id == account.id).first()
    )
    expires_at = (
        datetime.fromtimestamp(token_result.expires_at, tz=timezone.utc) if token_result.expires_at else None
    )
    if credential:
        credential.access_token_encrypted = encrypt_secret(token_result.access_token)
        if token_result.refresh_token:
            credential.refresh_token_encrypted = encrypt_secret(token_result.refresh_token)
        credential.expires_at = expires_at
        credential.scopes = token_result.scopes
    else:
        db.add(
            models.OAuthCredential(
                email_account_id=account.id,
                access_token_encrypted=encrypt_secret(token_result.access_token),
                refresh_token_encrypted=encrypt_secret(token_result.refresh_token)
                if token_result.refresh_token
                else None,
                expires_at=expires_at,
                scopes=token_result.scopes,
            )
        )

    db.add(models.AuditLog(user_id=user.id, event_type="EMAIL_CONNECTION_COMPLETED", detail=authenticated_email))
    db.commit()

    return RedirectResponse(f"/?connected={authenticated_email}")
