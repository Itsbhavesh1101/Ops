"""Repository for transactional idempotency marker persistence and conflict detection.

Part of Phase 1 Foundation Task 1H.2.
Adheres strictly to the repository and Unit of Work foundation:
- Externally supplied AsyncSession from Unit of Work.
- Shared transaction: executes within the caller's active business transaction.
- Atomic acquisition: PostgreSQL INSERT ... ON CONFLICT (tenant_id, operation_name, key_digest)
  DO NOTHING RETURNING.
- Zero SELECT-before-INSERT to prevent concurrency races.
- Post-conflict observation: queries existing committed record only after insertion conflict.
- Provisional ACQUIRED decision: marker is durable only when the surrounding UnitOfWork commits.
  If business processing rolls back, the marker rolls back with it.
- EXISTING_MATCH decision: observed committed marker with matching request fingerprint.
- EXISTING_CONFLICT decision: observed committed marker with differing request fingerprint.
- Never commits, rolls back, or closes the session.
- Zero HTTP framework, header extraction, response replay, or distributed locking machinery.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    RequestFingerprint,
)
from app.core.idempotency.models import IdempotencyRecord
from app.db.repositories.base import SqlAlchemyRepository


class IdempotencyDecisionType(StrEnum):
    """Provider-neutral idempotency acquisition decision types.

    Invariants:
    - ACQUIRED: This transaction provisionally inserted the idempotency marker.
    - EXISTING_MATCH: A previously committed marker exists with the identical request fingerprint.
    - EXISTING_CONFLICT: A previously committed marker exists with a different request fingerprint.
    """

    ACQUIRED = "ACQUIRED"
    EXISTING_MATCH = "EXISTING_MATCH"
    EXISTING_CONFLICT = "EXISTING_CONFLICT"


class IdempotencyDecision(BaseModel):
    """Immutable provider-neutral decision result for idempotency acquisition.

    Invariants:
    - ACQUIRED: existing_fingerprint is None.
    - EXISTING_MATCH: existing_fingerprint is not None and equals request_fingerprint.
    - EXISTING_CONFLICT: existing_fingerprint is not None and differs from request_fingerprint.
    - Privacy: contains no raw client key, no HTTP status, and no response payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: IdempotencyDecisionType
    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint
    existing_fingerprint: RequestFingerprint | None = None

    @model_validator(mode="after")
    def _validate_invariants(self) -> "IdempotencyDecision":
        if self.decision == IdempotencyDecisionType.ACQUIRED:
            if self.existing_fingerprint is not None:
                raise ValueError("ACQUIRED decision must not have existing_fingerprint")
        elif self.decision == IdempotencyDecisionType.EXISTING_MATCH:
            if self.existing_fingerprint is None:
                raise ValueError("EXISTING_MATCH decision requires existing_fingerprint")
            if self.existing_fingerprint != self.request_fingerprint:
                raise ValueError(
                    "EXISTING_MATCH decision requires existing_fingerprint == request_fingerprint"
                )
        elif self.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
            if self.existing_fingerprint is None:
                raise ValueError("EXISTING_CONFLICT decision requires existing_fingerprint")
            if self.existing_fingerprint == self.request_fingerprint:
                raise ValueError(
                    "EXISTING_CONFLICT decision requires "
                    "existing_fingerprint != request_fingerprint"
                )
        return self


class IdempotencyRepository(SqlAlchemyRepository):
    """Repository managing transactional idempotency marker persistence and conflict detection."""

    async def acquire_or_observe(
        self,
        *,
        identity: IdempotencyIdentity,
        request_fingerprint: RequestFingerprint,
    ) -> IdempotencyDecision:
        """Attempt to provisionally acquire an idempotency marker or observe an existing record.

        Algorithm:
        1. Attempt INSERT ... ON CONFLICT (tenant_id, operation_name, key_digest) DO NOTHING
           RETURNING key_digest.
        2. If a row is returned, return ACQUIRED. This transaction owns the provisional marker.
           No SELECT is performed.
        3. If no row is returned (conflict detected), execute a targeted SELECT for the existing
           committed request_fingerprint.
        4. Compare existing fingerprint against requested fingerprint:
           - Matching: return EXISTING_MATCH.
           - Differing: return EXISTING_CONFLICT.

        Transaction & Rollback Semantics:
        - ACQUIRED is PROVISIONAL until the caller-owned UnitOfWork commits.
        - If downstream business mutation fails and the UnitOfWork rolls back, this marker
          rolls back atomically. A future retry may acquire the identity again.
        - The repository never calls commit, rollback, or close.

        Args:
            identity: Validated, immutable canonical IdempotencyIdentity.
            request_fingerprint: Validated, immutable canonical RequestFingerprint.

        Returns:
            IdempotencyDecision: The acquisition decision and associated fingerprint context.

        Raises:
            TypeError: If identity is not an IdempotencyIdentity or request_fingerprint is not
                       a RequestFingerprint.
            RuntimeError: If conflict occurred but existing record could not be read.
        """
        if not isinstance(identity, IdempotencyIdentity):
            raise TypeError(
                f"identity must be an IdempotencyIdentity instance, got '{type(identity).__name__}'"
            )
        if not isinstance(request_fingerprint, RequestFingerprint):
            raise TypeError(
                f"request_fingerprint must be a RequestFingerprint instance, "
                f"got '{type(request_fingerprint).__name__}'"
            )

        # 1. Attempt INSERT FIRST with ON CONFLICT DO NOTHING
        insert_stmt = (
            pg_insert(IdempotencyRecord)
            .values(
                tenant_id=identity.tenant_id,
                operation_name=identity.operation_name,
                key_digest=str(identity.key_digest),
                request_fingerprint=str(request_fingerprint),
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "operation_name", "key_digest"],
            )
            .returning(IdempotencyRecord.key_digest)
        )

        result = await self._session.execute(insert_stmt)
        inserted_key = result.scalar_one_or_none()

        # If inserted, this transaction provisionally acquired the identity
        if inserted_key is not None:
            return IdempotencyDecision(
                decision=IdempotencyDecisionType.ACQUIRED,
                identity=identity,
                request_fingerprint=request_fingerprint,
                existing_fingerprint=None,
            )

        # 2. Conflict occurred: Observe existing committed record by exact primary key
        select_stmt = select(IdempotencyRecord.request_fingerprint).where(
            IdempotencyRecord.tenant_id == identity.tenant_id,
            IdempotencyRecord.operation_name == identity.operation_name,
            IdempotencyRecord.key_digest == str(identity.key_digest),
        )
        select_result = await self._session.execute(select_stmt)
        existing_fp_str = select_result.scalar_one_or_none()

        if existing_fp_str is None:
            raise RuntimeError(
                f"Idempotency record for operation '{identity.operation_name}' and digest "
                f"'{identity.key_digest}' encountered conflict but existing record could not "
                "be read."
            )

        existing_fingerprint = RequestFingerprint(existing_fp_str)
        if existing_fingerprint == request_fingerprint:
            return IdempotencyDecision(
                decision=IdempotencyDecisionType.EXISTING_MATCH,
                identity=identity,
                request_fingerprint=request_fingerprint,
                existing_fingerprint=existing_fingerprint,
            )

        return IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=request_fingerprint,
            existing_fingerprint=existing_fingerprint,
        )
