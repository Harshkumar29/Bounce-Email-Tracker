import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from .. import models
from ..auth import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    create_session,
    get_current_user,
    hash_password,
    log_event,
    set_session_cookie,
    verify_password,
)
from ..db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    normalized_email = payload.email.strip().lower()
    existing = db.query(models.User).filter(models.User.email == normalized_email).first()
    if existing:
        # Same message either way avoids confirming which emails already
        # have accounts (basic enumeration hygiene).
        raise HTTPException(status_code=400, detail="Could not register with these details")

    user = models.User(email=normalized_email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event(db, user.id, "USER_REGISTERED")
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    normalized_email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == normalized_email).first()

    if not user or not verify_password(payload.password, user.password_hash):
        log_event(db, user.id if user else None, "LOGIN_FAILED", detail=normalized_email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    session = create_session(db, user)
    set_session_cookie(response, session)
    log_event(db, user.id, "USER_LOGIN")
    return user


@router.post("/logout")
def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if session_id:
        session = db.get(models.AppSession, uuid.UUID(session_id))
        if session:
            db.delete(session)
            db.commit()
    clear_session_cookie(response)
    log_event(db, current_user.id, "USER_LOGOUT")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user
