"""Authentication: bcrypt password hashing, JWT access tokens, login throttling, bootstrap admin."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache

import bcrypt
import jwt

from app.config import settings
from app.db.base import session_scope
from app.db.models import User
from app.db.repositories import AuditRepository, UserRepository
from app.security.rbac import ROLE_ORDER, Principal

logger = logging.getLogger(__name__)

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72          # bcrypt limit
DEFAULT_SECRET_MARKER = "change-me-in-production"
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 900


class AuthError(Exception):
    """Authentication failed (bad credentials, token invalid/expired, account locked)."""


class SecurityConfigError(RuntimeError):
    """Unsafe security configuration (refuses to start in production)."""


# ------------------------------------------------------------------ passwords
def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")


def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except (ValueError, TypeError):
        return False


@lru_cache
def _dummy_hash(rounds: int) -> str:
    """Hash checked for unknown users so response time does not reveal which usernames exist."""
    return bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=rounds)).decode()


# --------------------------------------------------------------------- tokens
def _secret() -> str:
    return settings.jwt_secret.get_secret_value()


def create_access_token(username: str, roles: set[str] | frozenset[str]) -> tuple[str, int]:
    now = int(time.time())
    ttl = settings.jwt_expire_minutes * 60
    payload = {"sub": username, "roles": sorted(roles), "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm), ttl


def decode_token(token: str) -> Principal:
    try:
        data = jwt.decode(token, _secret(), algorithms=[settings.jwt_algorithm],
                          options={"require": ["exp", "sub", "iat"]})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid token") from exc
    return Principal(data["sub"], frozenset(r for r in data.get("roles", []) if r in ROLE_ORDER))


# ------------------------------------------------------------------ throttling
_failures: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _locked(username: str, now: float) -> bool:
    with _lock:
        window = _failures[username]
        while window and now - window[0] > LOCKOUT_SECONDS:
            window.popleft()
        return len(window) >= MAX_FAILED_LOGINS


def _record_failure(username: str, now: float) -> None:
    with _lock:
        _failures[username].append(now)


def reset_throttle() -> None:
    with _lock:
        _failures.clear()


# ----------------------------------------------------------------- operations
def authenticate(username: str, password: str) -> Principal:
    """Verify credentials. Raises AuthError with a deliberately generic message."""
    now = time.time()
    if _locked(username, now):
        with session_scope() as s:
            AuditRepository(s).record(username, "auth.locked", "user", username)
        raise AuthError("Too many failed attempts; try again later")
    with session_scope() as s:
        user = UserRepository(s).by_username(username)
        stored = user.password_hash if user else _dummy_hash(settings.bcrypt_rounds)
        ok = verify_password(password, stored) and user is not None and user.is_active
        roles = frozenset(r.strip() for r in (user.roles if user else "").split(",") if r.strip())
        AuditRepository(s).record(username, "auth.login" if ok else "auth.login_failed", "user", username)
    if not ok:
        _record_failure(username, now)
        raise AuthError("Invalid username or password")
    with _lock:
        _failures.pop(username, None)
    return Principal(username, frozenset(r for r in roles if r in ROLE_ORDER))


def create_user(username: str, password: str, roles: list[str], email: str = "", actor: str = "system") -> dict:
    bad = [r for r in roles if r not in ROLE_ORDER]
    if bad or not roles:
        raise ValueError(f"Roles must be a non-empty subset of {list(ROLE_ORDER)}")
    pw_hash = hash_password(password)
    with session_scope() as s:
        repo = UserRepository(s)
        if repo.by_username(username):
            raise ValueError("Username already exists")
        user = repo.add(User(username=username, email=email, password_hash=pw_hash, roles=",".join(sorted(roles))))
        AuditRepository(s).record(actor, "user.created", "user", username, roles=sorted(roles))
        return user_dict(user)


def user_dict(user: User) -> dict:
    return {"username": user.username, "email": user.email, "roles": [r for r in user.roles.split(",") if r],
            "is_active": user.is_active, "created_at": user.created_at.isoformat() if user.created_at else None}


def ensure_bootstrap_admin() -> bool:
    """Create the first admin from env if configured and not present. Returns True if created."""
    password = settings.bootstrap_admin_password.get_secret_value()
    if not password:
        return False
    with session_scope() as s:
        if UserRepository(s).by_username(settings.bootstrap_admin_username):
            return False
    create_user(settings.bootstrap_admin_username, password, ["admin"], actor="bootstrap")
    logger.info("Bootstrap admin '%s' created", settings.bootstrap_admin_username)
    return True


def validate_security_settings() -> list[str]:
    """Raise in production on unsafe settings; return warnings otherwise."""
    warnings = []
    weak_secret = DEFAULT_SECRET_MARKER in _secret() or len(_secret()) < 32
    if settings.is_production:
        if not settings.auth_enabled:
            raise SecurityConfigError("AUTH_ENABLED must be true in production")
        if weak_secret:
            raise SecurityConfigError("JWT_SECRET must be a unique random value of at least 32 characters in production")
    else:
        if weak_secret:
            warnings.append("JWT_SECRET is the development default; set a unique value before deploying")
        if not settings.auth_enabled:
            warnings.append("Authentication is DISABLED (development only)")
    return warnings
