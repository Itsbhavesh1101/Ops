"""Unit tests for OutboxEventRecord dispatch-state columns, constraints, and indexes.

Part of Phase 1 Foundation Task 1G.3B1.
Validates:
- Operational columns exist: dispatch_attempt_count, next_attempt_at, claim_token, claimed_at.
- Column types and nullability:
  - dispatch_attempt_count: INTEGER NOT NULL, server default 0.
  - next_attempt_at: TIMESTAMPTZ NOT NULL, server default now().
  - claim_token: UUID nullable.
  - claimed_at: TIMESTAMPTZ nullable.
- Named CheckConstraints:
  - ck_outbox_events_dispatch_attempt_count_non_negative: dispatch_attempt_count >= 0.
  - ck_outbox_events_claim_token_and_claimed_at_pair: both NULL or both NOT NULL.
  - ck_outbox_events_published_has_no_active_claim: published rows cannot retain claim.
- Partial index evolution:
  - ix_outbox_events_dispatch_ready replaces ix_outbox_events_pending_created_at.
  - Indexes on (next_attempt_at, created_at, event_id) WHERE published_at IS NULL.
- Negative checks:
  - Zero retry error message, worker hostname, process ID, or RabbitMQ fields.
- Metadata scope: Base.metadata contains strictly {"audit_events", "outbox_events"}.
"""

from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base
from app.events.outbox.models import OutboxEventRecord


def test_dispatch_state_columns_and_types() -> None:
    """Verify all 8 outbox columns, types, nullabilities, and server defaults."""
    table = OutboxEventRecord.__table__

    expected_columns = {
        "event_id",
        "envelope",
        "created_at",
        "published_at",
        "dispatch_attempt_count",
        "next_attempt_at",
        "claim_token",
        "claimed_at",
    }
    assert set(table.columns.keys()) == expected_columns

    # Immutable canonical staging columns (Task 1G.2)
    assert isinstance(table.c.event_id.type, UUID)
    assert table.c.event_id.primary_key is True

    assert isinstance(table.c.envelope.type, JSONB)
    assert table.c.envelope.nullable is False

    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.nullable is False
    assert table.c.created_at.server_default is not None

    assert table.c.published_at.type.timezone is True
    assert table.c.published_at.nullable is True

    # Operational dispatch columns (Task 1G.3B1)
    assert isinstance(table.c.dispatch_attempt_count.type, Integer)
    assert table.c.dispatch_attempt_count.nullable is False
    assert table.c.dispatch_attempt_count.server_default is not None

    assert table.c.next_attempt_at.type.timezone is True
    assert table.c.next_attempt_at.nullable is False
    assert table.c.next_attempt_at.server_default is not None

    assert isinstance(table.c.claim_token.type, UUID)
    assert table.c.claim_token.nullable is True

    assert table.c.claimed_at.type.timezone is True
    assert table.c.claimed_at.nullable is True


def test_dispatch_state_named_check_constraints() -> None:
    """Verify all 5 named CheckConstraints on outbox_events table."""
    table = OutboxEventRecord.__table__
    ck_map = {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}

    # 1. event_id matches envelope
    assert "ck_outbox_events_event_id_matches_envelope" in ck_map
    assert (
        "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)"
        == (ck_map["ck_outbox_events_event_id_matches_envelope"])
    )

    # 2. envelope is JSONB object
    assert "ck_outbox_events_envelope_is_object" in ck_map
    assert "jsonb_typeof(envelope) = 'object'" == ck_map["ck_outbox_events_envelope_is_object"]

    # 3. dispatch_attempt_count >= 0
    assert "ck_outbox_events_dispatch_attempt_count_non_negative" in ck_map
    assert (
        "dispatch_attempt_count >= 0"
        in (ck_map["ck_outbox_events_dispatch_attempt_count_non_negative"])
    )

    # 4. claim_token and claimed_at strictly paired
    assert "ck_outbox_events_claim_token_and_claimed_at_pair" in ck_map
    pair_sql = ck_map["ck_outbox_events_claim_token_and_claimed_at_pair"]
    assert "claim_token IS NULL AND claimed_at IS NULL" in pair_sql
    assert "claim_token IS NOT NULL AND claimed_at IS NOT NULL" in pair_sql

    # 5. published rows have no active claim
    assert "ck_outbox_events_published_has_no_active_claim" in ck_map
    pub_sql = ck_map["ck_outbox_events_published_has_no_active_claim"]
    assert "published_at IS NULL" in pub_sql
    assert "claim_token IS NULL AND claimed_at IS NULL" in pub_sql


def test_dispatch_ready_partial_index() -> None:
    """Verify partial index ix_outbox_events_dispatch_ready on dispatch columns."""

    table = OutboxEventRecord.__table__
    indexes = {idx.name: idx for idx in table.indexes}

    # Old pending index must NOT be present
    assert "ix_outbox_events_pending_created_at" not in indexes

    # Dispatch-ready partial index must be present
    assert "ix_outbox_events_dispatch_ready" in indexes
    idx = indexes["ix_outbox_events_dispatch_ready"]
    assert [c.name for c in idx.columns] == ["next_attempt_at", "created_at", "event_id"]
    assert idx.unique is False

    # Check postgresql_where predicate
    where_sql = str(idx.dialect_options["postgresql"]["where"])
    assert "published_at IS NULL" in where_sql


def test_no_forbidden_dispatch_or_transport_fields() -> None:
    """Verify table does NOT contain raw error messages, worker identities, or transport fields."""
    forbidden = {
        "last_error_message",
        "last_error",
        "raw_exception",
        "broker_response",
        "error_message",
        "error_detail",
        "routing_key",
        "exchange",
        "queue",
        "worker_hostname",
        "hostname",
        "process_id",
        "pid",
        "retry_reason",
        "lease_expires_at",
        "locked_by",
        "locked_until",
        "status",
        "outbox_id",
    }
    actual_columns = set(OutboxEventRecord.__table__.columns.keys())
    assert actual_columns.isdisjoint(forbidden)


def test_base_metadata_remains_strictly_canonical_tables() -> None:
    """Verify Base.metadata contains canonical tables."""
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
