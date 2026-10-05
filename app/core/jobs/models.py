"""SQLAlchemy 2.x mapped model for canonical asynchronous jobs.

Part of Phase 1 Foundation Task 1I.4B.
Maps to canonical PostgreSQL jobs table with UUID primary key (job_id),
tenant scoping (tenant_id), namespaced job type (job_type), status,
sanitized failure fields, server-authored timestamps, and database check
constraints enforcing canonical lifecycle invariants and naming formats.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobModel(Base):
    """Canonical asynchronous job entity mapped to the jobs table.

    Invariants:
    - Primary key is job_id (PostgreSQL UUIDv4).
    - Zero surrogate integer ID or second UUID.
    - tenant_id and job_type are immutable after creation.
    - status is a VARCHAR(32) constrained to ('pending', 'running', 'succeeded', 'failed').
    - Zero PostgreSQL enum type.
    - failure_code and failure_message are nullable, populated only when status == 'failed'.
    - created_at is server-authored TIMESTAMPTZ (DEFAULT now()).
    - started_at and completed_at are nullable TIMESTAMPTZ governed by lifecycle invariants:
      * pending: started_at IS NULL, completed_at IS NULL
      * running: started_at IS NOT NULL, completed_at IS NULL
      * succeeded: started_at IS NOT NULL, completed_at IS NOT NULL
      * failed: completed_at IS NOT NULL (started_at optional for pre-execution failures)
    - Timestamp ordering: started_at >= created_at, completed_at >= created_at,
      completed_at >= started_at (when started_at is present).
    - Zero result payloads, URLs, progress percentages, retry counters, or worker leases.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="status",
        ),
        CheckConstraint(
            r"job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="job_type",
        ),
        CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="tenant_id_nonblank",
        ),
        CheckConstraint(
            r"failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="failure_code_format",
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL AND failure_message IS NOT NULL) OR "
            "(status != 'failed' AND failure_code IS NULL AND failure_message IS NULL)",
            name="failure_consistency",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL)",
            name="timestamp_state_consistency",
        ),
        CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) AND "
            "(completed_at IS NULL OR completed_at >= created_at) AND "
            "(started_at IS NULL OR completed_at IS NULL OR completed_at >= started_at)",
            name="timestamp_ordering",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    failure_message: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
