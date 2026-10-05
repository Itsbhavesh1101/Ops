"""Unit tests for InboxMessageRecord model.

Part of Phase 1 Foundation Task 1G.4A.
Verifies:
- InboxMessageRecord exists and maps to inbox_messages.
- Table columns, types, nullability, and primary key composite structure.
- Canonical check constraint enforcing consumer_name format.
- Privacy-minimizing schema: zero envelope JSONB, zero payload, zero tenant_id.
- Deduplication only: zero status enum, zero raw failure columns.
- Base.metadata contains exactly {"audit_events", "outbox_events", "inbox_messages"}.
"""

from typing import cast

import sqlalchemy as sa
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.db.base import Base
from app.events.inbox.models import InboxMessageRecord


def _get_table() -> Table:
    """Helper to get typed Table from InboxMessageRecord."""
    return cast(Table, InboxMessageRecord.__table__)


def test_inbox_table_registered_in_metadata() -> None:
    """Verify inbox_messages table is registered in Base.metadata."""
    assert "inbox_messages" in Base.metadata.tables
    table = Base.metadata.tables["inbox_messages"]
    assert table is _get_table()


def test_base_metadata_contains_exactly_canonical_tables() -> None:
    """Verify Base.metadata contains exactly canonical tables."""
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
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 24


def test_inbox_columns_and_types() -> None:
    """Verify canonical columns, types, nullability, and server defaults on inbox_messages."""
    table = _get_table()

    expected_columns = {"consumer_name", "event_id", "recorded_at"}
    actual_columns = set(table.columns.keys())
    assert actual_columns == expected_columns, (
        f"inbox_messages must contain strictly {expected_columns}, found: {actual_columns}"
    )

    # 1. consumer_name: VARCHAR(128) NOT NULL, part of composite PK
    col_consumer = table.columns["consumer_name"]
    assert isinstance(col_consumer.type, sa.String)
    assert col_consumer.type.length == 128
    assert col_consumer.nullable is False
    assert col_consumer.primary_key is True

    # 2. event_id: UUID NOT NULL, part of composite PK
    col_event_id = table.columns["event_id"]
    assert isinstance(col_event_id.type, postgresql.UUID)
    assert col_event_id.type.as_uuid is True
    assert col_event_id.nullable is False
    assert col_event_id.primary_key is True

    # 3. recorded_at: TIMESTAMPTZ NOT NULL, server_default now()
    col_recorded = table.columns["recorded_at"]
    assert isinstance(col_recorded.type, sa.DateTime)
    assert col_recorded.type.timezone is True
    assert col_recorded.nullable is False
    assert col_recorded.server_default is not None
    assert col_recorded.primary_key is False


def test_inbox_primary_key_is_composite() -> None:
    """Verify primary key is composite (consumer_name, event_id)."""
    table = _get_table()
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["consumer_name", "event_id"]


def test_inbox_privacy_minimizing_zero_forbidden_columns() -> None:
    """Verify inbox_messages stores zero envelope, payload, tenant, status, or error columns."""
    table = _get_table()
    forbidden = {
        "inbox_id",
        "id",
        "envelope",
        "payload",
        "tenant_id",
        "status",
        "state",
        "error",
        "failure_reason",
        "retry_count",
        "attempt_count",
    }
    actual_columns = set(table.columns.keys())
    assert actual_columns.isdisjoint(forbidden)


def test_inbox_check_constraint_consumer_name_format() -> None:
    """Verify check constraint enforces canonical namespaced consumer_name format."""
    table = _get_table()
    check_constraints = [c for c in table.constraints if isinstance(c, sa.CheckConstraint)]
    assert len(check_constraints) >= 1

    ck = next((c for c in check_constraints if "consumer_name" in str(c.name)), None)
    assert ck is not None, f"Expected consumer_name check constraint, found: {check_constraints}"
    assert ck.name == "ck_inbox_messages_consumer_name_format"

    sql_text = str(ck.sqltext)
    assert "consumer_name ~" in sql_text
    assert "^[a-z][a-z0-9_]*" in sql_text


def test_inbox_zero_speculative_indexes() -> None:
    """Verify inbox_messages defines zero speculative secondary indexes."""
    table = _get_table()
    # The only index should be the primary key index generated by PostgreSQL
    assert len(table.indexes) == 0, f"Expected 0 secondary indexes, found: {table.indexes}"
