"""Shipment MCP tool: get_shipment_status.

Account-scoped. Same authorization + ownership rules as get_order: the session
must be verified for the customer who owns the order the shipment belongs to.
"""

from __future__ import annotations

from app.services import shipment_service
from mcp_server.tools.authorization import authorize_account_access
from mcp_server.tools.base import ok, err, require_string


GET_SHIPMENT_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string"},
        "customer_id": {"type": "string"},
        "order_id": {"type": "string", "description": "Order whose shipment to fetch."},
    },
    "required": ["session_id", "customer_id", "order_id"],
    "additionalProperties": False,
}


def get_shipment_status(args: dict) -> dict:
    session_id, e = require_string(args, "session_id")
    if e:
        return e
    customer_id, e = require_string(args, "customer_id")
    if e:
        return e
    order_id, e = require_string(args, "order_id")
    if e:
        return e

    auth = authorize_account_access(session_id=session_id, customer_id=customer_id)
    if not auth.authorized:
        return err("unauthorized", "Identity verification is required to view shipment details.")

    # Ownership check: the order behind this shipment must belong to the
    # verified customer.
    owner = shipment_service.shipment_order_customer(order_id)
    if owner != customer_id:
        return err("not_found", "No shipment was found for your account.")

    shipment = shipment_service.get_shipment_by_order(order_id)
    if not shipment:
        return err("no_shipment", "This order does not have a shipment yet.")

    return ok(data=shipment, message="Shipment status retrieved.")
