"""create_outbox_events

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 00:00:00.000000

Part of Phase 1 Foundation Task 1G.2.
Creates canonical transactional outbox_events table with UUID primary key copied
from EventEnvelope.event_id, PostgreSQL JSONB envelope column, server-authored
created_at timestamp, nullable published_at completion marker, partial index for
pending event scans, and database-level trigger protecting immutable event content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create outbox_events table, constraints, partial pending index, and update trigger."""
    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "envelope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_events"),
        sa.CheckConstraint(
            "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)",
            name="ck_outbox_events_event_id_matches_envelope",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(envelope) = 'object'",
            name="ck_outbox_events_envelope_is_object",
        ),
    )

    op.create_index(
        "ix_outbox_events_pending_created_at",
        "outbox_events",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )

    # Database-level trigger function: protect immutable event content
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_protect_outbox_event_content()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.event_id IS DISTINCT FROM OLD.event_id OR
               NEW.envelope IS DISTINCT FROM OLD.envelope OR
               NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'outbox_events immutable content cannot be updated'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Row-level trigger before UPDATE on outbox_events
    op.execute(
        """
        CREATE TRIGGER trg_outbox_events_protect_content
        BEFORE UPDATE ON outbox_events
        FOR EACH ROW
        EXECUTE FUNCTION fn_protect_outbox_event_content();
        """
    )


def downgrade() -> None:
    """Drop update protection trigger, function, partial index, and outbox_events table."""
    op.execute("DROP TRIGGER IF EXISTS trg_outbox_events_protect_content ON outbox_events;")
    op.execute("DROP FUNCTION IF EXISTS fn_protect_outbox_event_content();")
    op.drop_index(
        "ix_outbox_events_pending_created_at",
        table_name="outbox_events",
    )
    op.drop_table("outbox_events")
