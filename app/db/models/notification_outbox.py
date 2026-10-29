"""SQLAlchemy 2.x mapped model for durable notification outbox commands (Phase DQ-4 Option C).

Implements canonical System of Record persistence for:
- notification_outbox_commands

Invariants:
- All entities strictly tenant-scoped via tenant_id.
- Application-generated canonical UUID primary keys.
- Unique constraint on command_id for local and cross-system idempotency.
- Decouples external E-8 network I/O from domain/consumer transactions.
- State machine: PENDING -> PROCESSING -> DISPATCHED | FAILED | BLOCKED.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import DurableIntegrationBase


class NotificationOutboxCommandRecord(DurableIntegrationBase):
    """Canonical notification command outbox entity mapped to notification_outbox_commands."""

    __tablename__ = "notification_outbox_commands"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_notif_outbox_command_id"),
        UniqueConstraint("tenant_id", "command_id", name="uq_notif_outbox_tenant_command"),
        CheckConstraint("length(trim(command_id)) > 0", name="ck_notif_outbox_cmd_id_not_blank"),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_notif_outbox_tenant_not_blank"),
        CheckConstraint(
            "dispatch_state IN ('PENDING', 'PROCESSING', 'DISPATCHED', 'FAILED', 'BLOCKED')",
            name="ck_notif_outbox_state",
        ),
        CheckConstraint("retry_count >= 0", name="ck_notif_outbox_retry_non_negative"),
        Index("ix_notif_outbox_poll", "dispatch_state", "next_attempt_at"),
        Index("ix_notif_outbox_tenant_state", "tenant_id", "dispatch_state"),
        Index("ix_notif_outbox_command_id", "command_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    command_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    correlation_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    causation_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    trigger_kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    source_event_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    business_condition: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    source_entity_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    recipient_references: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    notification_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    dispatch_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
    )
    block_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

