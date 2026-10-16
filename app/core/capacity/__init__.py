"""Capacity controls, rate limiting governance, and tenant quotas (Phase 8E).

Provides:
- TenantQuotaExceededError (HTTP 429 with Retry-After header)
- TenantQuotaManager (database-backed, multi-replica safe tenant capacity controls)
"""

from app.core.capacity.quotas import (
    TenantQuotaExceededError,
    TenantQuotaManager,
)

__all__ = [
    "TenantQuotaExceededError",
    "TenantQuotaManager",
]
