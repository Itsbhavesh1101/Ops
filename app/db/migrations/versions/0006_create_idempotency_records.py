"""create_idempotency_records

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28 00:00:00.000000

Part of Phase 1 Foundation Task 1H.2.
Creates canonical transactional idempotency_records table with composite primary key
(tenant_id, operation_name, key_digest), request_fingerprint, server-authored
created_at timestamp, and check constraints enforcing canonical naming and SHA-256
digest hexadecimal formats.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create idempotency_records table with composite primary key and check constraints."""
    op.create_table(
        "idempotency_records",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("operation_name", sa.String(length=128), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "operation_name",
            "key_digest",
            name="pk_idempotency_records",
        ),
        sa.CheckConstraint(
            r"operation_name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'",
            name="ck_idempotency_records_operation_name_format",
        ),
        sa.CheckConstraint(
            r"key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_records_key_digest_format",
        ),
        sa.CheckConstraint(
            r"request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_records_request_fingerprint_format",
        ),
        sa.CheckConstraint(
            r"length(trim(tenant_id)) > 0",
            name="ck_idempotency_records_tenant_id_non_empty",
        ),
    )


def downgrade() -> None:
    """Drop idempotency_records table safely."""
    op.drop_table("idempotency_records")
