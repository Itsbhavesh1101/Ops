"""calendar_watch_channels

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-08 00:00:00.000000

Phase 7C Durable Google Calendar Push Watch Channel State Migration.
Creates:
- calendar_watch_channels: Durable lifecycle tracking table for external
  calendar push notification channels (watch channels).

Invariants:
- All watch channels are tenant-scoped (tenant_id).
- Application-generated UUIDv4 primary keys.
- Unique constraint on channel_id for fast lookup from X-Goog-Channel-ID.
- Token hash stored (SHA-256) rather than plaintext secrets.
- Check constraints enforce valid channel status ('ACTIVE', 'STOPPED', 'EXPIRED').
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create calendar_watch_channels table and associated indexes/constraints."""
    op.create_table(
        "calendar_watch_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'google'"),
        ),
        sa.Column("external_calendar_id", sa.String(length=255), nullable=False),
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("expiration_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "channel_id",
            name="uq_calendar_watch_channels_channel_id",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'STOPPED', 'EXPIRED')",
            name="ck_calendar_watch_channels_status_valid",
        ),
        sa.CheckConstraint(
            "length(trim(channel_id)) > 0",
            name="ck_calendar_watch_channels_channel_id_non_empty",
        ),
    )
    op.create_index(
        "ix_calendar_watch_channels_tenant_id",
        "calendar_watch_channels",
        ["tenant_id"],
    )
    op.create_index(
        "ix_calendar_watch_channels_tenant_prov_cal",
        "calendar_watch_channels",
        ["tenant_id", "provider", "external_calendar_id"],
    )
    op.create_index(
        "ix_calendar_watch_channels_expiration",
        "calendar_watch_channels",
        ["expiration_at"],
    )


def downgrade() -> None:
    """Drop calendar_watch_channels table."""
    op.drop_table("calendar_watch_channels")
