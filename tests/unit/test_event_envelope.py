"""Unit tests for canonical versioned event envelope contract.

Part of Phase 1 Foundation Task 1G.1.
Validates:
- EventCreate schema: event_type namespaced regex, event_version bounds, immutability,
  forbidding caller-supplied event_id and occurred_at.
- create_event_envelope factory: unconditional uuid4 generation, distinct IDs,
  timezone-aware UTC occurred_at, clock injection, defensive payload mutation isolation.
- Correlation, causation, and subject pair consistency: correlation_id sharing,
  causation_id preservation, both-or-none subject pairing.
- Payload defensive validation: JSON primitive strictness, non-finite float rejection
  (NaN/+Inf/-Inf), recursive sensitive key denylist, technical size/depth bounds.
- Deterministic serialization: UTF-8 JSON formatting, UUID/ISO-8601 formatting, zero mutation.
- Verified Phase-0 source event evidence: task.blocked, workflow.task.created, workflow.sla.warning,
  schedule.published, plus extensible future namespaced event types.
- Persistence isolation: zero database tables/models created (Base.metadata only contains
  audit_events), migration chain remains 0002 -> 0001 with no 0003.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from app.db.base import Base
from app.events.schemas import (
    EVENT_TYPE_REGEX,
    EventCreate,
    EventEnvelope,
    create_event_envelope,
)
from app.events.serialization import serialize_event_envelope
from app.events.validation import (
    MAX_EVENT_PAYLOAD_BYTES,
    MAX_EVENT_PAYLOAD_DEPTH,
    MAX_EVENT_STRING_LENGTH,
    freeze_payload,
    normalize_event_key,
    thaw_payload,
    validate_event_payload,
)

# ==============================================================================
# 1. EventCreate Input Contract Tests
# ==============================================================================


def test_event_create_valid_minimal() -> None:
    """Verify minimum valid EventCreate with default version and empty payload."""
    event = EventCreate(event_type="workflow.task.created")
    assert event.event_type == "workflow.task.created"
    assert event.event_version == 1
    assert event.tenant_id is None
    assert event.correlation_id is None
    assert event.causation_id is None
    assert event.subject_type is None
    assert event.subject_id is None
    assert event.payload == {}


def test_event_create_version_validation() -> None:
    """Verify event_version must be an integer >= 1."""
    event = EventCreate(event_type="task.blocked", event_version=2)
    assert event.event_version == 2

    with pytest.raises(ValidationError):
        EventCreate(event_type="task.blocked", event_version=0)

    with pytest.raises(ValidationError):
        EventCreate(event_type="task.blocked", event_version=-1)

    with pytest.raises(ValidationError):
        EventCreate(event_type="task.blocked", event_version="v1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "valid_event_type",
    [
        "task.blocked",
        "workflow.task.created",
        "workflow.sla.warning",
        "schedule.published",
        "operations.schedule.run.requested",
        "academic.term.section.assigned",
        "system.node.maintenance_window.opened",
    ],
)
def test_event_create_accepts_namespaced_event_types(valid_event_type: str) -> None:
    """Verify source-verified E-8 examples and future namespaced event types are accepted."""
    event = EventCreate(event_type=valid_event_type)
    assert event.event_type == valid_event_type
    assert EVENT_TYPE_REGEX.match(valid_event_type) is not None


@pytest.mark.parametrize(
    "invalid_event_type",
    [
        "",
        "   ",
        "singleword",
        "Task.Blocked",
        "workflow..task",
        ".workflow.task",
        "workflow.task.",
        "workflow task created",
        "workflow-task-created",
        "123.workflow.task",
        "workflow.task.created@",
        "a" * 129,
    ],
)
def test_event_create_rejects_malformed_event_types(invalid_event_type: str) -> None:
    """Verify uppercase, blank, single-segment, symbol, and invalid event types are rejected."""
    with pytest.raises(ValidationError):
        EventCreate(event_type=invalid_event_type)


def test_event_create_omits_event_id_and_occurred_at() -> None:
    """Verify EventCreate has no event_id or occurred_at, and caller supply is rejected."""
    assert "event_id" not in EventCreate.model_fields
    assert "occurred_at" not in EventCreate.model_fields

    with pytest.raises(ValidationError):
        EventCreate(event_type="task.blocked", event_id=uuid4())  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        EventCreate(
            event_type="task.blocked",
            occurred_at=datetime.now(UTC),  # type: ignore[call-arg]
        )


def test_event_create_is_immutable_and_forbids_extra_fields() -> None:
    """Verify EventCreate is frozen and rejects mutation and unknown fields."""
    event = EventCreate(event_type="task.blocked")

    with pytest.raises(ValidationError):
        event.event_type = "task.unblocked"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        EventCreate(event_type="task.blocked", unknown_extra_field="rejected")  # type: ignore[call-arg]


# ==============================================================================
# 2. Canonical Factory & Generated Metadata Tests
# ==============================================================================


def test_create_event_envelope_generates_uuid_and_utc_timestamp() -> None:
    """Verify create_event_envelope generates a UUID and timezone-aware UTC datetime."""
    event = EventCreate(
        event_type="workflow.task.created",
        tenant_id="tenant-alpha",
    )
    envelope = create_event_envelope(event)

    assert isinstance(envelope.event_id, UUID)
    assert envelope.event_type == "workflow.task.created"
    assert envelope.event_version == 1
    assert envelope.tenant_id == "tenant-alpha"
    assert isinstance(envelope.occurred_at, datetime)
    assert envelope.occurred_at.tzinfo is not None
    assert envelope.occurred_at.utcoffset() == timedelta(0)


def test_create_event_envelope_generates_distinct_uuids() -> None:
    """Verify separate calls generate distinct event_id UUIDs for identical inputs."""
    event1 = EventCreate(event_type="schedule.published")
    event2 = EventCreate(event_type="schedule.published")

    envelope1 = create_event_envelope(event1)
    envelope2 = create_event_envelope(event2)

    assert envelope1.event_id != envelope2.event_id


def test_create_event_envelope_uses_injectable_clock() -> None:
    """Verify clock callable is injectable and normalized to UTC."""
    fixed_time = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    expected_utc = fixed_time.astimezone(UTC)

    event = EventCreate(event_type="workflow.sla.warning")
    envelope = create_event_envelope(event, clock=lambda: fixed_time)

    assert envelope.occurred_at == expected_utc
    assert envelope.occurred_at.tzinfo == UTC


def test_create_event_envelope_rejects_naive_clock() -> None:
    """Verify clock producing naive datetime raises ValueError."""
    naive_time = datetime(2026, 9, 26, 12, 0, 0)
    event = EventCreate(event_type="task.blocked")

    with pytest.raises(ValueError, match="timezone-aware"):
        create_event_envelope(event, clock=lambda: naive_time)


# ==============================================================================
# 3. Correlation, Causation, and Subject Semantics Tests
# ==============================================================================


def test_correlation_id_is_optional_and_shareable() -> None:
    """Verify correlation_id is optional and multiple envelopes may share the same ID."""
    corr_id = "corr-req-12345"
    e1 = create_event_envelope(
        EventCreate(event_type="workflow.task.created", correlation_id=corr_id)
    )
    e2 = create_event_envelope(
        EventCreate(event_type="workflow.sla.warning", correlation_id=corr_id)
    )

    assert e1.correlation_id == corr_id
    assert e2.correlation_id == corr_id
    assert e1.event_id != e2.event_id


def test_causation_id_semantics() -> None:
    """Verify causation_id preserves causal event reference (A -> B -> C)."""
    env_a = create_event_envelope(EventCreate(event_type="workflow.task.created"))
    env_b = create_event_envelope(
        EventCreate(event_type="task.blocked", causation_id=env_a.event_id)
    )
    env_c = create_event_envelope(
        EventCreate(event_type="workflow.sla.warning", causation_id=env_b.event_id)
    )

    assert env_b.causation_id == env_a.event_id
    assert env_c.causation_id == env_b.event_id
    assert env_b.causation_id != env_b.event_id


def test_subject_pairing_validation() -> None:
    """Verify subject_type and subject_id enforce a strict both-or-none pairing invariant."""
    # Both absent: valid
    e_none = EventCreate(event_type="system.node.started")
    assert e_none.subject_type is None
    assert e_none.subject_id is None

    # Both present: valid
    e_both = EventCreate(
        event_type="task.blocked",
        subject_type="operations.task",
        subject_id="task-789",
    )
    assert e_both.subject_type == "operations.task"
    assert e_both.subject_id == "task-789"

    # Type without ID: rejected
    with pytest.raises(ValidationError, match="both be provided or both be None"):
        EventCreate(event_type="task.blocked", subject_type="operations.task")

    # ID without type: rejected
    with pytest.raises(ValidationError, match="both be provided or both be None"):
        EventCreate(event_type="task.blocked", subject_id="task-789")


@pytest.mark.parametrize(
    "field_name,invalid_val",
    [
        ("tenant_id", "   "),
        ("tenant_id", "t" * 256),
        ("correlation_id", "   "),
        ("correlation_id", "c" * 129),
        ("subject_type", "   "),
        ("subject_type", "s" * 129),
        ("subject_type", "https://example.com/item"),
        ("subject_id", "   "),
        ("subject_id", "i" * 256),
    ],
)
def test_string_field_bounds_and_whitespace_rejection(field_name: str, invalid_val: str) -> None:
    """Verify whitespace-only or oversized bounded string fields are rejected."""
    kwargs: dict[str, Any] = {"event_type": "task.blocked", field_name: invalid_val}
    if field_name == "subject_type":
        kwargs["subject_id"] = "valid-id"
    elif field_name == "subject_id":
        kwargs["subject_type"] = "valid.type"

    with pytest.raises(ValidationError):
        EventCreate(**kwargs)


# ==============================================================================
# 4. Payload Validation, Types, and Security Tests
# ==============================================================================


def test_normalize_event_key_variants() -> None:
    """Verify key normalization strips case, hyphens, underscores, and spaces."""
    assert normalize_event_key("PRIVATE_KEY") == "privatekey"
    assert normalize_event_key("private-key") == "privatekey"
    assert normalize_event_key("Private Key") == "privatekey"
    assert normalize_event_key("API_KEY") == "apikey"
    assert normalize_event_key("Client-Secret") == "clientsecret"


def test_payload_accepts_safe_primitives_and_nested() -> None:
    """Verify safe JSON primitives (null, bool, int, finite float, str, list, dict) are accepted."""
    safe_payload = {
        "task_name": "generate_timetable",
        "attempt_count": 3,
        "is_retry": True,
        "completion_ratio": 0.85,
        "items": ["slot_1", "slot_2"],
        "metadata": {"region": "us-east", "worker_id": 42, "notes": None},
    }
    validated = validate_event_payload(safe_payload)
    assert thaw_payload(validated) == safe_payload
    assert isinstance(validated, MappingProxyType)
    assert isinstance(validated["items"], tuple)
    assert validated["items"] == ("slot_1", "slot_2")
    assert isinstance(validated["metadata"], MappingProxyType)
    assert validated["metadata"]["region"] == "us-east"


def test_freeze_payload_direct_invariants() -> None:
    """Verify freeze_payload converts dict to MappingProxyType and list to tuple recursively."""
    raw = {"a": [1, {"b": 2}], "scalar": 42}
    frozen = freeze_payload(raw)
    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["a"], tuple)
    assert isinstance(frozen["a"][1], MappingProxyType)
    assert frozen["a"][1]["b"] == 2
    assert frozen["scalar"] == 42
    assert thaw_payload(frozen) == raw


def test_payload_rejects_non_dict_root() -> None:
    """Verify root payload must be a dictionary/mapping."""
    with pytest.raises(ValueError, match="root must be a dictionary"):
        validate_event_payload(["list", "root", "invalid"])

    with pytest.raises(ValueError, match="root must be a dictionary"):
        validate_event_payload("string_root_invalid")


def test_payload_rejects_non_finite_floats_recursively() -> None:
    """Verify NaN, +Inf, -Inf are rejected at root, nested dicts, and lists."""
    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_event_payload({"ratio": float("nan")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_event_payload({"ratio": float("inf")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_event_payload({"ratio": float("-inf")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_event_payload({"nested": {"ratio": float("nan")}})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_event_payload({"series": [1.0, 2.0, float("inf")]})


def test_payload_rejects_non_string_dict_keys() -> None:
    """Verify dictionary keys that are not strings are rejected."""
    with pytest.raises(ValueError, match="keys must be strings"):
        validate_event_payload({123: "numeric_key_prohibited"})


def test_payload_rejects_unsupported_python_types() -> None:
    """Verify sets, exceptions, and arbitrary objects are rejected."""

    class CustomObj:
        pass

    with pytest.raises(ValueError, match="Unsupported value type 'CustomObj'"):
        validate_event_payload({"obj": CustomObj()})

    with pytest.raises(ValueError, match="Unsupported value type 'set'"):
        validate_event_payload({"tags": {"set_not_allowed"}})

    with pytest.raises(ValueError, match="Unsupported value type 'RuntimeError'"):
        validate_event_payload({"err": RuntimeError("failure")})


def test_payload_rejects_excessive_depth() -> None:
    """Verify nesting exceeding MAX_EVENT_PAYLOAD_DEPTH is rejected."""
    deep: dict[str, Any] = {"level": 1}
    curr = deep
    for i in range(2, MAX_EVENT_PAYLOAD_DEPTH + 3):
        curr["child"] = {"level": i}
        curr = curr["child"]

    with pytest.raises(ValueError, match="nesting depth exceeds maximum limit"):
        validate_event_payload(deep)


def test_payload_rejects_oversized_string() -> None:
    """Verify individual string values exceeding MAX_EVENT_STRING_LENGTH are rejected."""
    payload = {"huge_str": "x" * (MAX_EVENT_STRING_LENGTH + 1)}
    with pytest.raises(ValueError, match="string value exceeds maximum length"):
        validate_event_payload(payload)


def test_payload_rejects_oversized_serialized_payload() -> None:
    """Verify total serialized payload exceeding MAX_EVENT_PAYLOAD_BYTES is rejected."""
    large_payload = {f"k_{i}": "a" * 5000 for i in range(60)}
    with pytest.raises(
        ValueError,
        match=f"Serialized event payload size.*exceeds limit of {MAX_EVENT_PAYLOAD_BYTES} bytes",
    ):
        validate_event_payload(large_payload)


@pytest.mark.parametrize(
    "denied_key",
    [
        "password",
        "PASSWORD",
        "pass_word",
        "passwd",
        "secret",
        "client_secret",
        "CLIENT-SECRET",
        "api_key",
        "ApiKey",
        "API-KEY",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "cookie",
        "private_key",
        "PrivateKey",
        "PRIVATE-KEY",
    ],
)
def test_payload_rejects_sensitive_keys_recursively(denied_key: str) -> None:
    """Verify sensitive credential keys are rejected at root and nested structures."""
    # Root key
    with pytest.raises(ValueError, match="Suspicious sensitive key"):
        validate_event_payload({denied_key: "synthetic_marker"})

    # Nested in dict
    with pytest.raises(ValueError, match="Suspicious sensitive key"):
        validate_event_payload({"context": {denied_key: "synthetic_marker"}})

    # Nested in list
    with pytest.raises(ValueError, match="Suspicious sensitive key"):
        validate_event_payload({"items": [{"header": "ok"}, {denied_key: "synthetic_marker"}]})


# ==============================================================================
# 5. Payload Mutation Isolation Test
# ==============================================================================


def test_payload_mutation_isolation() -> None:
    """Section 35 & Task 1G.1A: Verify mutating source payload does not alter EventEnvelope."""
    source_payload: dict[str, Any] = {
        "status": "pending",
        "details": {"retry_count": 0, "tags": ["alpha", "beta"]},
    }

    event = EventCreate(event_type="workflow.task.created", payload=source_payload)
    envelope = create_event_envelope(event)

    # Mutate source payload at root, nested dict, and nested list
    source_payload["status"] = "tampered"
    source_payload["details"]["retry_count"] = 99
    source_payload["details"]["tags"].append("gamma")

    # Canonical envelope remains completely unchanged
    assert envelope.payload["status"] == "pending"
    assert envelope.payload["details"]["retry_count"] == 0
    assert envelope.payload["details"]["tags"] == ("alpha", "beta")
    assert isinstance(envelope.payload["details"]["tags"], tuple)


def test_event_create_top_level_payload_mutation_rejected() -> None:
    """Verify modifying, adding, or deleting top-level keys on EventCreate.payload fails."""
    event = EventCreate(
        event_type="task.blocked",
        payload={"task_id": "task-100", "state": "BLOCKED"},
    )

    with pytest.raises(TypeError, match="does not support item assignment"):
        event.payload["task_id"] = "task-200"

    with pytest.raises(TypeError, match="does not support item assignment"):
        event.payload["new_key"] = "illegal_injection"

    with pytest.raises(TypeError, match="does not support item deletion"):
        del event.payload["task_id"]

    assert not hasattr(event.payload, "pop")
    assert not hasattr(event.payload, "clear")


def test_event_create_nested_mapping_mutation_rejected() -> None:
    """Verify mutating nested dictionary/mapping in EventCreate.payload fails."""
    event = EventCreate(
        event_type="workflow.task.created",
        payload={"config": {"retry": 3, "timeout": 30}},
    )

    with pytest.raises(TypeError, match="does not support item assignment"):
        event.payload["config"]["retry"] = 5

    with pytest.raises(TypeError, match="does not support item assignment"):
        event.payload["config"]["injected"] = True

    with pytest.raises(TypeError, match="does not support item deletion"):
        del event.payload["config"]["retry"]


def test_event_create_nested_sequence_mutation_rejected() -> None:
    """Verify mutating nested list/sequence in EventCreate.payload fails."""
    event = EventCreate(
        event_type="schedule.published",
        payload={"slots": ["slot_a", "slot_b"]},
    )

    assert isinstance(event.payload["slots"], tuple)

    with pytest.raises(AttributeError, match="has no attribute 'append'"):
        event.payload["slots"].append("slot_c")  # type: ignore[attr-defined]

    with pytest.raises(AttributeError, match="has no attribute 'extend'"):
        event.payload["slots"].extend(["slot_c"])  # type: ignore[attr-defined]

    with pytest.raises(TypeError, match="does not support item assignment"):
        event.payload["slots"][0] = "mutated"  # type: ignore[index]

    with pytest.raises(TypeError, match="doesn't support item deletion"):
        del event.payload["slots"][0]


def test_event_envelope_top_level_payload_mutation_rejected() -> None:
    """Verify modifying, adding, or deleting top-level keys on EventEnvelope.payload fails."""
    event = EventCreate(
        event_type="task.blocked",
        payload={"task_id": "task-100", "state": "BLOCKED"},
    )
    envelope = create_event_envelope(event)

    with pytest.raises(TypeError, match="does not support item assignment"):
        envelope.payload["task_id"] = "task-200"

    with pytest.raises(TypeError, match="does not support item assignment"):
        envelope.payload["new_key"] = "illegal_injection"

    with pytest.raises(TypeError, match="does not support item deletion"):
        del envelope.payload["task_id"]

    assert not hasattr(envelope.payload, "pop")
    assert not hasattr(envelope.payload, "clear")


def test_event_envelope_nested_mapping_mutation_rejected() -> None:
    """Verify mutating nested dictionary/mapping in EventEnvelope.payload fails."""
    event = EventCreate(
        event_type="workflow.task.created",
        payload={"config": {"retry": 3, "timeout": 30}},
    )
    envelope = create_event_envelope(event)

    with pytest.raises(TypeError, match="does not support item assignment"):
        envelope.payload["config"]["retry"] = 5

    with pytest.raises(TypeError, match="does not support item assignment"):
        envelope.payload["config"]["injected"] = True

    with pytest.raises(TypeError, match="does not support item deletion"):
        del envelope.payload["config"]["retry"]


def test_event_envelope_nested_sequence_mutation_rejected() -> None:
    """Verify mutating nested list/sequence in EventEnvelope.payload fails."""
    event = EventCreate(
        event_type="schedule.published",
        payload={"slots": ["slot_a", "slot_b"]},
    )
    envelope = create_event_envelope(event)

    assert isinstance(envelope.payload["slots"], tuple)

    with pytest.raises(AttributeError, match="has no attribute 'append'"):
        envelope.payload["slots"].append("slot_c")  # type: ignore[attr-defined]

    with pytest.raises(TypeError, match="does not support item assignment"):
        envelope.payload["slots"][0] = "mutated"  # type: ignore[index]

    with pytest.raises(TypeError, match="doesn't support item deletion"):
        del envelope.payload["slots"][0]


def test_payload_security_post_creation_credential_injection_impossible() -> None:
    """Section 13: Verify post-creation credential injection is impossible on envelope."""
    event = EventCreate(
        event_type="workflow.sla.warning",
        payload={"sla_threshold_seconds": 3600, "context": {"tier": "gold", "tags": [1, 2]}},
    )
    envelope = create_event_envelope(event)

    # Attempt to inject credentials at root
    with pytest.raises(TypeError):
        envelope.payload["access_token"] = "stolen_token"

    with pytest.raises(TypeError):
        envelope.payload["password"] = "stolen_pass"

    # Attempt to inject credentials into nested mapping
    with pytest.raises(TypeError):
        envelope.payload["context"]["api_key"] = "stolen_key"

    with pytest.raises(TypeError):
        envelope.payload["context"]["client_secret"] = "stolen_secret"

    # Attempt to inject NaN into nested mapping or sequence
    with pytest.raises(TypeError):
        envelope.payload["context"]["nan_ratio"] = float("nan")

    with pytest.raises(AttributeError):
        envelope.payload["context"]["tags"].append(float("nan"))  # type: ignore[attr-defined]


def test_payload_source_reference_deep_isolation() -> None:
    """Section 14: Stronger test verifying deep source mutation cannot alter event or envelope."""
    source = {
        "status": "initial",
        "nested": {
            "items": [1, 2],
            "config": {"active": True},
        },
    }

    event = EventCreate(event_type="task.blocked", payload=source)
    envelope = create_event_envelope(event)

    # Mutate every level of the original source dictionary
    source["status"] = "mutated"
    source["nested"]["items"].append(3)
    source["nested"]["config"]["active"] = False
    source["nested"]["other"] = "changed"

    # EventCreate payload is completely unchanged
    assert event.payload["status"] == "initial"
    assert event.payload["nested"]["items"] == (1, 2)
    assert event.payload["nested"]["config"]["active"] is True
    assert "other" not in event.payload["nested"]

    # EventEnvelope payload is completely unchanged
    assert envelope.payload["status"] == "initial"
    assert envelope.payload["nested"]["items"] == (1, 2)
    assert envelope.payload["nested"]["config"]["active"] is True
    assert "other" not in envelope.payload["nested"]


def test_payload_model_dump_and_serialization_thaw_roundtrip() -> None:
    """Section 15: Verify model_dump and serialize_event_envelope produce JSON objects/arrays."""
    source_payload = {
        "task_name": "schedule_solver",
        "parameters": {"max_iter": 100, "tolerance": 0.001, "enabled": True},
        "stages": ["init", "solve", "verify"],
        "metadata": None,
    }

    event = EventCreate(event_type="schedule.published", payload=source_payload)
    envelope = create_event_envelope(event)

    # Test model_dump(mode="json") produces plain dicts and lists
    json_dump = envelope.model_dump(mode="json")
    assert isinstance(json_dump["payload"], dict)
    assert isinstance(json_dump["payload"]["parameters"], dict)
    assert isinstance(json_dump["payload"]["stages"], list)
    assert json_dump["payload"]["stages"] == ["init", "solve", "verify"]

    # Test serialize_event_envelope produces standard JSON string
    serialized = serialize_event_envelope(envelope)
    assert isinstance(serialized, str)

    parsed = json.loads(serialized)
    assert parsed["payload"] == source_payload
    assert parsed["payload"]["stages"] == ["init", "solve", "verify"]
    assert parsed["payload"]["parameters"]["tolerance"] == 0.001
    assert parsed["payload"]["parameters"]["enabled"] is True
    assert parsed["event_id"] == str(envelope.event_id)


# ==============================================================================
# 6. Serialization Boundary Tests
# ==============================================================================


def test_serialize_event_envelope_is_deterministic_and_valid_json() -> None:
    """Verify serialize_event_envelope formats UUIDs, UTC ISO datetimes, and sorted JSON."""
    event_id = uuid4()
    causation_id = uuid4()
    fixed_time = datetime(2026, 9, 26, 1, 30, 0, tzinfo=UTC)

    envelope = EventEnvelope(
        event_id=event_id,
        event_type="schedule.published",
        event_version=1,
        occurred_at=fixed_time,
        tenant_id="tenant-123",
        correlation_id="corr-xyz",
        causation_id=causation_id,
        subject_type="schedule.term",
        subject_id="term-2026-fall",
        payload={"rooms": 12, "active": True},
    )

    serialized = serialize_event_envelope(envelope)

    assert isinstance(serialized, str)
    assert str(event_id) in serialized
    assert str(causation_id) in serialized
    assert "2026-09-26T01:30:00Z" in serialized or "2026-09-26T01:30:00+00:00" in serialized
    assert '"schedule.published"' in serialized

    # Verify zero mutation of source envelope
    assert envelope.event_id == event_id
    assert envelope.event_type == "schedule.published"


def test_event_envelope_contains_no_rabbitmq_transport_fields() -> None:
    """Verify EventEnvelope defines no RabbitMQ transport details."""
    forbidden_transport_fields = {
        "exchange",
        "queue",
        "delivery_tag",
        "consumer_tag",
        "redelivered",
        "routing_key",
        "channel",
        "broker",
        "retry_count",
    }
    envelope_fields = set(EventEnvelope.model_fields.keys())
    assert envelope_fields.isdisjoint(forbidden_transport_fields)


# ==============================================================================
# 7. Persistence & Migration Isolation Tests
# ==============================================================================


def test_no_database_persistence_tables_created() -> None:
    """Verify Base.metadata contains canonical foundation tables and canonical Phase 2A tables."""
    import app.audit.models  # noqa: F401
    import app.core.idempotency.models  # noqa: F401
    import app.core.jobs.models  # noqa: F401
    import app.events.inbox.models  # noqa: F401
    import app.events.outbox.models  # noqa: F401

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


def test_migration_chain_is_0007_with_no_0008() -> None:
    """Verify Alembic migration head is 0012 for Phase 4B and no 0013 migration exists."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head == "0012"

    versions_dir = Path("app/db/migrations/versions")
    version_files = [f.name for f in versions_dir.glob("*.py")]
    assert any(f.startswith("0001") for f in version_files)
    assert any(f.startswith("0002") for f in version_files)
    assert any(f.startswith("0003") for f in version_files)
    assert any(f.startswith("0004") for f in version_files)
    assert any(f.startswith("0005") for f in version_files)
    assert any(f.startswith("0006") for f in version_files)
    assert any(f.startswith("0007") for f in version_files)
    assert any(f.startswith("0008") for f in version_files)
    assert any(f.startswith("0009") for f in version_files)
    assert any(f.startswith("0010") for f in version_files)
    assert any(f.startswith("0011") for f in version_files)
    assert any(f.startswith("0012") for f in version_files)
    assert not any(f.startswith("0013") for f in version_files)
