"""Symmetric payload sealing helpers for cloud sync."""
from nacl.secret import SecretBox


def seal_payload(shared_key: bytes, payload: bytes, nonce: bytes | None = None) -> bytes:
    """Encrypt payload bytes with a shared symmetric key."""
    box = SecretBox(shared_key)
    return box.encrypt(payload, nonce).ciphertext


def open_payload(shared_key: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
    """Decrypt payload bytes with a shared symmetric key."""
    box = SecretBox(shared_key)
    return box.decrypt(ciphertext, nonce)
