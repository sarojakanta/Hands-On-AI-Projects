"""Phase 2 authentication tests.

Covers the security rules enforced in backend code:
  * valid OTP succeeds and creates a verified session,
  * invalid OTP is rejected and decrements remaining attempts,
  * OTP expires after the TTL,
  * more than max attempts is rejected,
  * OTP is single-use,
  * verified state is isolated per session (no cross-customer leakage),
  * no customer PII is exposed before verification.

Tests operate directly on the service layer and the Flask API. The hardcoded
development OTP is read from config (DEV_OTP = 123456).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import config
from app.database.db import get_session
from app.models.customer import Customer
from app.models.order import Order
from app.models.otp_challenge import OTPChallenge
from app.auth.auth_service import AuthService
from app.auth.verification_service import (
    VerificationService,
    RESULT_SUCCESS,
    RESULT_INVALID_CODE,
    RESULT_EXPIRED,
    RESULT_TOO_MANY_ATTEMPTS,
    RESULT_ALREADY_USED,
    RESULT_NO_CHALLENGE,
)
from app.auth.session_manager import session_manager


def _a_customer_id() -> str:
    session = get_session()
    try:
        return session.query(Customer).first().id
    finally:
        session.close()


def _known_order_customer() -> tuple[str, str]:
    """Return (order_id, customer_id) for the well-known Scenario A order."""
    session = get_session()
    try:
        order = session.get(Order, "UC-48213")
        return order.id, order.customer_id
    finally:
        session.close()


def setup_function(_):
    # Each test starts with a clean verified-session store.
    session_manager.reset()


# ---------------------------------------------------------------- service tier

def test_valid_otp_succeeds_and_creates_session():
    svc = AuthService()
    sid = "sess-valid"
    order_id, customer_id = _known_order_customer()

    init = svc.initiate(session_id=sid, order_ref=order_id)
    assert init.success
    assert init.customer_id == customer_id

    result = svc.verify(session_id=sid, customer_id=customer_id, code=config.DEV_OTP)
    assert result.success
    assert result.status == RESULT_SUCCESS
    assert session_manager.is_verified(session_id=sid, customer_id=customer_id)


def test_invalid_otp_rejected_and_decrements_attempts():
    svc = AuthService()
    sid = "sess-invalid"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    result = svc.verify(session_id=sid, customer_id=customer_id, code="000000")
    assert not result.success
    assert result.status == RESULT_INVALID_CODE
    assert result.attempts_remaining == config.OTP_MAX_ATTEMPTS - 1
    assert not session_manager.is_verified(session_id=sid, customer_id=customer_id)


def test_otp_expires_after_ttl():
    svc = AuthService()
    sid = "sess-expired"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    # Force the challenge to be expired.
    session = get_session()
    try:
        challenge = (
            session.query(OTPChallenge)
            .filter_by(session_id=sid, customer_id=customer_id)
            .order_by(OTPChallenge.created_at.desc())
            .first()
        )
        challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()

    result = svc.verify(session_id=sid, customer_id=customer_id, code=config.DEV_OTP)
    assert not result.success
    assert result.status == RESULT_EXPIRED


def test_more_than_max_attempts_rejected():
    svc = AuthService()
    sid = "sess-attempts"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    # Exhaust all attempts with wrong codes.
    last = None
    for _ in range(config.OTP_MAX_ATTEMPTS):
        last = svc.verify(session_id=sid, customer_id=customer_id, code="111111")
    assert last is not None and not last.success
    assert last.attempts_remaining == 0

    # Even the correct code is now refused (challenge consumed).
    after = svc.verify(session_id=sid, customer_id=customer_id, code=config.DEV_OTP)
    assert not after.success
    assert after.status in (RESULT_TOO_MANY_ATTEMPTS, RESULT_ALREADY_USED)


def test_otp_is_single_use():
    svc = AuthService()
    sid = "sess-single"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    first = svc.verify(session_id=sid, customer_id=customer_id, code=config.DEV_OTP)
    assert first.success

    # Reusing the same (now consumed) OTP must fail.
    second = svc.verify(session_id=sid, customer_id=customer_id, code=config.DEV_OTP)
    assert not second.success
    assert second.status == RESULT_ALREADY_USED


def test_no_challenge_for_wrong_binding():
    """A code issued for (sessionA, customerA) is not valid for a different
    session or a different customer."""
    svc = AuthService()
    customer_id = _a_customer_id()
    svc.initiate(session_id="sessA", customer_ref=customer_id)

    # Different session, same customer -> no challenge.
    other = VerificationService().verify(
        session_id="sessB", customer_id=customer_id, code=config.DEV_OTP
    )
    assert not other.success
    assert other.status == RESULT_NO_CHALLENGE


def test_session_isolation_between_customers():
    """A session verified for customer A is not verified for customer B."""
    svc = AuthService()
    session = get_session()
    try:
        customers = session.query(Customer).limit(2).all()
        cust_a, cust_b = customers[0].id, customers[1].id
    finally:
        session.close()

    sid = "sess-iso"
    svc.initiate(session_id=sid, customer_ref=cust_a)
    svc.verify(session_id=sid, customer_id=cust_a, code=config.DEV_OTP)

    assert session_manager.is_verified(session_id=sid, customer_id=cust_a)
    assert not session_manager.is_verified(session_id=sid, customer_id=cust_b)


def test_initiate_unknown_reference_is_neutral():
    svc = AuthService()
    init = svc.initiate(session_id="sess-unknown", order_ref="UC-DOES-NOT-EXIST")
    assert not init.success
    # No PII, no customer id disclosed for an unknown reference.
    assert init.customer_id is None


# -------------------------------------------------------------------- API tier

def test_api_initiate_hides_pii_and_dev_exposes_otp(client):
    order_id, _ = _known_order_customer()
    resp = client.post("/api/auth/initiate", json={"order_ref": order_id})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"]
    # Masked destination only - no full number, name, or email.
    assert data["masked_destination"].startswith("XXXX")
    assert "email" not in data and "name" not in data and "mobile" not in data
    # Dev mode exposes the OTP for the self-contained demo.
    assert data.get("dev_otp") == config.DEV_OTP


def test_api_verify_flow(client):
    order_id, customer_id = _known_order_customer()
    client.post("/api/auth/initiate", json={"order_ref": order_id})

    bad = client.post(
        "/api/auth/verify", json={"customer_id": customer_id, "code": "999999"}
    )
    assert bad.status_code == 400
    assert not bad.get_json()["success"]

    good = client.post(
        "/api/auth/verify", json={"customer_id": customer_id, "code": config.DEV_OTP}
    )
    assert good.status_code == 200
    assert good.get_json()["success"]

    status = client.get("/api/auth/status")
    assert status.get_json()["verified"] is True


def test_dev_clear_session_resets_verified_state_and_cookie(client):
    order_id, customer_id = _known_order_customer()
    client.post("/api/auth/initiate", json={"order_ref": order_id})
    verified = client.post(
        "/api/auth/verify", json={"customer_id": customer_id, "code": config.DEV_OTP}
    )
    assert verified.get_json()["success"]

    with client.session_transaction() as browser_session:
        old_sid = browser_session["sid"]
    assert session_manager.get(old_sid) is not None

    cleared = client.post("/api/auth/dev-clear-session")
    assert cleared.status_code == 200
    assert cleared.get_json()["success"]
    assert session_manager.get(old_sid) is None

    status = client.get("/api/auth/status").get_json()
    assert status["verified"] is False
    assert status["customer_id"] is None
    with client.session_transaction() as browser_session:
        assert browser_session["sid"] != old_sid


def test_dev_clear_session_is_unavailable_outside_dev_mode(client):
    client.application.config["DEV_MODE"] = False

    page = client.get("/")
    response = client.post("/api/auth/dev-clear-session")

    assert b"Clear session (Dev mode)" not in page.data
    assert response.status_code == 404


# ----------------------------------------------- additional negative scenarios

def test_empty_code_is_treated_as_invalid():
    """An empty code must never be treated as a match; it consumes an attempt."""
    svc = AuthService()
    sid = "sess-empty"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    result = svc.verify(session_id=sid, customer_id=customer_id, code="")
    assert not result.success
    assert result.status == RESULT_INVALID_CODE
    assert result.attempts_remaining == config.OTP_MAX_ATTEMPTS - 1
    assert not session_manager.is_verified(session_id=sid, customer_id=customer_id)


def test_wrong_length_code_is_invalid():
    """A code of the wrong length (not the exact OTP) is rejected."""
    svc = AuthService()
    sid = "sess-wronglen"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    # One digit short of the dev OTP.
    short_code = config.DEV_OTP[:-1]
    result = svc.verify(session_id=sid, customer_id=customer_id, code=short_code)
    assert not result.success
    assert result.status == RESULT_INVALID_CODE


def test_surrounding_whitespace_is_tolerated():
    """A correct code with surrounding whitespace still verifies (trimmed)."""
    svc = AuthService()
    sid = "sess-ws"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    padded = f"  {config.DEV_OTP}  "
    result = svc.verify(session_id=sid, customer_id=customer_id, code=padded)
    assert result.success
    assert result.status == RESULT_SUCCESS


def test_verify_without_initiate_has_no_challenge():
    """Verifying when no challenge was ever created returns NO_CHALLENGE and
    does not grant a session."""
    sid = "sess-noinit"
    customer_id = _a_customer_id()

    result = VerificationService().verify(
        session_id=sid, customer_id=customer_id, code=config.DEV_OTP
    )
    assert not result.success
    assert result.status == RESULT_NO_CHALLENGE
    assert not session_manager.is_verified(session_id=sid, customer_id=customer_id)


def test_attempts_countdown_is_sequential():
    """Each wrong attempt decrements remaining attempts: 2 -> 1 -> 0."""
    svc = AuthService()
    sid = "sess-countdown"
    customer_id = _a_customer_id()
    svc.initiate(session_id=sid, customer_ref=customer_id)

    r1 = svc.verify(session_id=sid, customer_id=customer_id, code="aaaaaa")
    r2 = svc.verify(session_id=sid, customer_id=customer_id, code="bbbbbb")
    r3 = svc.verify(session_id=sid, customer_id=customer_id, code="cccccc")

    assert [r1.attempts_remaining, r2.attempts_remaining, r3.attempts_remaining] == [2, 1, 0]
    assert r3.status == RESULT_TOO_MANY_ATTEMPTS


def test_api_verify_missing_customer_id_rejected(client):
    """The verify endpoint rejects a missing customer_id with 400 before
    touching the verification service."""
    order_id, _ = _known_order_customer()
    client.post("/api/auth/initiate", json={"order_ref": order_id})

    resp = client.post("/api/auth/verify", json={"code": config.DEV_OTP})
    assert resp.status_code == 400
    assert not resp.get_json()["success"]


def test_api_verify_missing_code_rejected(client):
    """The verify endpoint rejects a missing code with 400."""
    order_id, customer_id = _known_order_customer()
    client.post("/api/auth/initiate", json={"order_ref": order_id})

    resp = client.post("/api/auth/verify", json={"customer_id": customer_id})
    assert resp.status_code == 400
    assert not resp.get_json()["success"]
