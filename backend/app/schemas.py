import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


class CampaignCreate(BaseModel):
    campaignName: str
    fromEmail: EmailStr
    toEmails: list[EmailStr]
    body: str

    @field_validator("campaignName", "body")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("toEmails")
    @classmethod
    def at_least_one_recipient(cls, v: list[str]) -> list[str]:
        cleaned = [e.strip() for e in v if e and e.strip()]
        if not cleaned:
            raise ValueError("At least one To Email is required")
        dupes = [e for e in cleaned if cleaned.count(e) > 1]
        if dupes:
            raise ValueError(f"Duplicate recipient email(s): {', '.join(set(dupes))}")
        return cleaned


class RecipientOut(BaseModel):
    id: uuid.UUID
    email: str
    delivered: bool
    deliveredAt: Optional[datetime] = None
    opened: bool
    openedAt: Optional[datetime] = None
    notOpened: bool
    linkClicked: bool
    linkClickedAt: Optional[datetime] = None
    bounced: bool
    bounceType: Optional[str] = None
    bouncedAt: Optional[datetime] = None
    unsubscribed: bool
    unsubscribedAt: Optional[datetime] = None
    spamReported: Optional[bool] = None
    openPixelUrl: str
    clickBaseUrl: str
    unsubscribeUrl: str

    model_config = {"from_attributes": True}


class CampaignOut(BaseModel):
    id: uuid.UUID
    campaignName: str
    fromEmail: str
    body: str
    status: str
    createdAt: datetime
    recipients: list[RecipientOut]

    model_config = {"from_attributes": True}


class BounceReport(BaseModel):
    type: Optional[str] = "hard"
    reason: Optional[str] = None
