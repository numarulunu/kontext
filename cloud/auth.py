"""Workspace bearer-token issuance, hashing, and FastAPI verification."""
import hashlib
import hmac
import secrets


def generate_workspace_token() -> tuple[str, str, str]:
    """Return (token, salt_hex, hash_hex). Only the hash+salt are stored server-side."""
    token = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    digest = _hash(token, salt)
    return token, salt.hex(), digest


def _hash(token: str, salt: bytes) -> str:
    return hashlib.sha256(salt + token.encode("utf-8")).hexdigest()


def verify_workspace_token(token: str, salt_hex: str, expected_hash_hex: str) -> bool:
    if not token or not salt_hex or not expected_hash_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(_hash(token, salt), expected_hash_hex)


def extract_bearer(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    header = authorization_header.strip()
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None
