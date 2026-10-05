"""Repository implementation for appending canonical E-7 audit records.

Part of Phase 1 Foundation Task 1F.
Adheres strictly to the repository foundation:
- Externally supplied AsyncSession from Unit of Work.
- No transaction ownership: never commits, rolls back, or closes session.
- Append-only: exposes ONLY append(). Exposes no update or delete methods.
- Flush policy: does NOT flush by default; relies on Unit of Work transaction demarcation.
"""

from uuid import uuid4

from app.audit.models import AuditEventRecord
from app.audit.schemas import AuditEventCreate
from app.db.repositories.base import SqlAlchemyRepository


class AuditRepository(SqlAlchemyRepository):
    """Domain-specific repository for canonical append-only audit events."""

    async def append(self, event: AuditEventCreate) -> AuditEventRecord:
        """Append an immutable audit event to the active Unit of Work session.

        Instantiates AuditEventRecord and stages it with session.add().
        Does not commit, rollback, or close the session.

        Flush policy:
            Does NOT call session.flush() by default to avoid roundtrips.
            The audit record will be flushed/committed at the Unit of Work transaction boundary.

        Args:
            event: Validated, immutable AuditEventCreate input contract.

        Returns:
            The staged AuditEventRecord attached to the session.
        """
        outcome_val = str(event.outcome) if event.outcome is not None else None

        record = AuditEventRecord(
            audit_event_id=uuid4(),
            schema_version=event.schema_version,
            tenant_id=event.tenant_id,
            actor_principal_id=event.actor_principal_id,
            actor_principal_type=event.actor_principal_type,
            authentication_method=event.authentication_method,
            event_type=event.event_type,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=outcome_val,
            correlation_id=event.correlation_id,
            details=event.details,
        )
        self.session.add(record)
        return record
