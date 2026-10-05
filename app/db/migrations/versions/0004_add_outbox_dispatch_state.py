"""add_outbox_dispatch_state

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26 01:00:00.000000

Part of Phase 1 Foundation Task 1G.3B1.
Adds operational dispatch columns to outbox_events:
- dispatch_attempt_count (INTEGER NOT NULL server_default 0)
- next_attempt_at (TIMESTAMPTZ NOT NULL server_default now())
- claim_token (UUID nullable)
- claimed_at (TIMESTAMPTZ nullable)

Adds named check constraints:
- ck_outbox_events_dispatch_attempt_count_non_negative
- ck_outbox_events_claim_token_and_claimed_at_pair
- ck_outbox_events_published_has_no_active_claim

Replaces ix_outbox_events_pending_created_at with dispatcher-ready partial index:
- ix_outbox_events_dispatch_ready ON (next_attempt_at, created_at, event_id)
  WHERE published_at IS NULL
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dispatch-state columns, check constraints, and replace pending index."""
    # 1. Add operational dispatch columns with server defaults for existing-row safety
    op.add_column(
        "outbox_events",
        sa.Column(
            "dispatch_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "claim_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 2. Add named check constraints
    op.create_check_constraint(
        "ck_outbox_events_dispatch_attempt_count_non_negative",
        "outbox_events",
        "dispatch_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_outbox_events_claim_token_and_claimed_at_pair",
        "outbox_events",
        "(claim_token IS NULL AND claimed_at IS NULL) OR "
        "(claim_token IS NOT NULL AND claimed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_outbox_events_published_has_no_active_claim",
        "outbox_events",
        "published_at IS NULL OR (claim_token IS NULL AND claimed_at IS NULL)",
    )

    # 3. Replace pending index with dispatch-ready index
    op.drop_index(
        "ix_outbox_events_pending_created_at",
        table_name="outbox_events",
    )
    op.create_index(
        "ix_outbox_events_dispatch_ready",
        "outbox_events",
        ["next_attempt_at", "created_at", "event_id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    """Drop dispatch-ready index, restore pending index, drop constraints and columns."""
    # 1. Restore original pending index
    op.drop_index(
        "ix_outbox_events_dispatch_ready",
        table_name="outbox_events",
    )
    op.create_index(
        "ix_outbox_events_pending_created_at",
        "outbox_events",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )

    # 2. Drop named check constraints
    op.drop_constraint(
        "ck_outbox_events_published_has_no_active_claim",
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbox_events_claim_token_and_claimed_at_pair",
        "outbox_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbox_events_dispatch_attempt_count_non_negative",
        "outbox_events",
        type_="check",
    )

    # 3. Drop operational dispatch columns
    op.drop_column("outbox_events", "claimed_at")
    op.drop_column("outbox_events", "claim_token")
    op.drop_column("outbox_events", "next_attempt_at")
    op.drop_column("outbox_events", "dispatch_attempt_count")
