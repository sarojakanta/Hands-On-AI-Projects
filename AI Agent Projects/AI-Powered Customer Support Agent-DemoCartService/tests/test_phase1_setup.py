"""Phase 1 verification tests.

Confirms:
  * the app boots and serves the chat UI,
  * the health endpoint responds,
  * the placeholder chat endpoint validates input and echoes a reply,
  * the seed data meets the spec minimums and relationships are consistent.
"""

from __future__ import annotations

from app.database.db import get_session
from app.models.customer import Customer
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.store import Store


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_index_renders_chat(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "DemoCartService Support Assistant" in body
    # Dev-mode badge should render when DEV_MODE is on.
    assert "MOCK OTP" in body


def test_chat_requires_message(client):
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_returns_reply_and_conversation_id(client):
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reply"]
    assert data["conversation_id"]


def test_seed_minimum_counts(_seeded_db):
    assert _seeded_db["customers"] >= 20
    assert _seeded_db["orders"] >= 50
    assert _seeded_db["shipments"] >= 1
    assert _seeded_db["stores"] >= 1


def test_seeded_store_names_use_demo_brand():
    session = get_session()
    try:
        stores = session.query(Store).all()
        assert stores
        assert all(store.name.startswith("DemoCartService") for store in stores)
    finally:
        session.close()


def test_seed_relationships_consistent():
    session = get_session()
    try:
        customer_ids = {c.id for c in session.query(Customer).all()}
        orders = session.query(Order).all()
        assert orders, "expected orders to be seeded"
        # Every order references an existing customer.
        for order in orders:
            assert order.customer_id in customer_ids

        # Every shipment references an existing order.
        order_ids = {o.id for o in orders}
        for shipment in session.query(Shipment).all():
            assert shipment.order_id in order_ids

        # Spec Scenario A order exists.
        assert session.get(Order, "UC-48213") is not None

        # Stores exist.
        assert session.query(Store).count() >= 1
    finally:
        session.close()


def test_seeded_customers_use_demo_phone_number():
    session = get_session()
    try:
        customers = session.query(Customer).all()
        assert customers
        assert all(customer.mobile == "+91-0000000000" for customer in customers)
    finally:
        session.close()
