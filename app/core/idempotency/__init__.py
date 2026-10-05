"""app.core.idempotency package — Canonical contracts, persistence, and execution orchestration.

Part of Phase 1 Foundation Tasks 1H.1, 1H.2, & 1H.3.
"""

from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    IdempotencyKeyDigest,
    OperationName,
    RequestFingerprint,
    create_idempotency_identity,
    validate_hex_digest,
    validate_operation_name,
)
from app.core.idempotency.executor import (
    IdempotencyConflictError,
    IdempotentExecutionOutcome,
    IdempotentExecutionResult,
    IdempotentMutationExecutor,
    IdempotentMutationHandler,
)
from app.core.idempotency.hashing import (
    MAX_RAW_KEY_LENGTH,
    digest_idempotency_key,
    fingerprint_bytes,
    validate_raw_idempotency_key,
)
from app.core.idempotency.models import IdempotencyRecord
from app.core.idempotency.repository import (
    IdempotencyDecision,
    IdempotencyDecisionType,
    IdempotencyRepository,
)

__all__ = [
    "IdempotencyConflictError",
    "IdempotencyDecision",
    "IdempotencyDecisionType",
    "IdempotencyIdentity",
    "IdempotencyKeyDigest",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "IdempotentExecutionOutcome",
    "IdempotentExecutionResult",
    "IdempotentMutationExecutor",
    "IdempotentMutationHandler",
    "MAX_RAW_KEY_LENGTH",
    "OperationName",
    "RequestFingerprint",
    "create_idempotency_identity",
    "digest_idempotency_key",
    "fingerprint_bytes",
    "validate_hex_digest",
    "validate_operation_name",
    "validate_raw_idempotency_key",
]
