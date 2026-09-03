"""
Provider abstraction (fastapi_email_authentication_integration.md #30):
the main app talks to `EmailProvider`, not to Google/Microsoft specifics
directly, so adding a new provider means writing one class.

Only identity scopes are requested here (openid/email/profile-equivalent)
— enough to know *who* authenticated, per the doc's "request minimum
scopes" guidance (#10). Actually sending mail through the connected
mailbox (Gmail API `gmail.send` / Graph `Mail.Send`) is a separate,
later scope upgrade — deliberately not requested yet (doc #35, Phase 7:
mailbox APIs come only after identity/linking works).
"""

import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class TokenResult:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[float]  # unix timestamp, None if provider didn't say
    scopes: str


@dataclass
class IdentityResult:
    email: str
    account_id: str
    email_verified: bool


class EmailProvider:
    name: str

    def get_authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise NotImplementedError

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenResult:
        raise NotImplementedError

    async def get_identity(self, access_token: str) -> IdentityResult:
        raise NotImplementedError

    async def refresh(self, refresh_token: str) -> TokenResult:
        raise NotImplementedError


class GoogleProvider(EmailProvider):
    name = "google"

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    # gmail.send lets campaigns actually send through this mailbox via the
    # Gmail API once connected -- no app password needed. Requires the
    # Gmail API enabled and this scope added under the OAuth consent
    # screen's Data Access config in Google Cloud Console (see SETUP.md).
    SCOPES = "openid email profile https://www.googleapis.com/auth/gmail.send"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def get_authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.AUTH_URL}?{httpx.QueryParams(params)}"

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenResult:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_at = time.time() + data["expires_in"] if "expires_in" in data else None
        return TokenResult(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            scopes=data.get("scope", self.SCOPES),
        )

    async def get_identity(self, access_token: str) -> IdentityResult:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        return IdentityResult(
            email=data["email"],
            account_id=data["sub"],
            email_verified=bool(data.get("email_verified", False)),
        )

    async def refresh(self, refresh_token: str) -> TokenResult:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_at = time.time() + data["expires_in"] if "expires_in" in data else None
        return TokenResult(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_token),  # Google may not rotate it
            expires_at=expires_at,
            scopes=data.get("scope", self.SCOPES),
        )


# MicrosoftProvider intentionally not implemented yet — it needs its own
# Microsoft Entra ID app registration (client id/secret) before it can be
# tested at all, same as Google above. Add it here the same shape once
# those credentials exist: AUTH_URL/TOKEN_URL are Microsoft's v2.0 identity
# platform endpoints, and get_identity hits Microsoft Graph's /me.
PROVIDERS: dict[str, EmailProvider] = {}


def register_provider(provider: EmailProvider) -> None:
    PROVIDERS[provider.name] = provider


def get_provider(name: str) -> EmailProvider:
    provider = PROVIDERS.get(name)
    if not provider:
        raise ValueError(f"Unknown or unconfigured provider: {name}")
    return provider
