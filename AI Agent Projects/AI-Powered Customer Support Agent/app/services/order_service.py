"""Order business logic.

Returns plain, structured dicts. The LLM never gets raw ORM objects or SQL
access - it only ever receives the curated fields below, through MCP tools.
"""

from __future__ import annotations

from app.database.db import get_session
from app.models.order import Order


def _serialize_order(order: Order) -> dict:
    return {
        "order_id": order.id,
        "customer_id": order.customer_id,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "amount": order.amount,
        "currency": order.currency,
        "status": order.status,
    }


def get_order(order_id: str) -> dict | None:
    """Return a serialized order, or None if not found."""
    session = get_session()
    try:
        order = session.get(Order, (order_id or "").strip())
        return _serialize_order(order) if order else None
    finally:
        session.close()


def order_belongs_to(order_id: str, customer_id: str) -> bool:
    """True if the order exists and belongs to the given customer."""
    session = get_session()
    try:
        order = session.get(Order, (order_id or "").strip())
        return bool(order and order.customer_id == customer_id)
    finally:
        session.close()
