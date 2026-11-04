"""Encrypt / decrypt secrets kept in the database (Fernet). The key is config ENCRYPTION_KEY — losing it makes every
encrypted value unreadable, so production sets it once and never changes it."""
import hashlib

from cryptography.fernet import Fernet
from flask import current_app


def _fernet():
    return Fernet(current_app.config["ENCRYPTION_KEY"].encode())


def encrypt_secret(raw):
    return _fernet().encrypt(raw.encode()).decode() if raw else ""


def decrypt_secret(token):
    return _fernet().decrypt(token.encode()).decode() if token else ""


def sha256(raw):
    """One-way hash for one-time tokens / recovery codes (we only ever compare, never read back)."""
    return hashlib.sha256(raw.encode()).hexdigest()
