"""Repository for durable notification outbox commands (Phase DQ-4 Option C).

Enforces:
- Strict tenant-scoped persistence of notification dispatch intents.
- Decoupling of domain event ingestion from remote E-8 notification dispatch.
- Atomic staging within the same transaction as domain mutation / inbox deduplication.
- Concurrency-safe batch claiming using SELECT ... FOR UPDATE SKIP LOCKED.
- Lease management, exponential retry backoff, and fail-closed block states under DEC-E8-02.
- Lease ownership enforcement: only active lease owner can transition PROCESSING records.
- Error sanitization: credential masking and length truncation on failed records.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.db.repositories.base import SqlAlchemyRepository
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)


def sanitize_notification_error(error: str | None) -> str | None:
    """Sanitize error messages before logging or persistence.

    Masks sensitive credentials (bearer tokens, passwords, API keys, secrets)
    and truncates message length to prevent unbounded storage or leakage.
    """
    if not error:
        return None
    text = str(error)[:1000]
    text = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,;&]+",
        r"\1=[REDACTED]",
        text,
    )
    return text


class NotificationOutboxRepository(SqlAlchemyRepository):
    """Repository managing lifecycle and queries for notification_outbox_commands."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session."""
        super().__init__(session)

    @staticmethod
    def to_command(record: NotificationOutboxCommandRecord) -> NotificationCommand:
        """Reconstruct canonical NotificationCommand from a persisted outbox record."""
        return NotificationCommand(
            command_id=record.command_id,
            correlation_reference=record.correlation_reference,
            causation_reference=record.causation_reference,
            organization_scope_reference=record.tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind(record.trigger_kind),
                source_event_name=record.source_event_name,
                business_condition=record.business_condition,
                source_entity_reference=record.source_entity_reference,
            ),
            recipient_references=record.recipient_references,
            notification_context=record.notification_context,
        )

    async def get_by_command_id(
        self,
        tenant_id: str,
        command_id: str,
    ) -> NotificationOutboxCommandRecord | None:
        """Retrieve a notification command by tenant_id and command_id."""
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.tenant_id == tenant_id,
            NotificationOutboxCommandRecord.command_id == command_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def stage_command(
        self,
        *,
        command: NotificationCommand | None = None,
        tenant_id: str | None = None,
        command_id: str | None = None,
        correlation_reference: str | None = None,
        causation_reference: str | None = None,
        trigger_kind: str | None = None,
        source_event_name: str | None = None,
        business_condition: str | None = None,
        source_entity_reference: str | None = None,
        recipient_references: list[str] | None = None,
        notification_context: dict[str, Any] | None = None,
        dispatch_state: str = "PENDING",
        block_reason: str | None = None,
        max_retries: int = 3,
    ) -> NotificationOutboxCommandRecord:
        """Stage a notification command into the outbox within the current transaction.

        Idempotent: If a command with the same command_id already exists, returns the
        existing record without duplicate insertion.
        """
        if command is not None:
            t_id = command.organization_scope_reference
            c_id = command.command_id
            corr = command.correlation_reference
            caus = command.causation_reference
            t_kind = command.trigger.trigger_kind.value
            s_event = command.trigger.source_event_name
            b_cond = command.trigger.business_condition
            s_entity = command.trigger.source_entity_reference
            recipients = command.recipient_references
            ctx = command.notification_context
        else:
            if not tenant_id or not command_id or not correlation_reference or not trigger_kind:
                raise ValueError("Missing required fields to stage notification outbox command")
            t_id = tenant_id
            c_id = command_id
            corr = correlation_reference
            caus = causation_reference
            t_kind = trigger_kind
            s_event = source_event_name
            b_cond = business_condition
            s_entity = source_entity_reference
            recipients = recipient_references or []
            ctx = notification_context or {}

        # Check existing command for idempotency
        existing = await self.get_by_command_id(tenant_id=t_id, command_id=c_id)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        record = NotificationOutboxCommandRecord(
            tenant_id=t_id,
            command_id=c_id,
            correlation_reference=corr,
            causation_reference=caus,
            trigger_kind=t_kind,
            source_event_name=s_event,
            business_condition=b_cond,
            source_entity_reference=s_entity,
            recipient_references=recipients,
            notification_context=ctx,
            dispatch_state=dispatch_state,
            block_reason=block_reason,
            retry_count=0,
            max_retries=max_retries,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def claim_pending_batch(
        self,
        *,
        limit: int = 50,
        lease_duration_seconds: float = 30.0,
        lease_owner: str | None = None,
    ) -> list[NotificationOutboxCommandRecord]:
        """Claim a batch of eligible notification commands using row-level locking.

        Uses SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL to prevent contention.
        """
        now = datetime.now(UTC)
        owner = lease_owner or f"worker-{uuid4().hex[:8]}"
        expires_at = now + timedelta(seconds=lease_duration_seconds)

        conditions = and_(
            or_(
                NotificationOutboxCommandRecord.dispatch_state == "PENDING",
                and_(
                    NotificationOutboxCommandRecord.dispatch_state == "PROCESSING",
                    NotificationOutboxCommandRecord.lease_expires_at <= now,
                ),
            ),
            NotificationOutboxCommandRecord.next_attempt_at <= now,
        )

        stmt = (
            select(NotificationOutboxCommandRecord)
            .where(conditions)
            .order_by(
                NotificationOutboxCommandRecord.next_attempt_at.asc(),
                NotificationOutboxCommandRecord.created_at.asc(),
            )
            .limit(limit)
        )

        # Apply FOR UPDATE SKIP LOCKED on PostgreSQL
        bind = getattr(self.session, "bind", None)
        dialect_name = getattr(bind.dialect, "name", "") if bind is not None else ""
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await self.session.execute(stmt)
        records = list(result.scalars().all())

        for record in records:
            record.dispatch_state = "PROCESSING"
            record.lease_owner = owner
            record.lease_expires_at = expires_at
            record.updated_at = now

        await self.session.flush()
        return records

    async def mark_dispatched(
        self,
        command_id: str,
        *,
        lease_owner: str | None = None,
        tenant_id: str | None = None,
        enforce_lease_expiration: bool = True,
    ) -> bool:
        """Mark a notification command as successfully dispatched.

        Requires that the record is in 'PROCESSING' state, owned by lease_owner,
        and matches tenant_id. Missing lease_owner or tenant_id fails closed (returns False).
        When enforce_lease_expiration is True (default), completion rights require an
        active (unexpired) lease (lease_expires_at > now). An expired lease invalidates
        completion rights to eliminate split-brain races with background re-claiming workers.
        Returns True if the record was updated, False if lease was lost or expired, state changed,
        or record was already completed by another worker.
        """
        if not lease_owner or not lease_owner.strip() or not tenant_id or not tenant_id.strip():
            return False

        now = datetime.now(UTC)
        conditions = [
            NotificationOutboxCommandRecord.command_id == command_id,
            NotificationOutboxCommandRecord.tenant_id == tenant_id.strip(),
            NotificationOutboxCommandRecord.lease_owner == lease_owner.strip(),
            NotificationOutboxCommandRecord.dispatch_state == "PROCESSING",
        ]
        if enforce_lease_expiration:
            conditions.append(NotificationOutboxCommandRecord.lease_expires_at > now)

        stmt = select(NotificationOutboxCommandRecord).where(and_(*conditions))
        bind = getattr(self.session, "bind", None) or getattr(
            getattr(self.session, "sync_session", None), "bind", None
        )
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update()

        result = await self.session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            return False

        record.dispatch_state = "DISPATCHED"
        record.dispatched_at = now
        record.lease_owner = None
        record.lease_expires_at = None
        record.updated_at = now
        await self.session.flush()
        return True

    async def mark_failed(
        self,
        command_id: str,
        error: str = "",
        *,
        lease_owner: str | None = None,
        retry_backoff_seconds: float = 1.0,
        tenant_id: str | None = None,
        enforce_lease_expiration: bool = True,
    ) -> bool:
        """Record dispatch failure, increment retry count, and calculate backoff.

        Requires that the record is in 'PROCESSING' state, owned by lease_owner,
        and matches tenant_id. Missing lease_owner or tenant_id fails closed (returns False).
        When enforce_lease_expiration is True (default), failure transitions require an
        active (unexpired) lease (lease_expires_at > now). An expired lease invalidates
        transition rights to eliminate split-brain races with background re-claiming workers.
        Sanitizes error text before persisting.
        Returns True if the record was updated, False if lease was lost or expired, state changed,
        or record was already completed by another worker.
        """
        if not lease_owner or not lease_owner.strip() or not tenant_id or not tenant_id.strip():
            return False

        now = datetime.now(UTC)
        conditions = [
            NotificationOutboxCommandRecord.command_id == command_id,
            NotificationOutboxCommandRecord.tenant_id == tenant_id.strip(),
            NotificationOutboxCommandRecord.lease_owner == lease_owner.strip(),
            NotificationOutboxCommandRecord.dispatch_state == "PROCESSING",
        ]
        if enforce_lease_expiration:
            conditions.append(NotificationOutboxCommandRecord.lease_expires_at > now)

        stmt = select(NotificationOutboxCommandRecord).where(and_(*conditions))
        bind = getattr(self.session, "bind", None) or getattr(
            getattr(self.session, "sync_session", None), "bind", None
        )
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update()

        result = await self.session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            return False

        sanitized_error = sanitize_notification_error(error)
        record.retry_count += 1
        record.last_error = sanitized_error
        record.lease_owner = None
        record.lease_expires_at = None
        record.updated_at = now

        if record.retry_count >= record.max_retries:
            record.dispatch_state = "FAILED"
        else:
            record.dispatch_state = "PENDING"
            backoff = retry_backoff_seconds * (2 ** (record.retry_count - 1))
            record.next_attempt_at = now + timedelta(seconds=backoff)

        await self.session.flush()
        return True

    async def mark_blocked(
        self,
        command_id: str,
        block_reason: str,
        *,
        tenant_id: str | None = None,
    ) -> bool:
        """Mark a notification command as blocked under DEC-E8-02."""
        conditions = [NotificationOutboxCommandRecord.command_id == command_id]
        if tenant_id is not None:
            conditions.append(NotificationOutboxCommandRecord.tenant_id == tenant_id)

        stmt = select(NotificationOutboxCommandRecord).where(and_(*conditions))
        result = await self.session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            return False

        now = datetime.now(UTC)
        record.dispatch_state = "BLOCKED"
        record.block_reason = block_reason
        record.lease_owner = None
        record.lease_expires_at = None
        record.updated_at = now
        await self.session.flush()
        return True

    async def recover_blocked_command(
        self,
        tenant_id: str,
        command_id: str,
        resolved_recipients: list[str],
    ) -> NotificationOutboxCommandRecord | None:
        """Unblock a blocked notification intent with authorized recipient references."""
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.tenant_id == tenant_id,
            NotificationOutboxCommandRecord.command_id == command_id,
            NotificationOutboxCommandRecord.dispatch_state == "BLOCKED",
        )
        result = await self.session.execute(stmt)
        record = result.scalars().first()
        if record is None:
            return None

        now = datetime.now(UTC)
        record.recipient_references = list(resolved_recipients)
        record.dispatch_state = "PENDING"
        record.block_reason = None
        record.next_attempt_at = now
        record.updated_at = now
        await self.session.flush()
        return record

    async def list_by_state(
        self,
        tenant_id: str,
        dispatch_state: str | None = None,
        limit: int = 100,
    ) -> list[NotificationOutboxCommandRecord]:
        """List notification commands for a tenant, optionally filtered by dispatch_state."""
        conditions = [NotificationOutboxCommandRecord.tenant_id == tenant_id]
        if dispatch_state is not None:
            conditions.append(NotificationOutboxCommandRecord.dispatch_state == dispatch_state)

        stmt = (
            select(NotificationOutboxCommandRecord)
            .where(*conditions)
            .order_by(NotificationOutboxCommandRecord.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_tenant(
        self,
        tenant_id: str,
        limit: int = 100,
    ) -> list[NotificationOutboxCommandRecord]:
        """List all notification outbox records for a given tenant."""
        return await self.list_by_state(tenant_id=tenant_id, limit=limit)


__all__ = ["NotificationOutboxRepository", "sanitize_notification_error"]
