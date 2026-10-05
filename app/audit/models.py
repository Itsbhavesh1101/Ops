"""SQLAlchemy 2.x mapped model for canonical append-only audit events.

Part of Phase 1 Foundation Task 1F.
Maps to canonical PostgreSQL audit_events table with UUID primary key, timezone-aware
recorded_at timestamp, JSONB details, and deterministic constraints/indexes.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEventRecord(Base):
    """Canonical append-only audit event entity mapped to PostgreSQL audit_events.

    Invariants:
    - Immutable entity: no application mutation methods (update, delete) are defined.
    - Application-generated UUID primary key (never dependent on pgcrypto/uuid-ossp).
    - Database-authoritative recorded_at with server default func.now().
    - PostgreSQL JSONB details column with strict application validation.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "schema_version > 0",
            name="schema_version_positive",
        ),
        CheckConstraint(
            "length(trim(event_type)) > 0",
            name="event_type_not_blank",
        ),
        CheckConstraint(
            "(actor_principal_id IS NULL "
            "AND actor_principal_type IS NULL "
            "AND authentication_method IS NULL) "
            "OR ("
            "actor_principal_id IS NOT NULL "
            "AND tenant_id IS NOT NULL "
            "AND actor_principal_type IS NOT NULL "
            "AND authentication_method IS NOT NULL)",
            name="actor_consistency",
        ),
        CheckConstraint(
            "actor_principal_type IS NULL OR actor_principal_type IN ('HUMAN', 'SERVICE')",
            name="actor_principal_type_valid",
        ),
        CheckConstraint(
            "authentication_method IS NULL OR authentication_method IN ('OIDC', 'SERVICE_API_KEY')",
            name="authentication_method_valid",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('SUCCESS', 'FAILURE', 'DENIED')",
            name="outcome_valid",
        ),
        Index("ix_audit_events_tenant_id_recorded_at", "tenant_id", "recorded_at"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
        Index(
            "ix_audit_events_resource_type_resource_id_recorded_at",
            "resource_type",
            "resource_id",
            "recorded_at",
        ),
    )

    audit_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    actor_principal_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    actor_principal_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    authentication_method: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    resource_type: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    outcome: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
