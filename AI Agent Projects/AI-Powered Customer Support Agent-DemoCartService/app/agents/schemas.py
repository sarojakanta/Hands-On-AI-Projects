"""Agent intent enum and structured output schema.

The agent always produces a structured result with these fields (spec §6):
  reply, language, intent, confidence, escalate, escalation_reason,
  authentication_required, source_references.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


# Supported intents (spec §6).
INTENTS = (
    "ORDER_STATUS",
    "SHIPMENT_STATUS",
    "DELIVERY_DELAY",
    "ADDRESS_CHANGE",
    "RETURN_POLICY",
    "EXCHANGE_POLICY",
    "WARRANTY_POLICY",
    "SHIPPING_POLICY",
    # Additional policy/FAQ intents (beyond the spec baseline) so general
    # how-does-it-work questions route to RAG rather than account lookups.
    "DELIVERY_POLICY",
    "FAQ",
    "STORE_HOURS",
    "STORE_LOCATOR",
    "COMPLAINT",
    "HUMAN_SUPPORT",
    "GREETING",
    "OTHER",
)

# Intents that touch a specific customer's account and therefore require a
# verified session before any account data is revealed.
ACCOUNT_INTENTS = frozenset(
    {"ORDER_STATUS", "SHIPMENT_STATUS", "DELIVERY_DELAY", "ADDRESS_CHANGE"}
)

# Policy/FAQ intents answered from RAG.
POLICY_INTENTS = frozenset(
    {
        "RETURN_POLICY",
        "EXCHANGE_POLICY",
        "WARRANTY_POLICY",
        "SHIPPING_POLICY",
        "DELIVERY_POLICY",
        "FAQ",
    }
)


def normalize_intent(value: str | None) -> str:
    """Coerce an arbitrary string to a known intent, defaulting to OTHER."""
    if not value:
        return "OTHER"
    v = value.strip().upper()
    return v if v in INTENTS else "OTHER"


@dataclass
class AgentOutput:
    reply: str = ""
    language: str = "en"
    intent: str = "OTHER"
    confidence: float = 0.0
    escalate: bool = False
    escalation_reason: str | None = None
    authentication_required: bool = False
    source_references: list[str] = field(default_factory=list)
    # Set when an escalation created a support ticket (Phase 7).
    ticket_reference: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
