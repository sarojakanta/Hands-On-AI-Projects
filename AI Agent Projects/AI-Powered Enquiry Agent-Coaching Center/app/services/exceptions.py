"""Service-layer exceptions.

These are transport-agnostic; the API layer maps them to HTTP responses.
"""
from __future__ import annotations


class ServiceError(Exception):
    """Base class for service-layer errors."""


class NotFoundError(ServiceError):
    """A requested entity does not exist (or not within this tenant)."""


class ValidationError(ServiceError):
    """A requested operation failed validation."""


class ConflictError(ServiceError):
    """A requested operation conflicts with existing state."""
