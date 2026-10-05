"""SQLAlchemy 2.x mapped model for canonical transactional inbox messages.

Part of Phase 1 Foundation Task 1G.4A.
Maps to canonical PostgreSQL inbox_messages table with composite primary key
(consumer_name, event_id), server-authored recorded_at timestamp, and database
check constraint enforcing canonical namespaced consumer naming conventions.
Provides privacy-minimizing deduplication persistence for at-least-once consumer delivery.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InboxMessageRecord(Base):
    """Canonical transactional inbox entity mapped to PostgreSQL inbox_messages.

    Invariants:
    - Primary key is composite: (consumer_name, event_id).
    - Zero surrogate inbox_id column.
    - consumer_name is non-null VARCHAR(128) adhering to canonical namespaced format.
    - event_id is non-null UUID matching canonical EventEnvelope.event_id.
    - recorded_at is server-authored TIMESTAMPTZ (server_default func.now()).
    - Privacy-minimizing: zero complete envelope JSONB, zero payload, zero tenant_id.
    - Deduplication only: zero status machine enum, zero raw error/failure columns.
    - Zero speculative extra indexes (composite primary key supports lookup).
    """

    __tablename__ = "inbox_messages"
    __table_args__ = (
        CheckConstraint(
            r"consumer_name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="consumer_name_format",
        ),
    )

    consumer_name: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
