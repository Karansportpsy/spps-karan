"""Field-level encryption for consultation notes and formulation text (AES-256-GCM, authenticated).

Stored form: ``enc:v1:<key id>:<base64(nonce || ciphertext+tag)>``. The column stays ``text``, so the schema and
the row-level-security policies are unchanged. The associated data binds the ciphertext to the column name, so a
value copied into another encrypted column does not decrypt.

Keys: ``SPPS_FIELD_KEYS`` = comma-separated ``<id>:<base64 32-byte key>`` pairs; ``SPPS_FIELD_KEY_CURRENT`` = the id
used for new writes. Old ids stay listed until every row has been re-encrypted (tools/v2_rotate_field_key.py).
Hosted mode refuses to start without a valid current key (server/v2/preflight.py). Locally, with no keys set,
values are stored as plain text; a value written without the prefix is always read back as is, which is also how
v1 rows written before encryption remain readable until the rotation tool encrypts them.
"""
from __future__ import annotations

import base64
import os
import secrets

from sqlalchemy.types import Text, TypeDecorator

PREFIX = "enc:v1:"


class FieldKeyError(RuntimeError):
    pass


def _keys() -> tuple[str | None, dict[str, bytes]]:
    raw = os.getenv("SPPS_FIELD_KEYS", "").strip()
    keys: dict[str, bytes] = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        kid, _, b64 = part.partition(":")
        try:
            key = base64.b64decode(b64, validate=True)
        except Exception:
            raise FieldKeyError("SPPS_FIELD_KEYS contains an invalid key") from None
        if not kid.isalnum() or len(kid) > 16 or len(key) != 32:
            raise FieldKeyError("SPPS_FIELD_KEYS entries must be <id>:<base64 of 32 bytes>")
        keys[kid] = key
    current = os.getenv("SPPS_FIELD_KEY_CURRENT", "").strip() or None
    if current and current not in keys:
        raise FieldKeyError("SPPS_FIELD_KEY_CURRENT is not one of SPPS_FIELD_KEYS")
    return current, keys


def configured() -> bool:
    current, _ = _keys()
    return current is not None


def new_key_entry(kid: str) -> str:
    return f"{kid}:{base64.b64encode(secrets.token_bytes(32)).decode()}"


def encrypt(value: str | None, column: str) -> str | None:
    if value is None or value.startswith(PREFIX):
        return value
    current, keys = _keys()
    if not current:
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(keys[current]).encrypt(nonce, value.encode("utf-8"), column.encode())
    return f"{PREFIX}{current}:{base64.b64encode(nonce + ct).decode()}"


def decrypt(value: str | None, column: str) -> str | None:
    if value is None or not value.startswith(PREFIX):
        return value
    kid, _, b64 = value[len(PREFIX):].partition(":")
    _, keys = _keys()
    if kid not in keys:
        raise FieldKeyError(f"field key '{kid}' is not configured; add it back to SPPS_FIELD_KEYS")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = base64.b64decode(b64)
    return AESGCM(keys[kid]).decrypt(blob[:12], blob[12:], column.encode()).decode("utf-8")


def key_id(value: str | None) -> str | None:
    if value and value.startswith(PREFIX):
        return value[len(PREFIX):].split(":", 1)[0]
    return None


class EncryptedText(TypeDecorator):
    """A ``text`` column whose values are encrypted before they reach the database."""
    impl = Text
    cache_ok = True

    def __init__(self, column: str, *args, **kwargs):
        self.column = column
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        return encrypt(value, self.column)

    def process_result_value(self, value, dialect):
        return decrypt(value, self.column)
