"""Shipment business logic. Returns structured dicts only."""

from __future__ import annotations

from app.database.db import get_session
from app.models.order import Order
from app.models.shipment import Shipment


def _serialize_shipment(shipment: Shipment) -> dict:
    return {
        "shipment_id": shipment.id,
        "order_id": shipment.order_id,
        "courier": shipment.courier,
        "tracking_number": shipment.tracking_number,
        "status": shipment.status,
        "expected_delivery_date": (
            shipment.expected_delivery_date.isoformat()
            if shipment.expected_delivery_date
            else None
        ),
    }


def get_shipment_by_order(order_id: str) -> dict | None:
    """Return the serialized shipment for an order, or None if absent."""
    session = get_session()
    try:
        order_id = (order_id or "").strip()
        shipment = (
            session.query(Shipment).filter(Shipment.order_id == order_id).first()
        )
        return _serialize_shipment(shipment) if shipment else None
    finally:
        session.close()


def shipment_order_customer(order_id: str) -> str | None:
    """Return the customer_id that owns the order for this shipment lookup."""
    session = get_session()
    try:
        order = session.get(Order, (order_id or "").strip())
        return order.customer_id if order else None
    finally:
        session.close()
