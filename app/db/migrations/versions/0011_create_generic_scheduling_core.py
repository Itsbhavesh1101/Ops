"""create_generic_scheduling_core

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 00:00:00.000000

Phase 4A Canonical Generic Scheduling Core migration.
Creates canonical persistence tables:
- resources: Canonical resource registry with types, capacity, and skills
- resource_availabilities: Timezone-aware availability and blackout intervals
- constraint_definitions: Versioned constraint definitions
- solver_profiles: Versioned solver configuration profiles
- schedule_jobs: Asynchronous solver job tracking and worker leases
- schedules: Canonical candidate/published schedule records
- schedule_assignments: Discrete activity-to-resource time slot assignments

Invariants:
- All tables tenant-scoped (tenant_id).
- Application-generated UUIDv4 primary keys.
- Check constraints enforce positive versions, non-negative capacity, and valid state enums.
- Cascade deletion configured safely for child records (availabilities, assignments).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create generic scheduling core tables."""
    # 1. resources
    op.create_table(
        "resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("resource_key", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("display_label", sa.String(length=255), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "skills",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("tenant_id", "resource_key", name="uq_resources_tenant_key"),
        sa.CheckConstraint("capacity >= 0", name="ck_resources_capacity_non_negative"),
        sa.CheckConstraint(
            "type IN ('PERSON', 'ROOM', 'EQUIPMENT', 'OTHER')",
            name="ck_resources_type_valid",
        ),
    )
    op.create_index("ix_resources_tenant_id", "resources", ["tenant_id"])

    # 2. resource_availabilities
    op.create_table(
        "resource_availabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "interval_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'UNAVAILABLE'"),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resources.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("end_at >= start_at", name="ck_resource_availabilities_order"),
        sa.CheckConstraint(
            "interval_type IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_resource_availabilities_type_valid",
        ),
    )
    op.create_index(
        "ix_resource_availabilities_tenant_id",
        "resource_availabilities",
        ["tenant_id"],
    )
    op.create_index(
        "ix_resource_availabilities_resource_id",
        "resource_availabilities",
        ["resource_id"],
    )

    # 3. constraint_definitions
    op.create_table(
        "constraint_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("constraint_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("constraint_type", sa.String(length=32), nullable=False),
        sa.Column(
            "hardness",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'HARD'"),
        ),
        sa.Column(
            "parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
            "tenant_id",
            "constraint_key",
            "version",
            name="uq_constraint_def_tenant_key_ver",
        ),
        sa.CheckConstraint("version > 0", name="ck_constraint_def_version_positive"),
        sa.CheckConstraint(
            "hardness IN ('HARD', 'SOFT')",
            name="ck_constraint_def_hardness_valid",
        ),
    )
    op.create_index(
        "ix_constraint_definitions_tenant_id",
        "constraint_definitions",
        ["tenant_id"],
    )

    # 4. solver_profiles
    op.create_table(
        "solver_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("profile_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
            "tenant_id", "profile_key", "version", name="uq_solver_profile_tenant_key_ver"
        ),
        sa.CheckConstraint("version > 0", name="ck_solver_profile_version_positive"),
    )
    op.create_index("ix_solver_profiles_tenant_id", "solver_profiles", ["tenant_id"])

    # 5. schedule_jobs
    op.create_table(
        "schedule_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("schedule_key", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'REQUESTED'"),
        ),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("solver_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "solver_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["solver_profile_id"], ["solver_profiles.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'SOLVING', 'FEASIBLE', "
            "'INFEASIBLE', 'BUDGET_EXHAUSTED', 'FAILED')",
            name="ck_schedule_jobs_status_valid",
        ),
    )
    op.create_index("ix_schedule_jobs_tenant_id", "schedule_jobs", ["tenant_id"])
    op.create_index("ix_schedule_jobs_status", "schedule_jobs", ["status"])
    op.create_index(
        "ix_schedule_jobs_input_snapshot_hash",
        "schedule_jobs",
        ["input_snapshot_hash"],
    )
    op.create_index("ix_schedule_jobs_created_at", "schedule_jobs", ["created_at"])

    # 6. schedules
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("schedule_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'FEASIBLE'"),
        ),
        sa.Column("source_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("solver_profile_version", sa.Integer(), nullable=True),
        sa.Column(
            "solver_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "score_components",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["source_job_id"], ["schedule_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["schedules.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "tenant_id", "schedule_key", "version", name="uq_schedules_tenant_key_ver"
        ),
        sa.CheckConstraint("version > 0", name="ck_schedules_version_positive"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'FEASIBLE', 'REVIEW', 'PUBLISHED', 'SUPERSEDED')",
            name="ck_schedules_status_valid",
        ),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])

    # 7. schedule_assignments
    op.create_table(
        "schedule_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["schedules.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("end_at >= start_at", name="ck_schedule_assignments_order"),
    )
    op.create_index(
        "ix_schedule_assignments_tenant_id",
        "schedule_assignments",
        ["tenant_id"],
    )
    op.create_index(
        "ix_schedule_assignments_schedule_id",
        "schedule_assignments",
        ["schedule_id"],
    )
    op.create_index(
        "ix_schedule_assignments_resource_id",
        "schedule_assignments",
        ["resource_id"],
    )
    op.create_index(
        "ix_schedule_assignments_start_at",
        "schedule_assignments",
        ["start_at"],
    )


def downgrade() -> None:
    """Drop generic scheduling core tables in reverse dependency order."""
    op.drop_table("schedule_assignments")
    op.drop_table("schedules")
    op.drop_table("schedule_jobs")
    op.drop_table("solver_profiles")
    op.drop_table("constraint_definitions")
    op.drop_table("resource_availabilities")
    op.drop_table("resources")
