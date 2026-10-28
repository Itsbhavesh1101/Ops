"""SQLAlchemy 2.x mapped models for canonical SLA policies (Phase 3B).

Implements canonical System of Record persistence for:
- sla_policies (versioned SLA definition and threshold rules)

Invariants:
- All entities are tenant-scoped via tenant_id.
- Application-generated canonical UUID primary keys.
- SLA calculations are business-calendar aware with explicit timezone-aware datetimes.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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

from app.db.base import Base, DurableIntegrationBase


class SlaPolicyRecord(Base):
    """Canonical SLA policy entity mapped to PostgreSQL sla_policies."""

    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "policy_key", "version", name="uq_sla_policy_tenant_key_version"
        ),
        CheckConstraint("version > 0", name="ck_sla_policy_version_positive"),
        CheckConstraint(
            "target_duration_seconds > 0", name="ck_sla_policy_target_duration_positive"
        ),
        CheckConstraint(
            "warning_threshold_seconds >= 0", name="ck_sla_policy_warning_threshold_non_negative"
        ),
        CheckConstraint(
            "warning_threshold_seconds <= target_duration_seconds",
            name="ck_sla_policy_warning_lte_target",
        ),
        Index("ix_sla_policies_tenant_key", "tenant_id", "policy_key"),
        Index("ix_sla_policies_tenant_active", "tenant_id", "is_active"),
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
    policy_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    calendar_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="DEFAULT_BUSINESS",
    )
    target_duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    warning_threshold_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    escalation_action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    escalation_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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


class TaskSlaStateRecord(DurableIntegrationBase):
    """Canonical task SLA entity mapped to PostgreSQL task_sla_states (AD-DQ4-02)."""

    __tablename__ = "task_sla_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_id", name="uq_task_sla_tenant_task"),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_task_sla_tenant_not_blank"),
        CheckConstraint(
            "status IN ('RUNNING', 'WARNING', 'BREACHED', 'PAUSED', 'COMPLETED')",
            name="ck_task_sla_status",
        ),
        CheckConstraint(
            "total_paused_seconds >= 0",
            name="ck_task_sla_total_paused_non_negative",
        ),
        Index("ix_task_sla_eval", "tenant_id", "status", "warning_at", "breach_at"),
        Index("ix_task_sla_task_id", "task_id"),
        Index("ix_task_sla_tenant_status", "tenant_id", "status"),
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
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    instance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    policy_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="RUNNING",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    warning_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    breach_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    total_paused_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    remaining_warning_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    remaining_breach_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    warning_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    breach_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

