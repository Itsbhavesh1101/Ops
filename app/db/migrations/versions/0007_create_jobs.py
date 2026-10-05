"""create_jobs

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28 00:00:00.000000

Part of Phase 1 Foundation Task 1I.4B.
Creates canonical asynchronous jobs table with UUID primary key (job_id),
tenant scoping (tenant_id), namespaced job type (job_type), status,
sanitized failure fields, server-authored timestamps, and check constraints
enforcing canonical naming formats and lifecycle state invariants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create jobs table with UUID primary key and lifecycle check constraints."""
    op.create_table(
        "jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("job_type", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_message", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id", name="pk_jobs"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            r"job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="ck_jobs_job_type",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_jobs_tenant_id_nonblank",
        ),
        sa.CheckConstraint(
            r"failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="ck_jobs_failure_code_format",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL AND failure_message IS NOT NULL) OR "
            "(status != 'failed' AND failure_code IS NULL AND failure_message IS NULL)",
            name="ck_jobs_failure_consistency",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL)",
            name="ck_jobs_timestamp_state_consistency",
        ),
        sa.CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) AND "
            "(completed_at IS NULL OR completed_at >= created_at) AND "
            "(started_at IS NULL OR completed_at IS NULL OR completed_at >= started_at)",
            name="ck_jobs_timestamp_ordering",
        ),
    )


def downgrade() -> None:
    """Drop jobs table safely."""
    op.drop_table("jobs")
