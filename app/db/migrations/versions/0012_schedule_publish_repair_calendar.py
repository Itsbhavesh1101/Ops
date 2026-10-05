"""schedule_publish_repair_calendar

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-05 12:00:00.000000

Phase 4B Canonical Generic Scheduling Publication, Repair, and Calendar Sync Migration.
Creates:
- calendar_links: Secondary mapping entity connecting canonical schedule
  assignments to external calendars.
Updates:
- schedules: Adds published_at, published_by, repair_reason, repair_scope.

Invariants:
- All tables and mapping links are tenant-scoped (tenant_id).
- Application-generated UUIDv4 primary keys.
- Check constraints enforce valid sync_status enums.
- Cascade deletion configured safely from schedules and schedule_assignments.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply Phase 4B publication, repair, and calendar sync schema updates."""
    # 1. Update schedules table with publication and repair metadata
    op.add_column(
        "schedules",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "schedules",
        sa.Column("published_by", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "schedules",
        sa.Column("repair_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "schedules",
        sa.Column(
            "repair_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # 2. Create calendar_links table
    op.create_table(
        "calendar_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("activity_id", sa.String(length=128), nullable=True),
        sa.Column("schedule_key", sa.String(length=128), nullable=True),
        sa.Column(
            "link_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'EVENT'"),
        ),
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'google'"),
        ),
        sa.Column("external_calendar_id", sa.String(length=255), nullable=True),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("sync_token", sa.String(length=255), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("provider_version", sa.Integer(), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["schedules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["schedule_assignments.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "assignment_id",
            "provider",
            name="uq_calendar_links_tenant_assign_prov",
        ),
        sa.CheckConstraint(
            "sync_status IN ('PENDING', 'SYNCED', 'FAILED', 'DEGRADED', 'DELETED', 'CONFLICT')",
            name="ck_calendar_links_sync_status_valid",
        ),
        sa.CheckConstraint(
            "link_kind IN ('EVENT', 'SYNC_CURSOR')",
            name="ck_calendar_links_link_kind_valid",
        ),
    )
    op.create_index(
        "ix_calendar_links_tenant_id",
        "calendar_links",
        ["tenant_id"],
    )
    op.create_index(
        "ix_calendar_links_schedule_id",
        "calendar_links",
        ["schedule_id"],
    )
    op.create_index(
        "ix_calendar_links_tenant_key_activity",
        "calendar_links",
        ["tenant_id", "schedule_key", "activity_id"],
    )
    op.create_index(
        "ix_calendar_links_assignment_id",
        "calendar_links",
        ["assignment_id"],
    )
    op.create_index(
        "ix_calendar_links_external_event_id",
        "calendar_links",
        ["external_event_id"],
    )
    op.create_index(
        "uq_calendar_links_cursor",
        "calendar_links",
        ["tenant_id", "provider", "external_calendar_id"],
        unique=True,
        postgresql_where=sa.text("link_kind = 'SYNC_CURSOR'"),
    )
    op.create_index(
        "uq_calendar_links_scope_ext_event",
        "calendar_links",
        ["tenant_id", "provider", "external_calendar_id", "external_event_id"],
        unique=True,
        postgresql_where=sa.text("link_kind = 'EVENT' AND external_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Revert Phase 4B publication, repair, and calendar sync schema updates."""
    op.drop_table("calendar_links")
    op.drop_column("schedules", "repair_scope")
    op.drop_column("schedules", "repair_reason")
    op.drop_column("schedules", "published_by")
    op.drop_column("schedules", "published_at")
