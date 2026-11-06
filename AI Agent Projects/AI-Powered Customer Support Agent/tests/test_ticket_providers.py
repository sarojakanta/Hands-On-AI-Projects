"""Ticket provider abstraction tests.

Covers the local provider (CRUD + update), provider selection via config, and
the Salesforce provider with a MOCKED client (no live org): field mapping,
local-mirror write, and graceful fallback to local on error / missing config.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import config
from app.models.ticket import Ticket
from app.services.ticket_providers import local_provider
from app.services.ticket_providers.local_provider import LocalTicketProvider
from app.services.ticket_providers.salesforce_provider import SalesforceTicketProvider
from app.services.ticket_providers import factory


# ------------------------------------------------------------- local provider

def test_local_create_and_get():
    p = LocalTicketProvider()
    created = p.create_ticket(
        category="ORDER_STATUS",
        customer_id="CUST-1001",
        conversation_id="conv-x",
        original_request="Where is my order?",
        summary="Asking about order status.",
    )
    assert created["ticket_id"].startswith("TKT-")
    assert created["status"] == "OPEN"
    fetched = p.get_ticket(created["ticket_id"])
    assert fetched and fetched["ticket_id"] == created["ticket_id"]


def test_local_list_filter_by_status():
    p = LocalTicketProvider()
    p.create_ticket(category="COMPLAINT", summary="x")
    all_open = p.list_tickets(status="OPEN")
    assert all(t["status"] == "OPEN" for t in all_open)


def test_local_update_ticket():
    p = LocalTicketProvider()
    created = p.create_ticket(category="COMPLAINT", summary="y")
    updated = p.update_ticket(
        created["ticket_id"],
        status="in_progress",
        assigned_agent="agent.smith",
        internal_notes="Looking into it.",
    )
    assert updated["status"] == "IN_PROGRESS"
    assert updated["assigned_agent"] == "agent.smith"
    assert updated["internal_notes"] == "Looking into it."


def test_local_update_rejects_bad_status():
    p = LocalTicketProvider()
    created = p.create_ticket(category="COMPLAINT", summary="z")
    updated = p.update_ticket(created["ticket_id"], status="NONSENSE")
    # Invalid status is ignored; remains OPEN.
    assert updated["status"] == "OPEN"


def test_dev_ticket_cap_prunes_oldest_resolved_and_preserves_open(
    monkeypatch,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Ticket.__table__.create(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(local_provider, "get_session", test_session)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "TICKET_PROVIDER", "local")
    monkeypatch.setattr(config, "DEV_TICKET_MAX_COUNT", 3)

    session = test_session()
    session.add_all(
        [
            Ticket(
                id="TKT-100001",
                category="TEST",
                status="RESOLVED",
                created_at=datetime(2020, 1, 1),
            ),
            Ticket(
                id="TKT-100002",
                category="TEST",
                status="CLOSED",
                created_at=datetime(2020, 1, 2),
            ),
        ]
    )
    session.commit()
    session.close()

    provider = LocalTicketProvider()
    first = provider.create_ticket(category="TEST")
    second = provider.create_ticket(category="TEST")
    third = provider.create_ticket(category="TEST")
    fourth = provider.create_ticket(category="TEST")

    tickets = provider.list_tickets()
    assert len(tickets) == 4  # Open tickets are preserved even over the cap.
    assert {ticket["ticket_id"] for ticket in tickets} == {
        first["ticket_id"],
        second["ticket_id"],
        third["ticket_id"],
        fourth["ticket_id"],
    }
    assert all(ticket["status"] == "OPEN" for ticket in tickets)
    assert len({ticket["ticket_id"] for ticket in (first, second, third, fourth)}) == 4
    engine.dispose()


def test_dev_ticket_cap_never_prunes_open_or_salesforce_mirror(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Ticket.__table__.create(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(local_provider, "get_session", test_session)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "TICKET_PROVIDER", "salesforce")
    monkeypatch.setattr(config, "DEV_TICKET_MAX_COUNT", 1)

    provider = LocalTicketProvider()
    provider.create_ticket(category="TEST")
    provider.create_ticket(category="TEST")

    assert len(provider.list_tickets()) == 2
    engine.dispose()


def test_dev_ticket_cap_is_disabled_outside_dev_mode(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Ticket.__table__.create(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(local_provider, "get_session", test_session)
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "TICKET_PROVIDER", "local")
    monkeypatch.setattr(config, "DEV_TICKET_MAX_COUNT", 1)

    provider = LocalTicketProvider()
    provider.create_ticket(category="TEST")
    provider.create_ticket(category="TEST")

    assert len(provider.list_tickets()) == 2
    engine.dispose()


# ------------------------------------------------------------------- factory

def test_factory_defaults_to_local(monkeypatch):
    monkeypatch.setattr(config, "TICKET_PROVIDER", "local")
    assert factory.build_provider().name == "local"


def test_factory_unknown_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(config, "TICKET_PROVIDER", "bogus")
    assert factory.build_provider().name == "local"


def test_factory_selects_salesforce(monkeypatch):
    monkeypatch.setattr(config, "TICKET_PROVIDER", "salesforce")
    assert factory.build_provider().name == "salesforce"


# -------------------------------------------------------- salesforce (mocked)

class _FakeCase:
    def __init__(self, recorder):
        self._recorder = recorder

    def create(self, fields):
        self._recorder.append(fields)
        return {"id": "500XX0000000ABC", "success": True}


class _FakeSF:
    def __init__(self, recorder):
        self.Case = _FakeCase(recorder)


def test_salesforce_create_maps_to_case_and_mirrors(monkeypatch):
    recorder: list[dict] = []
    provider = SalesforceTicketProvider()
    # Bypass real auth/client by injecting a fake Salesforce client.
    monkeypatch.setattr(provider, "_client", lambda: _FakeSF(recorder))

    ticket = provider.create_ticket(
        category="complaint",
        customer_id="CUST-1002",
        conversation_id="conv-sf",
        priority="URGENT",
        escalation_reason="Customer mentioned a lawyer.",
        original_request="This is the third late delivery.",
        summary="Repeated delays; legal threat.",
    )

    # Local mirror exists and is retrievable.
    assert ticket["ticket_id"].startswith("TKT-")
    assert ticket["external_id"] == "500XX0000000ABC"
    assert ticket["provider"] == "salesforce"

    # Case fields were mapped correctly.
    assert len(recorder) == 1
    case = recorder[0]
    assert case["Priority"] == "High"  # URGENT -> High
    assert case["Status"] == "New"
    assert "COMPLAINT" in case["Subject"]
    assert "lawyer" in case["Description"].lower()


def test_salesforce_falls_back_to_local_on_error(monkeypatch):
    provider = SalesforceTicketProvider()

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(provider, "_client", _boom)
    monkeypatch.setattr(config, "TICKET_FALLBACK_LOCAL", True)

    ticket = provider.create_ticket(category="COMPLAINT", summary="fallback test")
    # Ticket still created locally; marked as local.
    assert ticket["ticket_id"].startswith("TKT-")
    assert ticket.get("provider") == "local"
    assert "external_id" not in ticket


def test_salesforce_missing_config_falls_back(monkeypatch):
    # No credentials -> _client raises SalesforceConfigError -> local fallback.
    monkeypatch.setattr(config, "SALESFORCE_USERNAME", "")
    monkeypatch.setattr(config, "SALESFORCE_PASSWORD", "")
    monkeypatch.setattr(config, "SALESFORCE_SECURITY_TOKEN", "")
    monkeypatch.setattr(config, "TICKET_FALLBACK_LOCAL", True)

    provider = SalesforceTicketProvider()
    ticket = provider.create_ticket(category="COMPLAINT", summary="no creds")
    assert ticket.get("provider") == "local"
