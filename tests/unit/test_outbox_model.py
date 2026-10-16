"""Unit tests for OutboxEventRecord model and schema registration.

Part of Phase 1 Foundation Task 1G.2.
Validates:
- Table registration: outbox_events registered in Base.metadata.
- Metadata scope: Base.metadata contains exactly {"audit_events", "outbox_events"}.
- Primary key: event_id is canonical UUID primary key; zero secondary outbox_id exists.
- Column types: envelope is JSONB, created_at is timezone-aware DateTime with server_default,
  published_at is nullable timezone-aware DateTime.
- Constraints: event_id matches envelope->>'event_id', envelope is object type.
- Partial index: ix_outbox_events_pending_created_at on created_at WHERE published_at IS NULL.
- Transport isolation: zero RabbitMQ columns on the model.
"""

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

import app.db.models  # noqa: F401
from app.db.base import Base
from app.events.outbox.models import OutboxEventRecord


def test_outbox_table_registered_in_metadata() -> None:
    """Verify outbox_events is registered in Base.metadata and matches OutboxEventRecord."""
    assert "outbox_events" in Base.metadata.tables
    table = Base.metadata.tables["outbox_events"]
    assert table is OutboxEventRecord.__table__


def test_base_metadata_contains_only_audit_and_outbox_events() -> None:
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
        # Phase 7 Calendar Watch (1)
        "calendar_watch_channels",
        # Phase 8D Service Credentials Persistence (1)
        "service_api_credentials",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26


def test_outbox_columns_and_types() -> None:
    """Verify canonical columns, types, nullabilities, and primary key on outbox_events."""
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

    # Verify primary key is strictly event_id
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["event_id"]
    assert isinstance(table.c.event_id.type, UUID)

    # Negative check: no secondary outbox_id
    assert "outbox_id" not in table.columns
    assert "id" not in table.columns

    # Envelope must be PostgreSQL JSONB and not nullable
    assert isinstance(table.c.envelope.type, JSONB)
    assert table.c.envelope.nullable is False

    # created_at must be TIMESTAMPTZ, not nullable, with server default
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.nullable is False
    assert table.c.created_at.server_default is not None

    # published_at must be TIMESTAMPTZ and nullable
    assert table.c.published_at.type.timezone is True
    assert table.c.published_at.nullable is True

    # dispatch_attempt_count must be Integer, not nullable, with server default 0
    assert table.c.dispatch_attempt_count.nullable is False
    assert table.c.dispatch_attempt_count.server_default is not None

    # next_attempt_at must be TIMESTAMPTZ, not nullable, with server default
    assert table.c.next_attempt_at.type.timezone is True
    assert table.c.next_attempt_at.nullable is False
    assert table.c.next_attempt_at.server_default is not None

    # claim_token must be UUID and nullable
    assert isinstance(table.c.claim_token.type, UUID)
    assert table.c.claim_token.nullable is True

    # claimed_at must be TIMESTAMPTZ and nullable
    assert table.c.claimed_at.type.timezone is True
    assert table.c.claimed_at.nullable is True


