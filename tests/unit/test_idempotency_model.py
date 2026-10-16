"""Unit tests for IdempotencyRecord model.

Part of Phase 1 Foundation Task 1H.2.
Verifies:
- IdempotencyRecord exists and maps to idempotency_records.
- Table columns, types, nullability, and composite primary key structure.
- Canonical check constraints enforcing format of operation_name, key_digest,
  request_fingerprint, and non-empty tenant_id.
- Privacy-minimizing schema: zero raw idempotency key, zero principal ID.
- Marker-only schema: zero status/state enum, zero lease columns, zero response columns,
  zero retention/TTL columns.
- Base.metadata contains exactly the four canonical foundation tables.
"""

from typing import cast

import sqlalchemy as sa
from sqlalchemy import Table

import app.db.models  # noqa: F401
from app.core.idempotency.models import IdempotencyRecord
from app.db.base import Base


def _get_table() -> Table:
    """Helper to get typed Table from IdempotencyRecord."""
    return cast(Table, IdempotencyRecord.__table__)


def test_idempotency_table_registered_in_metadata() -> None:
    """Verify idempotency_records table is registered in Base.metadata."""
    assert "idempotency_records" in Base.metadata.tables
    table = Base.metadata.tables["idempotency_records"]
    assert table is _get_table()


def test_base_metadata_contains_exactly_canonical_tables() -> None:
    """Verify Base.metadata contains exactly the canonical foundation tables."""
    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A Scheduling (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        # Phase 4B Calendar (1)
        "calendar_links",
        # Phase 7 Calendar Watch (1)
        "calendar_watch_channels",
        # Phase 8D Service Credentials Persistence (1)
        "service_api_credentials",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26


def test_idempotency_columns_and_types() -> None:
    """Verify canonical columns, types, nullability, and server defaults on idempotency_records."""
    table = _get_table()

    expected_columns = {
        "tenant_id",
        "operation_name",
        "key_digest",
        "request_fingerprint",
        "created_at",
    }
    actual_columns = set(table.columns.keys())
    assert actual_columns == expected_columns, (
        f"idempotency_records must contain strictly {expected_columns}, found: {actual_columns}"
    )

    # 1. tenant_id: VARCHAR(128) NOT NULL, part of composite PK
    col_tenant = table.columns["tenant_id"]
    assert isinstance(col_tenant.type, sa.String)
    assert col_tenant.type.length == 128
    assert col_tenant.nullable is False
    assert col_tenant.primary_key is True

    # 2. operation_name: VARCHAR(128) NOT NULL, part of composite PK
    col_op = table.columns["operation_name"]
    assert isinstance(col_op.type, sa.String)
    assert col_op.type.length == 128
    assert col_op.nullable is False
    assert col_op.primary_key is True

    # 3. key_digest: VARCHAR(64) NOT NULL, part of composite PK
    col_digest = table.columns["key_digest"]
    assert isinstance(col_digest.type, sa.String)
    assert col_digest.type.length == 64
    assert col_digest.nullable is False
    assert col_digest.primary_key is True

    # 4. request_fingerprint: VARCHAR(64) NOT NULL
    col_fp = table.columns["request_fingerprint"]
    assert isinstance(col_fp.type, sa.String)
    assert col_fp.type.length == 64
    assert col_fp.nullable is False
    assert col_fp.primary_key is False

    # 5. created_at: TIMESTAMPTZ NOT NULL, server_default now()
    col_created = table.columns["created_at"]
    assert isinstance(col_created.type, sa.DateTime)
    assert col_created.type.timezone is True
    assert col_created.nullable is False
    assert col_created.server_default is not None
    assert col_created.primary_key is False


def test_idempotency_primary_key_is_composite() -> None:
    """Verify primary key is composite (tenant_id, operation_name, key_digest)."""
    table = _get_table()
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["tenant_id", "operation_name", "key_digest"]


def test_idempotency_privacy_minimizing_zero_forbidden_columns() -> None:
    """Verify idempotency_records stores zero raw key, principal, or status columns."""
    table = _get_table()
    forbidden = {
        "idempotency_id",
        "record_id",
        "id",
        "raw_key",
        "idempotency_key",
        "original_key",
        "client_token",
        "principal_id",
        "user_id",
        "actor_id",
        "email",
        "role",
        "permissions",
        "status",
        "state",
        "lease_token",
        "claimed_at",
        "lease_expires_at",
        "owner_id",
        "heartbeat",
        "response_body",
        "response_json",
        "response_headers",
        "http_status",
        "content_type",
        "result_payload",
        "result_reference",
        "expires_at",
        "cleanup_after",
        "ttl",
    }
    actual_columns = set(table.columns.keys())
    assert actual_columns.isdisjoint(forbidden)


def test_idempotency_check_constraints() -> None:
    """Verify check constraints enforce canonical format for operation, digest, and tenant."""
    table = _get_table()
    ck_names = {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)}

    expected_ck_suffixes = {
        "ck_idempotency_records_operation_name_format",
        "ck_idempotency_records_key_digest_format",
        "ck_idempotency_records_request_fingerprint_format",
        "ck_idempotency_records_tenant_id_non_empty",
    }
    assert ck_names == expected_ck_suffixes


def test_idempotency_zero_speculative_secondary_indexes() -> None:
    """Verify zero speculative secondary indexes exist on idempotency_records."""
    table = _get_table()
    assert len(table.indexes) == 0, (
        f"idempotency_records must not define speculative secondary indexes, found: {table.indexes}"
    )
