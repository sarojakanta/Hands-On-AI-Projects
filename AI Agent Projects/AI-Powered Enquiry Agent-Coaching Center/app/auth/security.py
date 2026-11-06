"""Password hashing and JWT helpers.

Secrets come from configuration (environment). Nothing is hard-coded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

_settings = get_settings()

# bcrypt has a 72-byte input limit; passlib handles truncation detection.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored hash."""
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # Malformed hash etc. — treat as a failed verification, never raise.
        return False


def create_access_token(
    subject: str | int,
    extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Create a signed JWT access token."""
    expire_minutes = expires_minutes or _settings.access_token_expire_minutes
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _settings.secret_key, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT. Returns the claims dict or None if invalid."""
    try:
        return jwt.decode(
            token, _settings.secret_key, algorithms=[_settings.jwt_algorithm]
        )
    except JWTError:
        return None
