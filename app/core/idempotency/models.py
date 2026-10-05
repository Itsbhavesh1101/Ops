"""SQLAlchemy 2.x mapped model for canonical transactional idempotency records.

Part of Phase 1 Foundation Task 1H.2.
Maps to canonical PostgreSQL idempotency_records table with composite primary key
(tenant_id, operation_name, key_digest), request_fingerprint, server-authored
created_at timestamp, and database check constraints enforcing canonical formats.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyRecord(Base):
    """Canonical transactional idempotency entity mapped to idempotency_records.

    Invariants:
    - Primary key is composite: (tenant_id, operation_name, key_digest).
    - Zero surrogate idempotency_id / record_id column.
    - Zero raw idempotency key or client token column (stores only 64-char key_digest).
    - Zero principal ID, user ID, role, or authorization column.
    - Zero status or state machine column.
    - Zero lease, claim, or lock columns.
    - Zero response replay or HTTP status columns.
    - Zero retention or TTL columns.
    - created_at is server-authored TIMESTAMPTZ (server_default func.now()).
    - Database check constraints enforce canonical formats for operation_name, key_digest,
      request_fingerprint, and non-empty tenant_id.
    - Zero speculative secondary indexes.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(
            r"operation_name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="operation_name_format",
        ),
        CheckConstraint(
            r"key_digest ~ '^[0-9a-f]{64}$'",
            name="key_digest_format",
        ),
        CheckConstraint(
            r"request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="request_fingerprint_format",
        ),
        CheckConstraint(
            r"length(trim(tenant_id)) > 0",
            name="tenant_id_non_empty",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        nullable=False,
    )
    operation_name: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        nullable=False,
    )
    key_digest: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
