"""Tests for password hashing and JWT helpers in app/auth/security.py."""
from __future__ import annotations

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# --- Password hashing -----------------------------------------------------
def test_hash_is_not_plaintext_and_verifies():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert verify_password("s3cret-pass", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("correct-horse")
    assert verify_password("wrong", h) is False


def test_hashes_are_salted_and_unique():
    # bcrypt salts each hash, so the same password hashes differently.
    assert hash_password("same") != hash_password("same")


def test_verify_handles_malformed_hash_gracefully():
    # Must return False, never raise, on a garbage hash.
    assert verify_password("x", "not-a-real-hash") is False


# --- JWT ------------------------------------------------------------------
def test_token_roundtrip_with_claims():
    token = create_access_token(subject=42, extra_claims={"institute_id": 7, "role": "admin"})
    claims = decode_access_token(token)
    assert claims is not None
    assert claims["sub"] == "42"
    assert claims["institute_id"] == 7
    assert claims["role"] == "admin"


def test_expired_token_is_rejected():
    # Negative expiry -> already expired.
    token = create_access_token(subject=1, expires_minutes=-1)
    assert decode_access_token(token) is None


def test_tampered_token_is_rejected():
    token = create_access_token(subject=1)
    tampered = token[:-3] + ("abc" if not token.endswith("abc") else "xyz")
    assert decode_access_token(tampered) is None


def test_garbage_token_is_rejected():
    assert decode_access_token("not.a.jwt") is None
