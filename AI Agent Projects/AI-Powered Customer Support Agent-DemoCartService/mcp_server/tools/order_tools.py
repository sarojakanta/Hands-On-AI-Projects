"""Order MCP tool: get_order.

Account-scoped. Requires a verified session for the exact customer who owns the
order. Authorization is enforced here in backend code, independent of anything
the LLM says.
"""

from __future__ import annotations

from app.services import order_service
from mcp_server.tools.authorization import authorize_account_access
from mcp_server.tools.base import ok, err, require_string


GET_ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Verified session id."},
        "customer_id": {"type": "string", "description": "Verified customer id."},
        "order_id": {"type": "string", "description": "Order reference to fetch."},
    },
    "required": ["session_id", "customer_id", "order_id"],
    "additionalProperties": False,
}


def get_order(args: dict) -> dict:
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    customer_id, e = require_string(args, "customer_id")
    if e:
        return e
    order_id, e = require_string(args, "order_id")
    if e:
        return e

    # 1) Authorization: the session must be verified for THIS customer.
    auth = authorize_account_access(session_id=session_id, customer_id=customer_id)
    if not auth.authorized:
        return err("unauthorized", "Identity verification is required to view order details.")

    # 2) Ownership: the order must belong to the verified customer. This blocks
    #    a verified customer A from reading customer B's order.
    if not order_service.order_belongs_to(order_id, customer_id):
        # Do not reveal whether the order exists for someone else.
        return err("not_found", "No such order was found for your account.")

    order = order_service.get_order(order_id)
    if not order:
        return err("not_found", "No such order was found for your account.")

    return ok(data=order, message="Order retrieved.")


# --- Guarded write: request_address_change (OMS) ---

from app.services import address_service  # noqa: E402

REQUEST_ADDRESS_CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Verified session id."},
        "customer_id": {"type": "string", "description": "Verified customer id."},
        "order_id": {"type": "string", "description": "Order whose address to change."},
        "new_address": {"type": "string", "description": "The requested new delivery address."},
    },
    "required": ["session_id", "customer_id", "order_id", "new_address"],
    "additionalProperties": False,
}


def request_address_change(args: dict) -> dict:
    """Guarded write: submit an address-change request after rule checks.

    Requires a verified session for the owning customer. The business rule
    (no change after dispatch) is enforced in address_service.
    """
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    customer_id, e = require_string(args, "customer_id")
    if e:
        return e
    order_id, e = require_string(args, "order_id")
    if e:
        return e
    new_address, e = require_string(args, "new_address")
    if e:
        return e

    auth = authorize_account_access(session_id=session_id, customer_id=customer_id)
    if not auth.authorized:
        return err("unauthorized", "Identity verification is required to change an address.")

    result = address_service.request_change(
        order_id=order_id, customer_id=customer_id, new_address=new_address
    )
    if not result.get("allowed"):
        return err("rejected", result.get("message", "Address change not allowed."))
    return ok(data=result, message=result.get("message", "Address-change request submitted."))
