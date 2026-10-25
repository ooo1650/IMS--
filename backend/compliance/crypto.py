"""Encrypt stored third-party credentials (CBMS portal password) at rest.

Key: CBMS_ENCRYPTION_KEY (a Fernet key) if set, else derived from SECRET_KEY.
Rotating SECRET_KEY without setting CBMS_ENCRYPTION_KEY means re-entering the
CBMS password in Tax & IRD settings.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = os.environ.get("CBMS_ENCRYPTION_KEY")
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(("cbms:" + settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
