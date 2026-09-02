import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_FILE = Path(__file__).resolve().parent.parent / ".tracking_secret"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("TRACKING_SECRET")
    if env_key:
        return bytes.fromhex(env_key)
    if SECRET_FILE.exists():
        return bytes.fromhex(SECRET_FILE.read_text().strip())
    key = AESGCM.generate_key(bit_length=256)
    SECRET_FILE.write_text(key.hex())
    return key


_KEY = _load_or_create_key()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def encrypt_token(payload: dict) -> str:
    aesgcm = AESGCM(_KEY)
    nonce = os.urandom(12)
    plaintext = json.dumps(payload).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # ciphertext includes GCM tag appended
    return _b64url_encode(nonce + ciphertext)


def decrypt_token(token: str) -> dict | None:
    try:
        raw = _b64url_decode(token)
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(_KEY)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None
