"""Customer (CRM) business logic.

Backs the crm-mcp get_customer_profile tool. Returns only curated, non-secret
profile fields (language, preferences, tier) - never the full mobile number or
other sensitive PII.
"""

from __future__ import annotations

from app.database.db import get_session
from app.models.customer import Customer
from app.services.masking import mask_phone


def get_profile(customer_id: str) -> dict | None:
    """Return a safe CRM profile for a customer, or None if not found."""
    session = get_session()
    try:
        customer = session.get(Customer, (customer_id or "").strip())
        if not customer:
            return None
        return {
            "customer_id": customer.id,
            "name": customer.name,
            "language": customer.language,
            "tier": customer.tier,
            # Masked contact only - keep just the last 2 digits (T-4).
            "masked_mobile": mask_phone(customer.mobile),
        }
    finally:
        session.close()
