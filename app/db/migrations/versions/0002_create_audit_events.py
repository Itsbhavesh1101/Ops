"""create_audit_events

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 00:00:00.000000

Part of Phase 1 Foundation Task 1F.
Creates canonical append-only audit_events table with UUID primary key,
PostgreSQL JSONB details, timezone-aware recorded_at timestamp, justified indexes,
and database-level append-only trigger prohibiting UPDATE, DELETE, and TRUNCATE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create audit_events table, indexes, and append-only immutability trigger."""
    op.create_table(
        "audit_events",
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=255), nullable=True),
        sa.Column("actor_principal_id", sa.String(length=255), nullable=True),
        sa.Column("actor_principal_type", sa.String(length=32), nullable=True),
        sa.Column("authentication_method", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("audit_event_id", name="pk_audit_events"),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_audit_events_schema_version_positive",
        ),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0",
            name="ck_audit_events_event_type_not_blank",
        ),
        sa.CheckConstraint(
            "(actor_principal_id IS NULL "
            "AND actor_principal_type IS NULL "
            "AND authentication_method IS NULL) "
            "OR ("
            "actor_principal_id IS NOT NULL "
            "AND tenant_id IS NOT NULL "
            "AND actor_principal_type IS NOT NULL "
            "AND authentication_method IS NOT NULL)",
            name="ck_audit_events_actor_consistency",
        ),
        sa.CheckConstraint(
            "actor_principal_type IS NULL OR actor_principal_type IN ('HUMAN', 'SERVICE')",
            name="ck_audit_events_actor_principal_type_valid",
        ),
        sa.CheckConstraint(
            "authentication_method IS NULL OR authentication_method IN ('OIDC', 'SERVICE_API_KEY')",
            name="ck_audit_events_authentication_method_valid",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('SUCCESS', 'FAILURE', 'DENIED')",
            name="ck_audit_events_outcome_valid",
        ),
    )

    op.create_index(
        "ix_audit_events_tenant_id_recorded_at",
        "audit_events",
        ["tenant_id", "recorded_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_correlation_id",
        "audit_events",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_resource_type_resource_id_recorded_at",
        "audit_events",
        ["resource_type", "resource_id", "recorded_at"],
        unique=False,
    )

    # Database-level trigger function: reject any mutation attempt
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_prevent_audit_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events table is append-only: % operations are prohibited', TG_OP
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Statement-level trigger protecting against UPDATE, DELETE, and TRUNCATE
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_prevent_mutation
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION fn_prevent_audit_event_mutation();
        """
    )


def downgrade() -> None:
    """Drop append-only trigger, function, indexes, and audit_events table."""
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_prevent_mutation ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS fn_prevent_audit_event_mutation();")
    op.drop_index(
        "ix_audit_events_resource_type_resource_id_recorded_at",
        table_name="audit_events",
    )
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_id_recorded_at", table_name="audit_events")
    op.drop_table("audit_events")
