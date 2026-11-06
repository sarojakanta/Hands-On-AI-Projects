"""Deterministic seed data for the simulated business database.

Generates a consistent, related dataset:
  * >= 20 customers
  * >= 50 orders (each belonging to a customer)
  * a shipment for most orders
  * a handful of physical stores

Seeding is deterministic (fixed random seed) so tests and demos are stable,
and idempotent (it resets the relevant tables before inserting).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.database.db import get_session, reset_db
from app.models.customer import Customer
from app.models.order import Order, ORDER_STATUSES
from app.models.shipment import Shipment, SHIPMENT_STATUSES
from app.models.store import Store

# Fixed seed => deterministic data.
_RNG = random.Random(42)

_FIRST_NAMES = [
    "Aarav", "Diya", "Vivaan", "Isha", "Aditya", "Ananya", "Kabir", "Meera",
    "Rohan", "Sara", "Arjun", "Nisha", "Dev", "Priya", "Karan", "Riya",
    "Vikram", "Tara", "Nikhil", "Pooja", "Siddharth", "Kavya", "Manav", "Anjali",
]
_LAST_NAMES = [
    "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Khan", "Das",
    "Mehta", "Rao", "Verma", "Bose", "Chopra", "Menon", "Shah", "Pillai",
]

_COURIERS = ["BlueDart", "Delhivery", "DTDC", "Ecom Express", "India Post"]

_STORES = [
    ("STORE-01", "DemoCartService Flagship - Connaught Place", "12 Connaught Place", "New Delhi",
     "Mon-Sat 10:00-21:00, Sun 11:00-19:00", "+91-1140001001"),
    ("STORE-02", "DemoCartService - Bandra", "45 Linking Road, Bandra West", "Mumbai",
     "Mon-Sun 11:00-22:00", "+91-2240002002"),
    ("STORE-03", "DemoCartService - Koramangala", "80 5th Block, Koramangala", "Bengaluru",
     "Mon-Sat 10:30-21:30, Sun 11:00-20:00", "+91-8040003003"),
    ("STORE-04", "DemoCartService - Banjara Hills", "9 Road No. 12, Banjara Hills", "Hyderabad",
     "Mon-Sun 10:00-21:00", "+91-4040004004"),
    ("STORE-05", "DemoCartService - Park Street", "22 Park Street", "Kolkata",
     "Mon-Sat 10:00-20:30, Sun closed", "+91-3340005005"),
]


_LANGUAGES = ("en", "en", "en", "hi", "ta", "fr")
_TIERS = ("STANDARD", "STANDARD", "STANDARD", "SILVER", "GOLD", "PLATINUM")
_DEMO_MOBILE = "+91-0000000000"


def _make_customers(n: int = 24) -> list[Customer]:
    customers: list[Customer] = []
    used_emails: set[str] = set()
    for i in range(n):
        first = _FIRST_NAMES[i % len(_FIRST_NAMES)]
        last = _LAST_NAMES[_RNG.randrange(len(_LAST_NAMES))]
        cust_id = f"CUST-{1001 + i}"
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        used_emails.add(email)
        customers.append(
            Customer(
                id=cust_id,
                name=f"{first} {last}",
                email=email,
                mobile=_DEMO_MOBILE,
                language=_RNG.choice(_LANGUAGES),
                tier=_RNG.choice(_TIERS),
            )
        )
    return customers


def _make_orders_and_shipments(
    customers: list[Customer], n_orders: int = 55
) -> tuple[list[Order], list[Shipment]]:
    orders: list[Order] = []
    shipments: list[Shipment] = []
    now = datetime.now(timezone.utc)

    # Give the first order a well-known id used in the spec's Scenario A.
    known_ids = ["UC-48213"]

    for i in range(n_orders):
        if i < len(known_ids):
            order_id = known_ids[i]
        else:
            order_id = f"UC-{48214 + i}"

        customer = _RNG.choice(customers)
        days_ago = _RNG.randrange(1, 60)
        order_date = now - timedelta(days=days_ago)
        amount = round(_RNG.uniform(299, 24999), 2)
        status = _RNG.choice(ORDER_STATUSES)

        orders.append(
            Order(
                id=order_id,
                customer_id=customer.id,
                order_date=order_date,
                amount=amount,
                currency="INR",
                status=status,
            )
        )

        # Create a shipment for orders that have progressed past processing.
        if status in ("SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED", "RETURNED"):
            ship_status = {
                "SHIPPED": "IN_TRANSIT",
                "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY",
                "DELIVERED": "DELIVERED",
                "RETURNED": "RETURNED_TO_ORIGIN",
            }[status]
            # A few in-transit orders are explicitly delayed for the demo.
            if ship_status == "IN_TRANSIT" and _RNG.random() < 0.3:
                ship_status = "DELAYED"

            expected = order_date + timedelta(days=_RNG.randrange(2, 9))
            shipments.append(
                Shipment(
                    id=f"SHP-{90001 + i}",
                    order_id=order_id,
                    courier=_RNG.choice(_COURIERS),
                    tracking_number=f"{_RNG.randrange(10**9, 10**10)}",
                    status=ship_status,
                    expected_delivery_date=expected,
                )
            )

    return orders, shipments


def _make_stores() -> list[Store]:
    return [
        Store(
            id=sid,
            name=name,
            address=address,
            city=city,
            opening_hours=hours,
            phone=phone,
        )
        for (sid, name, address, city, hours, phone) in _STORES
    ]


def seed(reset: bool = True) -> dict[str, int]:
    """Seed the database. Returns counts of inserted rows.

    Args:
        reset: when True, drop and recreate all tables before inserting.
    """
    if reset:
        reset_db()

    session = get_session()
    try:
        customers = _make_customers()
        session.add_all(customers)
        session.flush()

        orders, shipments = _make_orders_and_shipments(customers)
        session.add_all(orders)
        session.flush()
        session.add_all(shipments)

        stores = _make_stores()
        session.add_all(stores)

        session.commit()

        counts = {
            "customers": len(customers),
            "orders": len(orders),
            "shipments": len(shipments),
            "stores": len(stores),
        }
        return counts
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover - manual invocation
    result = seed()
    print("Seeded:", result)
