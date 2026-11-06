"""CRM customer-profile tool: get_customer_profile.

Account-scoped read. Requires a verified session for the customer. Returns only
curated profile fields (language, tier, masked contact) - never raw PII.
"""

from __future__ import annotations

from app.services import customer_service
from mcp_server.tools.authorization import authorize_account_access
from mcp_server.tools.base import ok, err, require_string


GET_CUSTOMER_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Verified session id."},
        "customer_id": {"type": "string", "description": "Verified customer id."},
    },
    "required": ["session_id", "customer_id"],
    "additionalProperties": False,
}


def get_customer_profile(args: dict) -> dict:
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    customer_id, e = require_string(args, "customer_id")
    if e:
        return e

    auth = authorize_account_access(session_id=session_id, customer_id=customer_id)
    if not auth.authorized:
        return err("unauthorized", "Identity verification is required to view your profile.")

    profile = customer_service.get_profile(customer_id)
    if not profile:
        return err("not_found", "No profile was found for your account.")
    return ok(data=profile, message="Customer profile retrieved.")
