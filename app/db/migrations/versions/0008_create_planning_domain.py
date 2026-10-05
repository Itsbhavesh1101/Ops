"""create_planning_domain

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01 00:00:00.000000

Phase 2A Canonical Planning Domain migration.
Creates canonical planning domain tables:
- goals
- plans (with native versioning and approved baseline support)
- milestones
- tasks
- task_dependencies
- task_risks

Invariants:
- All tables tenant-scoped (tenant_id).
- Application-generated UUIDv4 primary keys.
- Check constraints enforcing durations, percentages, state machines, and no self-dependencies.
- Standard VARCHAR/CHECK constraints without PostgreSQL custom enum types.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical planning domain tables with constraints and indexes."""
    # 1. goals
    op.create_table(
        "goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.String(length=1000), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_window", postgresql.JSONB(), nullable=True),
        sa.Column(
            "constraints", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "resource_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "context_references",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("priority_target", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(trim(objective)) > 0", name="ck_goals_objective_not_blank"),
    )
    op.create_index("ix_goals_tenant_id_created_at", "goals", ["tenant_id", "created_at"])

    # 2. plans
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("plan_name", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default=sa.text("'DRAFT'")
        ),
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "superseded_by_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("baseline_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_plans_version_number_positive"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'GENERATED', 'VALIDATED', 'APPROVAL_PENDING', "
            "'APPROVED', 'ACTIVE', 'COMPLETED', 'CANCELLED')",
            name="ck_plans_status_valid",
        ),
        sa.UniqueConstraint(
            "tenant_id", "goal_id", "version_number", name="uq_plans_tenant_goal_version"
        ),
    )
    op.create_index("ix_plans_tenant_id_goal_id", "plans", ["tenant_id", "goal_id"])
    op.create_index("ix_plans_tenant_id_status", "plans", ["tenant_id", "status"])

    # 3. milestones
    op.create_table(
        "milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("temporary_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "plan_id", "temporary_id", name="uq_milestones_tenant_plan_temporary_id"
        ),
    )
    op.create_index("ix_milestones_tenant_id_plan_id", "milestones", ["tenant_id", "plan_id"])

    # 4. tasks
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("temporary_id", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("wbs_code", sa.String(length=64), nullable=True),
        sa.Column("owner_role", sa.String(length=128), nullable=True),
        sa.Column("owner_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "estimated_duration_minutes", sa.Integer(), nullable=False, server_default=sa.text("60")
        ),
        sa.Column("effort_estimate_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("work_category", sa.String(length=128), nullable=True),
        sa.Column(
            "priority", sa.String(length=32), nullable=False, server_default=sa.text("'MEDIUM'")
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'TODO'")),
        sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("actual_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column(
            "milestone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("milestones.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "estimated_duration_minutes >= 0", name="ck_tasks_duration_non_negative"
        ),
        sa.CheckConstraint(
            "actual_duration_minutes IS NULL OR actual_duration_minutes >= 0",
            name="ck_tasks_actual_duration_non_negative",
        ),
        sa.CheckConstraint(
            "progress_percentage >= 0 AND progress_percentage <= 100",
            name="ck_tasks_progress_percentage_bounds",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="ck_tasks_priority_valid"
        ),
        sa.CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED')",
            name="ck_tasks_status_valid",
        ),
        sa.UniqueConstraint(
            "tenant_id", "plan_id", "temporary_id", name="uq_tasks_tenant_plan_temporary_id"
        ),
    )
    op.create_index("ix_tasks_tenant_id_plan_id", "tasks", ["tenant_id", "plan_id"])
    op.create_index("ix_tasks_tenant_id_status", "tasks", ["tenant_id", "status"])

    # 5. task_dependencies
    op.create_table(
        "task_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "predecessor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "successor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dependency_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'FINISH_TO_START'"),
        ),
        sa.Column("lag_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "predecessor_id <> successor_id", name="ck_task_dependencies_no_self_edge"
        ),
        sa.CheckConstraint("lag_minutes >= 0", name="ck_task_dependencies_lag_non_negative"),
        sa.CheckConstraint(
            "dependency_type IN ("
            "'FINISH_TO_START', 'START_TO_START', 'FINISH_TO_FINISH', 'START_TO_FINISH'"
            ")",
            name="ck_task_dependencies_type_valid",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "plan_id",
            "predecessor_id",
            "successor_id",
            name="uq_task_dependencies_tenant_plan_pred_succ",
        ),
    )
    op.create_index(
        "ix_task_dependencies_tenant_id_plan_id", "task_dependencies", ["tenant_id", "plan_id"]
    )
    op.create_index("ix_task_dependencies_predecessor_id", "task_dependencies", ["predecessor_id"])
    op.create_index("ix_task_dependencies_successor_id", "task_dependencies", ["successor_id"])

    # 6. task_risks
    op.create_table(
        "task_risks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_evaluable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name="ck_task_risks_severity_valid"
        ),
    )
    op.create_index("ix_task_risks_tenant_id_plan_id", "task_risks", ["tenant_id", "plan_id"])
    op.create_index("ix_task_risks_task_id", "task_risks", ["task_id"])


def downgrade() -> None:
    """Drop canonical planning domain tables in reverse dependency order."""
    op.drop_table("task_risks")
    op.drop_table("task_dependencies")
    op.drop_table("tasks")
    op.drop_table("milestones")
    op.drop_table("plans")
    op.drop_table("goals")
