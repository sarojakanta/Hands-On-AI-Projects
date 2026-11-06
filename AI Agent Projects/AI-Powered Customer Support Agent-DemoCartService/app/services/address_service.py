"""Address-change (OMS) business logic.

Backs the oms-mcp request_address_change tool - a GUARDED write. Business rule:
an address change is only allowed BEFORE the order is dispatched (spec's
delivery policy). Once a shipment exists / the order has shipped, the request
is rejected. We record a request row rather than mutating the order directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.database.db import get_session
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.address_change import AddressChangeRequest


# Statuses at/after which the address can no longer be changed.
_LOCKED_ORDER_STATUSES = {"SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED", "RETURNED", "CANCELLED"}


def request_change(*, order_id: str, customer_id: str, new_address: str) -> dict:
    """Create a guarded address-change request after rule checks.

    Returns a structured result dict: {allowed, status, message, request_id?}.
    """
    order_id = (order_id or "").strip()
    new_address = (new_address or "").strip()

    if not new_address:
        return {"allowed": False, "status": "REJECTED", "message": "A new address is required."}

    session = get_session()
    try:
        order = session.get(Order, order_id)
        # Ownership: the order must belong to the (already verified) customer.
        if not order or order.customer_id != customer_id:
            return {
                "allowed": False,
                "status": "REJECTED",
                "message": "No such order was found for your account.",
            }

        # Rule: cannot change once dispatched.
        shipment = session.query(Shipment).filter(Shipment.order_id == order_id).first()
        if order.status in _LOCKED_ORDER_STATUSES or shipment is not None:
            return {
                "allowed": False,
                "status": "REJECTED",
                "message": (
                    "This order has already been dispatched, so the delivery "
                    "address can no longer be changed."
                ),
            }

        # T-7 idempotency: an identical pending request for the same order +
        # address is not duplicated.
        existing = (
            session.query(AddressChangeRequest)
            .filter(
                AddressChangeRequest.order_id == order_id,
                AddressChangeRequest.customer_id == customer_id,
                AddressChangeRequest.new_address == new_address,
                AddressChangeRequest.status == "REQUESTED",
            )
            .first()
        )
        if existing is not None:
            return {
                "allowed": True,
                "status": "REQUESTED",
                "request_id": existing.id,
                "message": "Your address-change request is already on file.",
            }

        req = AddressChangeRequest(
            order_id=order_id,
            customer_id=customer_id,
            new_address=new_address,
            status="REQUESTED",
            created_at=datetime.now(timezone.utc),
        )
        session.add(req)
        session.commit()
        return {
            "allowed": True,
            "status": "REQUESTED",
            "request_id": req.id,
            "message": "Your address-change request has been submitted for processing.",
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
