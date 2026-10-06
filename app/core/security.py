"""Password hashing (Argon2) and JWT token handling."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.utils.dates import utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _secret() -> str:
    return settings.JWT_SECRET_KEY.get_secret_value()


def create_access_token(user_id: uuid.UUID, company_id: uuid.UUID, role: str) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at)."""
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "cid": str(company_id),
        "role": role,
        "type": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM), jti, expires_at


def password_fingerprint(password_hash: str) -> str:
    """Short digest of the current hash: changing the password invalidates outstanding reset tokens."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:24]


def create_reset_token(user_id: uuid.UUID, password_hash: str) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "type": "reset",
        "pwf": password_fingerprint(password_hash),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token") from exc
    if payload.get("type") != expected_type:
        raise AuthenticationError("Invalid token type")
    return payload
