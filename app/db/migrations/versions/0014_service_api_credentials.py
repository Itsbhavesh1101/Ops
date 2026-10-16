"""service_api_credentials

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10 00:00:00.000000

Phase 8D Persistent Service API Key Credential Store Migration (REC-001 Closure).
Creates:
- service_api_credentials: Durable, tenant-scoped storage for service API key credentials.

Invariants:
- All credentials strictly tenant-scoped (tenant_id).
- Application-generated canonical UUIDv4 primary keys.
- Unique constraint on credential_id for O(1) lookup.
- Only one-way SHA-256 secret digests (64 hex chars) are stored; plaintext secrets
  are NEVER persisted.
- Roles and permissions JSON arrays for durable RBAC principal resolution across restarts.
- Check constraints enforce non-empty identifier, tenant_id, and exact 64-character digest length.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create service_api_credentials table and associated indexes/constraints."""
    op.create_table(
        "service_api_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("credential_id", sa.String(length=64), nullable=False),
        sa.Column("service_subject", sa.String(length=255), nullable=False),
        sa.Column("secret_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "issuer",
            sa.String(length=255),
            nullable=True,
            server_default=sa.text("'e7-service-key'"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
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
            "credential_id",
            name="uq_service_api_credentials_credential_id",
        ),
        sa.CheckConstraint(
            "length(trim(credential_id)) > 0",
            name="ck_service_cred_id_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_service_cred_tenant_not_blank",
        ),
        sa.CheckConstraint(
            "length(secret_digest) = 64",
            name="ck_service_cred_secret_digest_len",
        ),
    )

    op.create_index(
        "ix_service_api_credentials_tenant_id",
        "service_api_credentials",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_api_credentials_credential_id",
        "service_api_credentials",
        ["credential_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop service_api_credentials table and associated indexes."""
    op.drop_index(
        "ix_service_api_credentials_credential_id",
        table_name="service_api_credentials",
    )
    op.drop_index(
        "ix_service_api_credentials_tenant_id",
        table_name="service_api_credentials",
    )
    op.drop_table("service_api_credentials")
