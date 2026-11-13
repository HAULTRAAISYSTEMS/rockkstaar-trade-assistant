"""Encryption for secrets kept in the database (broker tokens).

Values are stored as "enc:v1:<Fernet token>". Reading a value without that
prefix returns it unchanged, so tokens saved before encryption keep working
and are re-saved encrypted the next time they're touched.

Key: TOKEN_ENCRYPTION_KEY if set, otherwise derived from SECRET_KEY. If the key
changes, old values can't be read: decrypt() returns "" and the user simply
reconnects the broker.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)
PREFIX = "enc:v1:"


def _key(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("token-store:" + secret).encode()).digest()))


def _fernet() -> Fernet | None:
    """Encrypts with TOKEN_ENCRYPTION_KEY (else SECRET_KEY); decrypts with
    either, so adding TOKEN_ENCRYPTION_KEY after tokens were already
    encrypted under SECRET_KEY doesn't disconnect anyone."""
    secrets = [v.strip() for v in (os.environ.get("TOKEN_ENCRYPTION_KEY") or "",
                                   os.environ.get("SECRET_KEY") or "") if v and v.strip()]
    if not secrets:
        return None
    if len(secrets) == 1 or secrets[0] == secrets[1]:
        return _key(secrets[0])
    return MultiFernet([_key(x) for x in secrets])


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str) -> str:
    """Encrypt a non-empty string. Empty stays empty (that means "cleared")."""
    if not value or is_encrypted(value):
        return value or ""
    f = _fernet()
    if f is None:
        logger.warning("token encryption key missing — storing secret unencrypted")
        return value
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value) -> str:
    if not value:
        return ""
    if not is_encrypted(value):
        return str(value)          # saved before encryption existed
    f = _fernet()
    if f is None:
        return ""
    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("stored secret could not be decrypted (key changed?) — reconnect needed")
        return ""
