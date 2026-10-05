"""create_inbox_messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28 00:00:00.000000

Part of Phase 1 Foundation Task 1G.4A.
Creates canonical transactional inbox_messages table with composite primary key
(consumer_name, event_id), server-authored recorded_at timestamp, and check
constraint enforcing canonical namespaced consumer naming format.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create inbox_messages table with composite primary key and check constraint."""
    op.create_table(
        "inbox_messages",
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("consumer_name", "event_id", name="pk_inbox_messages"),
        sa.CheckConstraint(
            r"consumer_name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="ck_inbox_messages_consumer_name_format",
        ),
    )


def downgrade() -> None:
    """Drop inbox_messages table safely."""
    op.drop_table("inbox_messages")
