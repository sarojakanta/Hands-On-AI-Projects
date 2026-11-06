"""Tool guardrails.

A backend-enforced authorization layer that sits in front of MCP tool calls.
Even though the MCP tools themselves re-validate (defense in depth), this layer
gives the agent a single place to decide:

  * whether a tool is permitted for the current intent,
  * whether the session is authenticated for account-scoped tools,
  * whether arguments are well-formed,
  * whether a SENSITIVE operation (e.g. address change) needs explicit customer
    confirmation before proceeding,
  * whether the caller is trying to reach another customer's records.

These decisions are deterministic and do not depend on the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tools that require a verified session (account-scoped across crm/oms).
_ACCOUNT_TOOLS = {
    "get_order",
    "get_shipment_status",
    "get_customer_profile",
    "request_address_change",
}

# Sensitive write/modify operations require explicit customer confirmation.
_SENSITIVE_TOOLS = {"request_address_change"}


@dataclass
class ToolDecision:
    allowed: bool
    reason: str | None = None
    needs_confirmation: bool = False


def authorize_tool_call(
    *,
    tool_name: str,
    arguments: dict,
    authenticated: bool,
    authenticated_customer_id: str | None,
) -> ToolDecision:
    """Decide whether a tool call may proceed."""
    if not isinstance(arguments, dict):
        return ToolDecision(allowed=False, reason="invalid_arguments")

    # Account tools require verification AND that the claimed customer matches
    # the verified session (cross-customer protection).
    if tool_name in _ACCOUNT_TOOLS:
        if not authenticated or not authenticated_customer_id:
            return ToolDecision(allowed=False, reason="not_verified")
        claimed = arguments.get("customer_id")
        if claimed and claimed != authenticated_customer_id:
            return ToolDecision(allowed=False, reason="cross_customer")

    if tool_name in _SENSITIVE_TOOLS:
        return ToolDecision(allowed=True, needs_confirmation=True)

    return ToolDecision(allowed=True)


# Intent-level sensitive operations that require explicit confirmation before
# the agent acts (spec §11: "explicit customer confirmation before sensitive
# operations such as changing an address").
_SENSITIVE_INTENTS = {"ADDRESS_CHANGE"}

_CONFIRMATION_WORDS = (
    "yes", "confirm", "go ahead", "please do", "do it", "correct", "proceed",
)


def requires_confirmation(intent: str) -> bool:
    return intent in _SENSITIVE_INTENTS


def is_confirmation(message: str) -> bool:
    low = (message or "").strip().lower()
    return any(word in low for word in _CONFIRMATION_WORDS)
