import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable so pre-existing rows created before login was added aren't
    # orphaned by a NOT NULL migration; every new campaign always sets it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    campaign_name: Mapped[str] = mapped_column(String(255), nullable=False)
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="Scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    recipients: Mapped[list["Recipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class Recipient(Base):
    __tablename__ = "recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    opened: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    link_clicked: Mapped[bool] = mapped_column(Boolean, default=False)
    link_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bounced: Mapped[bool] = mapped_column(Boolean, default=False)
    bounce_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 'hard' | 'soft'
    bounce_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    spam_reported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    spam_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="recipients")
    click_logs: Mapped[list["ClickLog"]] = relationship(
        back_populates="recipient", cascade="all, delete-orphan"
    )


class User(Base):
    """Application login identity — separate from any mailbox the user
    later connects (see EmailAccount). One user can own many EmailAccounts."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    email_accounts: Mapped[list["EmailAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AppSession(Base):
    """Opaque server-side session token stored in a Secure+HttpOnly cookie.
    DB-backed (not JWT) so logout/revocation is immediate."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailAccount(Base):
    """A mailbox a user has connected (or is connecting) via OAuth.
    UNIQUE(user_id, email_address) stops one user creating duplicates;
    UNIQUE(provider, provider_account_id) stops the same provider account
    being linked to two different application users (accidental/malicious
    account linking — see fastapi_email_authentication_integration.md #21)."""

    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_email", name="uq_email_accounts_user_email"),
        UniqueConstraint("provider", "provider_account_id", name="uq_email_accounts_provider_account"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # 'google' | 'microsoft' | ...
    provider_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="email_accounts")
    oauth_credential: Mapped["OAuthCredential | None"] = relationship(
        back_populates="email_account", cascade="all, delete-orphan", uselist=False
    )


class OAuthCredential(Base):
    """Encrypted OAuth tokens for one EmailAccount. Kept in its own table,
    separate from ordinary email-account metadata, per the integration doc."""

    __tablename__ = "oauth_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    email_account: Mapped["EmailAccount"] = relationship(back_populates="oauth_credential")


class OAuthState(Base):
    """Short-lived CSRF state (+ PKCE verifier) for one in-progress OAuth
    connect attempt. Row is deleted once consumed by the callback, and
    expired rows are rejected even if presented."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """Security-sensitive event trail — never store tokens/passwords/codes
    in `detail`, only descriptive metadata."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ClickLog(Base):
    """Every individual link click, matching the blueprint's click_logs table
    (message_id/destination_url/clicked_at/ip_address/user_agent) — unlike
    the single linkClicked flag on Recipient, this keeps full click history."""

    __tablename__ = "click_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipients.id", ondelete="CASCADE"), nullable=False
    )
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    recipient: Mapped["Recipient"] = relationship(back_populates="click_logs")
