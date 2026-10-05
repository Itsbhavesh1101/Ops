"""Canonical append-only audit trail package for E-7 operations.

Part of Phase 1 Foundation Task 1F.
Exports audit entity models, immutable schemas, validation primitives, and repository.
"""

from app.audit.models import AuditEventRecord
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.audit.validation import (
    MAX_AUDIT_DETAILS_BYTES,
    MAX_AUDIT_DETAILS_DEPTH,
    MAX_AUDIT_STRING_LENGTH,
    normalize_audit_key,
    validate_audit_details,
)

__all__ = [
    "AuditEventCreate",
    "AuditEventOutcome",
    "AuditEventRecord",
    "AuditRepository",
    "MAX_AUDIT_DETAILS_BYTES",
    "MAX_AUDIT_DETAILS_DEPTH",
    "MAX_AUDIT_STRING_LENGTH",
    "normalize_audit_key",
    "validate_audit_details",
]
