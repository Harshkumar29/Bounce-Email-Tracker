"""Shared environment-derived config, loaded once so main.py, oauth/router.py,
and routers/auth_router.py can't independently drift on how they parse the
same env vars."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _normalize_base_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if not raw:
        return "http://localhost:3000"
    # Guards against a real deploy mistake: pasting just the host (no
    # scheme) into an env var UI produces a URL that silently breaks
    # every tracking link and OAuth redirect rather than failing loudly.
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"https://{raw}"
    return raw


PUBLIC_BASE_URL = _normalize_base_url(os.environ.get("PUBLIC_BASE_URL", "http://localhost:3000"))
API_KEY = os.environ.get("API_KEY", "dev-local-api-key")
