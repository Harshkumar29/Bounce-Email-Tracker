import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_FILE = Path(__file__).resolve().parent.parent / ".tracking_secret"
OAUTH_SECRET_FILE = Path(__file__).resolve().parent.parent / ".oauth_secret"


def _load_or_create_key(env_var: str, file_path: Path) -> bytes:
    env_key = os.environ.get(env_var)
    if env_key:
        return bytes.fromhex(env_key)
    if file_path.exists():
        return bytes.fromhex(file_path.read_text().strip())
    key = AESGCM.generate_key(bit_length=256)
    file_path.write_text(key.hex())
    return key


_TRACKING_KEY = _load_or_create_key("TRACKING_SECRET", SECRET_FILE)
# Deliberately a separate key from _TRACKING_KEY (per the OAuth integration
# doc: keep OAuth credentials isolated from other secrets) so rotating one
# never invalidates the other.
_OAUTH_KEY = _load_or_create_key("OAUTH_ENCRYPTION_KEY", OAUTH_SECRET_FILE)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _aesgcm_encrypt(key: bytes, plaintext: bytes) -> str:
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # tag appended by AESGCM
    return _b64url_encode(nonce + ciphertext)


def _aesgcm_decrypt(key: bytes, token: str) -> bytes:
    raw = _b64url_decode(token)
    nonce, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def encrypt_token(payload: dict) -> str:
    """Tracking-pixel/click/unsubscribe token: {campaignId, recipientId}."""
    return _aesgcm_encrypt(_TRACKING_KEY, json.dumps(payload).encode("utf-8"))


def decrypt_token(token: str) -> dict | None:
    try:
        return json.loads(_aesgcm_decrypt(_TRACKING_KEY, token).decode("utf-8"))
    except Exception:
        return None


def encrypt_secret(plaintext: str) -> str:
    """OAuth access/refresh tokens at rest — never store these unencrypted."""
    return _aesgcm_encrypt(_OAUTH_KEY, plaintext.encode("utf-8"))


def decrypt_secret(token: str) -> str | None:
    try:
        return _aesgcm_decrypt(_OAUTH_KEY, token).decode("utf-8")
    except Exception:
        return None
