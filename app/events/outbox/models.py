"""SQLAlchemy 2.x mapped model for canonical transactional outbox events.

Part of Phase 1 Foundation Task 1G.2 and Task 1G.3B1.
Maps to canonical PostgreSQL outbox_events table with UUID primary key copied from
EventEnvelope.event_id, complete canonical envelope stored in JSONB, database-authored
created_at timestamp, nullable published_at completion marker, and dispatch operational state
(dispatch_attempt_count, next_attempt_at, claim_token, claimed_at).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEventRecord(Base):
    """Canonical transactional outbox entity mapped to PostgreSQL outbox_events.

    Invariants:
    - Primary key is the canonical event_id (UUID) copied from EventEnvelope.event_id.
    - Zero redundant outbox_id.
    - Stores the complete canonical EventEnvelope in PostgreSQL JSONB.
    - created_at is server-authored TIMESTAMPTZ (server_default func.now()).
    - published_at is nullable TIMESTAMPTZ (NULL means pending publication).
    - Database consistency: COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)
      and typeof(envelope) = 'object'.
    - dispatch_attempt_count is INTEGER NOT NULL >= 0 (server_default 0).
    - next_attempt_at is TIMESTAMPTZ NOT NULL (server_default func.now()).
    - claim_token and claimed_at form a strictly paired lease (both NULL or both NOT NULL).
    - Published event cannot retain an active claim.
    - Partial index ix_outbox_events_dispatch_ready on (next_attempt_at, created_at, event_id)
      WHERE published_at IS NULL for efficient concurrent dispatcher polling.
    - Immutable event content (event_id, envelope, created_at) protected from normal UPDATE
      by database trigger; operational columns remain updateable.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)",
            name="event_id_matches_envelope",
        ),
        CheckConstraint(
            "jsonb_typeof(envelope) = 'object'",
            name="envelope_is_object",
        ),
        CheckConstraint(
            "dispatch_attempt_count >= 0",
            name="dispatch_attempt_count_non_negative",
        ),
        CheckConstraint(
            "(claim_token IS NULL AND claimed_at IS NULL) OR "
            "(claim_token IS NOT NULL AND claimed_at IS NOT NULL)",
            name="claim_token_and_claimed_at_pair",
        ),
        CheckConstraint(
            "published_at IS NULL OR (claim_token IS NULL AND claimed_at IS NULL)",
            name="published_has_no_active_claim",
        ),
        Index(
            "ix_outbox_events_dispatch_ready",
            "next_attempt_at",
            "created_at",
            "event_id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
    )
    envelope: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    dispatch_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        default=0,
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claim_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        default=None,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
