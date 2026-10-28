"""create_notification_outbox

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-28 00:00:00.000000

Phase DQ-4 Option C Two-Stage Notification Outbox Migration.
Creates:
- notification_outbox_commands: Durable, tenant-scoped storage for notification commands staged
  by event consumers and dispatched asynchronously by an independent delivery worker to E-8.

Invariants:
- All commands strictly tenant-scoped (tenant_id).
- Application-generated canonical UUIDv4 primary keys.
- Deterministic, unique command_id for local and cross-system idempotency.
- Decouples external E-8 network I/O from consumer database transactions.
- Check constraints enforce non-empty identifier, tenant_id, and valid dispatch states.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create notification_outbox_commands table and associated indexes/constraints."""
    op.create_table(
        "notification_outbox_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("command_id", sa.String(length=255), nullable=False),
        sa.Column("correlation_reference", sa.String(length=255), nullable=False),
        sa.Column("causation_reference", sa.String(length=255), nullable=True),
        sa.Column("trigger_kind", sa.String(length=64), nullable=False),
        sa.Column("source_event_name", sa.String(length=128), nullable=True),
        sa.Column("source_entity_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "recipient_references",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "notification_context",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "dispatch_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("block_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_retries",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "command_id",
            name="uq_notif_outbox_command_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "command_id",
            name="uq_notif_outbox_tenant_command",
        ),
        sa.CheckConstraint(
            "length(trim(command_id)) > 0",
            name="ck_notif_outbox_cmd_id_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_notif_outbox_tenant_not_blank",
        ),
        sa.CheckConstraint(
            "dispatch_state IN ('PENDING', 'PROCESSING', 'DISPATCHED', 'FAILED', 'BLOCKED')",
            name="ck_notif_outbox_state",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_notif_outbox_retry_non_negative",
        ),
    )

    op.create_index(
        "ix_notif_outbox_poll",
        "notification_outbox_commands",
        ["dispatch_state", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_notif_outbox_tenant_state",
        "notification_outbox_commands",
        ["tenant_id", "dispatch_state"],
        unique=False,
    )
    op.create_index(
        "ix_notif_outbox_command_id",
        "notification_outbox_commands",
        ["command_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop notification_outbox_commands table and associated indexes."""
    op.drop_index(
        "ix_notif_outbox_command_id",
        table_name="notification_outbox_commands",
    )
    op.drop_index(
        "ix_notif_outbox_tenant_state",
        table_name="notification_outbox_commands",
    )
    op.drop_index(
        "ix_notif_outbox_poll",
        table_name="notification_outbox_commands",
    )
    op.drop_table("notification_outbox_commands")

