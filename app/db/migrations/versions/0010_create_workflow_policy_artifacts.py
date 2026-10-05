"""create_workflow_policy_artifacts

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-02 00:00:00.000000

Phase 3B Canonical Workflow Policies, Artifacts, and SLA Runtime Core migration.
Creates canonical persistence tables:
- sla_policies (versioned SLA definition and threshold rules)
- workflow_artifacts (versioned BPMN, DMN, and Form definitions)
Extends:
- workflow_task_mappings (adds form_key, sla_policy_id, sla_status,
  warning_at, paused_at, accumulated_pause_seconds)

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
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical SLA policy and artifact tables; extend task mappings."""
    # 1. sla_policies
    op.create_table(
        "sla_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "calendar_id",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'DEFAULT_BUSINESS'"),
        ),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_threshold_seconds", sa.Integer(), nullable=False),
        sa.Column("escalation_action", sa.String(length=64), nullable=False),
        sa.Column(
            "escalation_rules",
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
            "policy_key",
            "version",
            name="uq_sla_policy_tenant_key_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_sla_policy_version_positive"),
        sa.CheckConstraint(
            "target_duration_seconds > 0", name="ck_sla_policy_target_duration_positive"
        ),
        sa.CheckConstraint(
            "warning_threshold_seconds >= 0",
            name="ck_sla_policy_warning_threshold_non_negative",
        ),
        sa.CheckConstraint(
            "warning_threshold_seconds <= target_duration_seconds",
            name="ck_sla_policy_warning_lte_target",
        ),
    )
    op.create_index(
        "ix_sla_policies_tenant_key",
        "sla_policies",
        ["tenant_id", "policy_key"],
    )
    op.create_index(
        "ix_sla_policies_tenant_active",
        "sla_policies",
        ["tenant_id", "is_active"],
    )

    # 2. workflow_artifacts
    op.create_table(
        "workflow_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("artifact_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("engine_deployment_id", sa.String(length=255), nullable=True),
        sa.Column("engine_reference_id", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata_json",
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
            "artifact_type",
            "artifact_key",
            "version",
            name="uq_workflow_artifacts_tenant_type_key_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_workflow_artifact_version_positive"),
        sa.CheckConstraint(
            "artifact_type IN ('BPMN', 'DMN', 'FORM')",
            name="ck_workflow_artifact_type_valid",
        ),
    )
    op.create_index(
        "ix_workflow_artifacts_tenant_type_key",
        "workflow_artifacts",
        ["tenant_id", "artifact_type", "artifact_key"],
    )

    # 3. Extend workflow_task_mappings with SLA and form columns
    op.add_column(
        "workflow_task_mappings",
        sa.Column("form_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "workflow_task_mappings",
        sa.Column("sla_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflow_task_mappings_sla_policy_id",
        "workflow_task_mappings",
        "sla_policies",
        ["sla_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "workflow_task_mappings",
        sa.Column("sla_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workflow_task_mappings",
        sa.Column("warning_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_task_mappings",
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_task_mappings",
        sa.Column(
            "accumulated_pause_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_workflow_tasks_tenant_sla_status",
        "workflow_task_mappings",
        ["tenant_id", "sla_status"],
    )


def downgrade() -> None:
    """Drop Phase 3B extensions and tables in reverse dependency order."""
    op.drop_index("ix_workflow_tasks_tenant_sla_status", table_name="workflow_task_mappings")
    op.drop_constraint(
        "fk_workflow_task_mappings_sla_policy_id",
        "workflow_task_mappings",
        type_="foreignkey",
    )
    op.drop_column("workflow_task_mappings", "accumulated_pause_seconds")
    op.drop_column("workflow_task_mappings", "paused_at")
    op.drop_column("workflow_task_mappings", "warning_at")
    op.drop_column("workflow_task_mappings", "sla_status")
    op.drop_column("workflow_task_mappings", "sla_policy_id")
    op.drop_column("workflow_task_mappings", "form_key")

    op.drop_table("workflow_artifacts")
    op.drop_table("sla_policies")
