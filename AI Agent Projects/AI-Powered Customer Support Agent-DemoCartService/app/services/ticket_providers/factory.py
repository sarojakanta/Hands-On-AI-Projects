"""Ticket provider selection.

Picks the provider from config (TICKET_PROVIDER). Defaults to local so the demo
is self-contained. Unknown values fall back to local.
"""

from __future__ import annotations

from app.config import config
from app.services.ticket_providers.base import TicketProvider
from app.services.ticket_providers.local_provider import LocalTicketProvider


def build_provider() -> TicketProvider:
    choice = (config.TICKET_PROVIDER or "local").lower()
    if choice == "salesforce":
        from app.services.ticket_providers.salesforce_provider import (
            SalesforceTicketProvider,
        )

        return SalesforceTicketProvider()
    return LocalTicketProvider()


# Convenience singleton.
ticket_provider: TicketProvider = build_provider()
