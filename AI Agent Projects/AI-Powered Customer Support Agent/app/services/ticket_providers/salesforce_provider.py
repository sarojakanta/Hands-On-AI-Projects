"""Salesforce ticket provider (opt-in).

Creates a Case in a Salesforce org via simple-salesforce, while ALSO writing a
local mirror row so the human dashboard and get/list keep working against one
consistent store. The Salesforce Case id is stored as the mirror's
external_id.

This provider never logs credentials. If Salesforce is unreachable or
misconfigured and TICKET_FALLBACK_LOCAL is on, it degrades to local-only so a
ticket is never lost.

Field mapping (our ticket -> Salesforce Case):
    category          -> Subject (prefixed) / Type
    priority          -> Priority
    status (OPEN)     -> Status ("New")
    summary + reason  -> Description
    customer_id       -> SuppliedName / (custom field if configured)

Requires a Connected App and the OAuth username-password flow credentials in
config (SALESFORCE_*). simple-salesforce is an optional dependency.
"""

from __future__ import annotations

import logging

from app.config import config
from app.services.ticket_providers.base import TicketProvider
from app.services.ticket_providers.local_provider import LocalTicketProvider

logger = logging.getLogger(__name__)

_PRIORITY_MAP = {"LOW": "Low", "MEDIUM": "Medium", "HIGH": "High", "URGENT": "High"}


class SalesforceConfigError(RuntimeError):
    pass


class SalesforceTicketProvider(TicketProvider):
    name = "salesforce"

    def __init__(self, local: LocalTicketProvider | None = None) -> None:
        # Local mirror keeps the dashboard/get/list working regardless of SF.
        self._local = local or LocalTicketProvider()
        self._sf = None

    # ---- connection ----

    def _credentials_present(self) -> bool:
        return bool(
            config.SALESFORCE_USERNAME
            and config.SALESFORCE_PASSWORD
            and config.SALESFORCE_SECURITY_TOKEN
        )

    def _client(self):
        """Lazily build and cache the Salesforce client."""
        if self._sf is not None:
            return self._sf
        if not self._credentials_present():
            raise SalesforceConfigError("Salesforce credentials are not configured.")
        try:
            from simple_salesforce import Salesforce
        except ImportError as exc:  # pragma: no cover - optional dep
            raise SalesforceConfigError(
                "simple-salesforce is not installed. Run: pip install simple-salesforce"
            ) from exc

        self._sf = Salesforce(
            username=config.SALESFORCE_USERNAME,
            password=config.SALESFORCE_PASSWORD,
            security_token=config.SALESFORCE_SECURITY_TOKEN,
            domain=config.SALESFORCE_DOMAIN,
        )
        return self._sf

    # ---- mapping ----

    @staticmethod
    def _to_case_fields(
        *, category, priority, escalation_reason, original_request, summary, customer_id
    ) -> dict:
        description_parts = []
        if summary:
            description_parts.append(f"Summary: {summary}")
        if escalation_reason:
            description_parts.append(f"Escalation reason: {escalation_reason}")
        if original_request:
            description_parts.append(f"Original request: {original_request}")
        if customer_id:
            description_parts.append(f"Customer reference: {customer_id}")

        return {
            "Subject": f"[{(category or 'GENERAL').upper()}] Support escalation",
            "Description": "\n".join(description_parts) or "Escalated by AI agent.",
            "Priority": _PRIORITY_MAP.get((priority or "MEDIUM").upper(), "Medium"),
            "Status": "New",
            "Origin": "AI Support Agent",
        }

    # ---- interface ----

    def create_ticket(self, **kwargs) -> dict:
        # Always write the local mirror first so the ticket exists for the
        # dashboard even if Salesforce fails.
        ticket = self._local.create_ticket(**kwargs)

        try:
            sf = self._client()
            case_fields = self._to_case_fields(
                category=kwargs.get("category"),
                priority=kwargs.get("priority", "MEDIUM"),
                escalation_reason=kwargs.get("escalation_reason"),
                original_request=kwargs.get("original_request"),
                summary=kwargs.get("summary"),
                customer_id=kwargs.get("customer_id"),
            )
            result = sf.Case.create(case_fields)
            case_id = result.get("id") if isinstance(result, dict) else None
            ticket["external_id"] = case_id
            ticket["provider"] = "salesforce"
            logger.info("Created Salesforce Case for ticket %s", ticket.get("ticket_id"))
            return ticket
        except SalesforceConfigError:
            if config.TICKET_FALLBACK_LOCAL:
                logger.warning("Salesforce not configured; ticket stored locally only.")
                ticket["provider"] = "local"
                return ticket
            raise
        except Exception as exc:  # noqa: BLE001 - network/API errors
            if config.TICKET_FALLBACK_LOCAL:
                logger.warning("Salesforce Case creation failed (%s); local only.", exc)
                ticket["provider"] = "local"
                return ticket
            raise

    def get_ticket(self, ticket_id: str) -> dict | None:
        # Reads come from the local mirror.
        return self._local.get_ticket(ticket_id)

    def list_tickets(self, status: str | None = None) -> list[dict]:
        return self._local.list_tickets(status)

    def update_ticket(self, ticket_id: str, **fields) -> dict | None:
        # Dashboard updates go to the local mirror. (Pushing updates back to
        # Salesforce could be added here later.)
        return self._local.update_ticket(ticket_id, **fields)
