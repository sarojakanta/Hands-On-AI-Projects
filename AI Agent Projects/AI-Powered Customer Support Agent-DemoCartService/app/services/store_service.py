"""Store business logic. Public information - no authentication required."""

from __future__ import annotations

from app.database.db import get_session
from app.models.store import Store


def _serialize_store(store: Store) -> dict:
    return {
        "store_id": store.id,
        "name": store.name,
        "address": store.address,
        "city": store.city,
        "opening_hours": store.opening_hours,
        "phone": store.phone,
    }


def search_stores(query: str | None = None, city: str | None = None) -> list[dict]:
    """Search stores by free-text query (name/city/address) and/or city.

    Returns a list of serialized stores (possibly empty).
    """
    session = get_session()
    try:
        q = session.query(Store)
        if city:
            q = q.filter(Store.city.ilike(f"%{city.strip()}%"))
        stores = q.all()

        if query:
            needle = query.strip().lower()
            stores = [
                s
                for s in stores
                if needle in s.name.lower()
                or needle in s.city.lower()
                or needle in s.address.lower()
            ]
        return [_serialize_store(s) for s in stores]
    finally:
        session.close()


def get_store_hours(store_id: str) -> dict | None:
    """Return opening hours for a specific store, or None if not found."""
    session = get_session()
    try:
        store = session.get(Store, (store_id or "").strip())
        if not store:
            return None
        return {
            "store_id": store.id,
            "name": store.name,
            "opening_hours": store.opening_hours,
        }
    finally:
        session.close()
