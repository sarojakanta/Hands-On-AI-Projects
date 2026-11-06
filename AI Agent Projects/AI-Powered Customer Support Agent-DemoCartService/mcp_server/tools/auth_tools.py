"""Authentication MCP tools: initiate_otp, verify_customer.

These wrap the auth service so the agent can trigger verification through the
same scoped-tool channel as everything else. The tools NEVER decide OTP
validity themselves - they delegate to the backend verification service.
"""

from __future__ import annotations

from app.auth.auth_service import AuthService
from mcp_server.tools.base import ok, err, require_string

_auth_service = AuthService()


INITIATE_OTP_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Browser/session id."},
        "order_ref": {"type": "string", "description": "Order reference, optional."},
        "customer_ref": {"type": "string", "description": "Customer id, optional."},
    },
    "required": ["session_id"],
    "additionalProperties": False,
}

VERIFY_CUSTOMER_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "customer_id": {"type": "string"},
        "code": {"type": "string", "description": "The OTP code to verify."},
    },
    "required": ["session_id", "customer_id", "code"],
    "additionalProperties": False,
}


def initiate_otp(args: dict) -> dict:
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    order_ref = args.get("order_ref")
    customer_ref = args.get("customer_ref")
    if not (order_ref or customer_ref):
        return err(
            "invalid_arguments",
            "Provide either 'order_ref' or 'customer_ref' to identify the customer.",
        )

    result = _auth_service.initiate(
        session_id=session_id,
        order_ref=order_ref.strip() if isinstance(order_ref, str) else None,
        customer_ref=customer_ref.strip() if isinstance(customer_ref, str) else None,
    )
    if not result.success:
        return err("initiation_failed", result.message)

    # Never leak PII: only masked destination + opaque customer id. The dev
    # OTP is intentionally NOT returned here; the dev UI gets it via the HTTP
    # auth endpoint, not through the tool channel.
    return ok(
        data={
            "customer_id": result.customer_id,
            "masked_destination": result.masked_destination,
            "channel": result.channel,
        },
        message=result.message,
    )


def verify_customer(args: dict) -> dict:
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    customer_id, e = require_string(args, "customer_id")
    if e:
        return e
    code, e = require_string(args, "code")
    if e:
        return e

    result = _auth_service.verify(
        session_id=session_id, customer_id=customer_id, code=code
    )
    return ok(
        data={
            "verified": result.success,
            "status": result.status,
            "attempts_remaining": result.attempts_remaining,
        },
        message=result.message,
    )
