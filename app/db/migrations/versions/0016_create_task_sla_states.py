"""create_task_sla_states

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28 00:00:00.000000

Phase DQ-4 AD-DQ4-02 PostgreSQL-Backed Task SLA State Persistence Migration.
Creates:
- task_sla_states: Durable, tenant-scoped storage for active task SLA tracking, thresholds,
  pause durations, and warning/breach firing state across worker restarts.

Invariants:
- All task SLA states strictly tenant-scoped (tenant_id).
- Application-generated canonical UUIDv4 primary keys.
- Unique constraint on (tenant_id, task_id) guaranteeing exactly one SLA record per task.
- Survives worker restarts and node reboots without state loss.
- Row-level locking (SELECT FOR UPDATE SKIP LOCKED) prevents concurrent split-brain evaluations.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create task_sla_states table and associated indexes/constraints."""
    op.create_table(
        "task_sla_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'RUNNING'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warning_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breach_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_paused_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("remaining_warning_seconds", sa.Integer(), nullable=True),
        sa.Column("remaining_breach_seconds", sa.Integer(), nullable=True),
        sa.Column("warning_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breach_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            name="uq_task_sla_tenant_task",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_task_sla_tenant_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'WARNING', 'BREACHED', 'PAUSED', 'COMPLETED')",
            name="ck_task_sla_status",
        ),
        sa.CheckConstraint(
            "total_paused_seconds >= 0",
            name="ck_task_sla_total_paused_non_negative",
        ),
    )

    op.create_index(
        "ix_task_sla_eval",
        "task_sla_states",
        ["tenant_id", "status", "warning_at", "breach_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_sla_task_id",
        "task_sla_states",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_task_sla_tenant_status",
        "task_sla_states",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """Drop task_sla_states table and associated indexes."""
    op.drop_index(
        "ix_task_sla_tenant_status",
        table_name="task_sla_states",
    )
    op.drop_index(
        "ix_task_sla_task_id",
        table_name="task_sla_states",
    )
    op.drop_index(
        "ix_task_sla_eval",
        table_name="task_sla_states",
    )
    op.drop_table("task_sla_states")

