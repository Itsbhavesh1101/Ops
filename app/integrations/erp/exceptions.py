"""Exceptions for ERP/SIS adapter operations.

Part of Phase 7B Real ERP/SIS Adapter.
"""


class ErpAdapterError(Exception):
    """Base exception for all ERP/SIS adapter errors."""


class ScopeUnresolvedError(ErpAdapterError):
    """Raised when tenant/organization scope is unresolved or invalid under BLK-01."""


class ErpConnectionError(ErpAdapterError):
    """Raised when the ERP database connection fails or is unavailable."""


class ErpTimeoutError(ErpAdapterError):
    """Raised when an ERP query times out."""


class ErpPermissionDeniedError(ErpAdapterError):
    """Raised when ERP database access credentials or permissions are denied."""


class ReadOnlyViolationError(ErpAdapterError):
    """Raised when any mutation (INSERT, UPDATE, DELETE, DDL) is attempted against read-only ERP."""


class EntityNotFoundError(ErpAdapterError):
    """Raised when an expected upstream entity does not exist."""


__all__ = [
    "EntityNotFoundError",
    "ErpAdapterError",
    "ErpConnectionError",
    "ErpPermissionDeniedError",
    "ErpTimeoutError",
    "ReadOnlyViolationError",
    "ScopeUnresolvedError",
]