def test_outbox_check_constraints() -> None:
    """Verify named CheckConstraints for outbox consistency and dispatch invariants."""
    table = OutboxEventRecord.__table__
    ck_map = {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}

    # event_id consistency constraint (NULL-safe)
    assert "ck_outbox_events_event_id_matches_envelope" in ck_map
    sql_event_id = ck_map["ck_outbox_events_event_id_matches_envelope"]
    assert "COALESCE" in sql_event_id
    assert "FALSE" in sql_event_id
    assert "envelope ->> 'event_id' = event_id::text" in sql_event_id
    assert sql_event_id == "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)"

    # envelope is object constraint
    assert "ck_outbox_events_envelope_is_object" in ck_map
    sql_type = ck_map["ck_outbox_events_envelope_is_object"]
    assert "jsonb_typeof(envelope) = 'object'" in sql_type

    # dispatch_attempt_count >= 0 constraint
    assert "ck_outbox_events_dispatch_attempt_count_non_negative" in ck_map
    assert (
        "dispatch_attempt_count >= 0"
        in (ck_map["ck_outbox_events_dispatch_attempt_count_non_negative"])
    )

    # claim pair constraint (both NULL or both NOT NULL)
    assert "ck_outbox_events_claim_token_and_claimed_at_pair" in ck_map
    sql_claim_pair = ck_map["ck_outbox_events_claim_token_and_claimed_at_pair"]
    assert "claim_token IS NULL AND claimed_at IS NULL" in sql_claim_pair
    assert "claim_token IS NOT NULL AND claimed_at IS NOT NULL" in sql_claim_pair

    # published has no active claim constraint
    assert "ck_outbox_events_published_has_no_active_claim" in ck_map
    sql_pub_claim = ck_map["ck_outbox_events_published_has_no_active_claim"]
    assert "published_at IS NULL" in sql_pub_claim
    assert "claim_token IS NULL AND claimed_at IS NULL" in sql_pub_claim


def test_outbox_event_id_constraint_null_safety_structural_guarantee() -> None:
    """Verify structurally that missing or null envelope event_id cannot satisfy the CHECK.

    Because PostgreSQL CHECK constraints do not reject NULL/UNKNOWN results,
    equality alone is insufficient. The constraint definition must structurally
    guarantee non-null boolean evaluation:
    - If envelope ->> 'event_id' is NULL (key missing or JSON null):
      NULL = event_id::text -> NULL
      COALESCE(NULL, FALSE) -> FALSE (CHECK fails / row rejected)
    - If envelope ->> 'event_id' is a mismatched string:
      mismatched = event_id::text -> FALSE
      COALESCE(FALSE, FALSE) -> FALSE (CHECK fails / row rejected)
    - If envelope ->> 'event_id' equals event_id::text:
      matched = event_id::text -> TRUE
      COALESCE(TRUE, FALSE) -> TRUE (CHECK passes / row accepted)
    """
    table = OutboxEventRecord.__table__
    ck = next(
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_outbox_events_event_id_matches_envelope"
    )
    raw_sql = str(ck.sqltext)

    # Must explicitly match the hardened NULL-safe expression
    assert raw_sql == "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)"
    # Negative assertion: cannot be raw un-coalesced equality
    assert raw_sql != "envelope ->> 'event_id' = event_id::text"


def test_outbox_partial_dispatch_ready_index() -> None:
    """Verify ix_outbox_events_dispatch_ready partial index replaces old pending index."""
    table = OutboxEventRecord.__table__
    indexes = {idx.name: idx for idx in table.indexes}

    # Old index superseded and replaced
    assert "ix_outbox_events_pending_created_at" not in indexes

    # Dispatch-ready index on (next_attempt_at, created_at, event_id) WHERE published_at IS NULL
    assert "ix_outbox_events_dispatch_ready" in indexes
    idx = indexes["ix_outbox_events_dispatch_ready"]
    assert [c.name for c in idx.columns] == ["next_attempt_at", "created_at", "event_id"]
    assert idx.unique is False

    # Check postgresql_where clause
    assert idx.dialect_options["postgresql"]["where"] is not None
    where_sql = str(idx.dialect_options["postgresql"]["where"])
    assert "published_at IS NULL" in where_sql


def test_outbox_contains_no_rabbitmq_transport_fields() -> None:
    """Verify outbox_events model contains zero RabbitMQ/broker or raw failure fields."""
    forbidden_transport_fields = {
        "exchange",
        "queue",
        "routing_key",
        "delivery_tag",
        "consumer_tag",
        "redelivered",
        "broker",
        "channel",
        "broker_message_id",
        "last_error_message",
        "raw_exception",
        "broker_response",
        "worker_hostname",
        "process_id",
        "retry_reason",
        "lease_expires_at",
        "locked_by",
    }
    table_columns = set(OutboxEventRecord.__table__.columns.keys())
    assert table_columns.isdisjoint(forbidden_transport_fields)
