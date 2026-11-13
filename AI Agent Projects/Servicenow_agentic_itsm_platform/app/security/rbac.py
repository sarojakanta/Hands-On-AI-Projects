"""Role-based access control primitives (shared by the approval framework and the API)."""
from __future__ import annotations

from dataclasses import dataclass

# Ascending privilege. Each role implies all roles below it.
ROLE_ORDER = ("viewer", "analyst", "approver", "admin")


@dataclass(frozen=True)
class Principal:
    username: str
    roles: frozenset[str] = frozenset({"viewer"})

    @classmethod
    def of(cls, username: str, *roles: str) -> "Principal":
        return cls(username, frozenset(roles or ("viewer",)))


def rank(principal: Principal) -> int:
    return max((ROLE_ORDER.index(r) for r in principal.roles if r in ROLE_ORDER), default=-1)


def has_role(principal: Principal, required: str) -> bool:
    if required not in ROLE_ORDER:
        raise ValueError(f"Unknown role: {required}")
    return rank(principal) >= ROLE_ORDER.index(required)
