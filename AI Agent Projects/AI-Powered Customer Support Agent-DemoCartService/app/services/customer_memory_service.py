"""Per-customer episodic + preference memory service (M-1, M-3, M-4).

All reads/writes are keyed strictly by customer_id, so one customer's lookup can
never return another customer's data (M-3). Episodic entries store only a
one-paragraph summary + outcome - never raw chat text (M-1). Customers can view,
correct, and delete their memory (M-4), and expired episodic rows can be purged
(12-month retention).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.database.db import get_session
from app.models.customer_memory import CustomerIssue, CustomerPreference


# ---- episodic ----

def record_issue(
    *,
    customer_id: str,
    summary: str,
    outcome: str | None = None,
    category: str | None = None,
    conversation_id: str | None = None,
) -> dict | None:
    """Store a one-paragraph episodic memory for a customer."""
    if not customer_id or not summary:
        return None
    session = get_session()
    try:
        issue = CustomerIssue(
            customer_id=customer_id,
            conversation_id=conversation_id,
            category=category,
            summary=summary.strip()[:800],
            outcome=outcome,
        )
        session.add(issue)
        session.commit()
        return _serialize_issue(issue)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_issues(customer_id: str) -> list[dict]:
    """Return a customer's episodic memory (newest first). Keyed by id (M-3)."""
    if not customer_id:
        return []
    session = get_session()
    try:
        rows = (
            session.query(CustomerIssue)
            .filter(CustomerIssue.customer_id == customer_id)
            .order_by(CustomerIssue.created_at.desc())
            .all()
        )
        return [_serialize_issue(r) for r in rows]
    finally:
        session.close()


# ---- preference ----

def get_preference(customer_id: str) -> dict | None:
    if not customer_id:
        return None
    session = get_session()
    try:
        pref = session.get(CustomerPreference, customer_id)
        return _serialize_pref(pref) if pref else None
    finally:
        session.close()


def set_preference(
    *, customer_id: str, preferred_language: str | None = None, contact_channel: str | None = None
) -> dict | None:
    """Create/update a customer's preferences (M-4 correct)."""
    if not customer_id:
        return None
    session = get_session()
    try:
        pref = session.get(CustomerPreference, customer_id)
        if pref is None:
            pref = CustomerPreference(customer_id=customer_id)
            session.add(pref)
        if preferred_language is not None:
            pref.preferred_language = preferred_language
        if contact_channel is not None:
            pref.contact_channel = contact_channel
        session.commit()
        return _serialize_pref(pref)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---- M-4 see / delete ----

def export_memory(customer_id: str) -> dict:
    """Return everything stored for a customer (the 'see' right, M-4)."""
    return {
        "customer_id": customer_id,
        "preferences": get_preference(customer_id),
        "issues": list_issues(customer_id),
    }


def delete_memory(customer_id: str) -> dict:
    """Delete all episodic + preference memory for a customer (M-4).

    Completes immediately here; the spec's 30-day SLA is comfortably met.
    """
    if not customer_id:
        return {"deleted_issues": 0, "deleted_preferences": 0}
    session = get_session()
    try:
        issues = session.query(CustomerIssue).filter(
            CustomerIssue.customer_id == customer_id
        )
        n_issues = issues.count()
        issues.delete(synchronize_session=False)

        pref = session.get(CustomerPreference, customer_id)
        n_pref = 0
        if pref is not None:
            session.delete(pref)
            n_pref = 1

        session.commit()
        return {"deleted_issues": n_issues, "deleted_preferences": n_pref}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def purge_expired(now: datetime | None = None) -> int:
    """Delete episodic rows past their 12-month retention. Returns count."""
    now = now or datetime.now(timezone.utc)
    session = get_session()
    try:
        q = session.query(CustomerIssue).filter(CustomerIssue.retain_until < now)
        count = q.count()
        q.delete(synchronize_session=False)
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---- serializers ----

def _serialize_issue(issue: CustomerIssue) -> dict:
    return {
        "id": issue.id,
        "customer_id": issue.customer_id,
        "conversation_id": issue.conversation_id,
        "category": issue.category,
        "summary": issue.summary,
        "outcome": issue.outcome,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "retain_until": issue.retain_until.isoformat() if issue.retain_until else None,
    }


def _serialize_pref(pref: CustomerPreference) -> dict:
    return {
        "customer_id": pref.customer_id,
        "preferred_language": pref.preferred_language,
        "contact_channel": pref.contact_channel,
        "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
    }
