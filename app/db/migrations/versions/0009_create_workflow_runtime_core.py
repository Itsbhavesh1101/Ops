"""create_workflow_runtime_core

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02 00:00:00.000000

Phase 3A Canonical Workflow Runtime Core migration.
Creates canonical workflow persistence tables:
- workflow_definitions (versioned BPMN XML definition metadata)
- workflow_instances (canonical process instance projection)
- workflow_task_mappings (human user task mapping and authorization state)

Invariants:
- All tables tenant-scoped (tenant_id).
- Application-generated UUIDv4 primary keys.
- Flowable native execution IDs are strictly secondary external references.
- No Flowable internal schema copied; zero Liquibase ACT_* tables managed here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical workflow domain tables with constraints and indexes."""
    # 1. workflow_definitions
    op.create_table(
        "workflow_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("bpmn_process_id", sa.String(length=255), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_xml", sa.Text(), nullable=False),
        sa.Column("deployment_id", sa.String(length=255), nullable=True),
        sa.Column("engine_definition_id", sa.String(length=255), nullable=True),
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
            "workflow_key",
            "version",
            name="uq_workflow_defs_tenant_key_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_workflow_def_version_positive"),
        sa.CheckConstraint("length(trim(workflow_key)) > 0", name="ck_workflow_def_key_not_blank"),
        sa.CheckConstraint(
            "length(trim(bpmn_process_id)) > 0", name="ck_workflow_def_process_id_not_blank"
        ),
    )
    op.create_index(
        "ix_workflow_defs_tenant_key",
        "workflow_definitions",
        ["tenant_id", "workflow_key"],
    )
    op.create_index(
        "ix_workflow_defs_tenant_engine_def",
        "workflow_definitions",
        ["tenant_id", "engine_definition_id"],
    )

    # 2. workflow_instances
    op.create_table(
        "workflow_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("business_reference", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("engine_instance_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=64), nullable=False, server_default=sa.text("'RUNNING'")
        ),
        sa.Column(
            "input_variables",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_by", sa.String(length=255), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
        sa.UniqueConstraint(
            "tenant_id",
            "engine_instance_id",
            name="uq_workflow_inst_tenant_engine_id",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'SUSPENDED', 'TERMINATED', 'FAILED')",
            name="ck_workflow_inst_status_valid",
        ),
    )
    op.create_index(
        "ix_workflow_inst_tenant_status",
        "workflow_instances",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_workflow_inst_tenant_business_ref",
        "workflow_instances",
        ["tenant_id", "business_reference"],
    )
    op.create_index(
        "ix_workflow_inst_tenant_correlation_id",
        "workflow_instances",
        ["tenant_id", "correlation_id"],
    )

    # 3. workflow_task_mappings
    op.create_table(
        "workflow_task_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column(
            "workflow_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine_task_id", sa.String(length=255), nullable=False),
        sa.Column("task_definition_key", sa.String(length=255), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("assignee", sa.String(length=255), nullable=True),
        sa.Column(
            "candidate_roles",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(length=64), nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("action_by", sa.String(length=255), nullable=True),
        sa.Column("action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "variables",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "engine_task_id",
            name="uq_workflow_task_tenant_engine_id",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'COMPLETED', 'CANCELLED')",
            name="ck_workflow_task_status_valid",
        ),
    )
    op.create_index(
        "ix_workflow_tasks_tenant_instance",
        "workflow_task_mappings",
        ["tenant_id", "workflow_instance_id"],
    )
    op.create_index(
        "ix_workflow_tasks_tenant_status",
        "workflow_task_mappings",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_workflow_tasks_tenant_assignee",
        "workflow_task_mappings",
        ["tenant_id", "assignee"],
    )


def downgrade() -> None:
    """Drop canonical workflow tables in reverse dependency order."""
    op.drop_table("workflow_task_mappings")
    op.drop_table("workflow_instances")
    op.drop_table("workflow_definitions")
