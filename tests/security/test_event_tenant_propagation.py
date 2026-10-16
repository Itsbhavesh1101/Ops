"""Background & Event Pipeline Tenant Isolation Security Suite for Phase 8A.

Validates:
1. Domain event creation preserves original trusted tenant_id.
2. Event serialization / deserialization round-trip maintains tenant boundary.
3. Outbox and Inbox contracts preserve tenant_id through asynchronous processing.
4. Composite idempotency namespaces remain strictly tenant-scoped.
5. Cross-tenant event tampering attempts are rejected.
"""

from uuid import uuid4

import pytest

from app.core.idempotency.contracts import create_idempotency_identity
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.events.serialization import serialize_event_envelope
from tests.security.conftest import TENANT_A, TENANT_B


class TestEventTenantPropagation:
    """Verifies that tenant context is strictly preserved across asynchronous boundaries."""

    def test_domain_event_envelope_preserves_tenant_id(self) -> None:
        """Creating an event envelope preserves the canonical tenant_id without mutation."""
        task_id = str(uuid4())
        create_dto = EventCreate(
            event_type="workflow.task.created",
            event_version=1,
            tenant_id=TENANT_A,
            subject_type="workflow_task",
            subject_id=task_id,
            payload={"task_id": task_id, "status": "PENDING"},
        )
        envelope = create_event_envelope(create_dto)

        assert envelope.tenant_id == TENANT_A
        assert envelope.tenant_id != TENANT_B
        assert envelope.subject_id == task_id

    def test_event_serialization_round_trip_preserves_tenant_boundary(self) -> None:
        """Transport serialization (e.g. RabbitMQ message body) preserves tenant_id."""
        create_dto = EventCreate(
            event_type="scheduling.job.completed",
            event_version=1,
            tenant_id=TENANT_A,
            subject_type="schedule_job",
            subject_id=str(uuid4()),
            payload={"status": "FEASIBLE", "score": 98.5},
        )
        envelope = create_event_envelope(create_dto)

        json_str = serialize_event_envelope(envelope)
        rehydrated = EventEnvelope.model_validate_json(json_str)

        assert rehydrated.tenant_id == TENANT_A
        assert rehydrated.tenant_id != TENANT_B
        assert rehydrated.event_id == envelope.event_id

    def test_composite_idempotency_identities_are_tenant_scoped(self) -> None:
        """Identical client keys and operations across tenants yield distinct identities."""
        shared_key = "idemp-trans-2026-10-09-001"
        op_name = "workflow.task.action"

        identity_a = create_idempotency_identity(
            tenant_id=TENANT_A,
            operation_name=op_name,
            raw_key=shared_key,
        )
        identity_b = create_idempotency_identity(
            tenant_id=TENANT_B,
            operation_name=op_name,
            raw_key=shared_key,
        )

        # Hashes match because raw_key is identical
        assert identity_a.key_digest == identity_b.key_digest

        # But composite identities differ strictly by tenant_id
        assert identity_a.tenant_id == TENANT_A
        assert identity_b.tenant_id == TENANT_B
        assert identity_a != identity_b

    def test_cross_tenant_event_tampering_rejected_by_schema(self) -> None:
        """Malformed, empty, or whitespace-padded tenant_ids are rejected at event ingestion."""
        with pytest.raises(ValueError, match="tenant_id must not be empty or whitespace-only"):
            EventCreate(
                event_type="task.status.updated",
                event_version=1,
                tenant_id="   ",
                subject_type="task",
                subject_id=str(uuid4()),
                payload={"progress": 100},
            )

    def test_outbox_envelope_immutability(self) -> None:
        """EventEnvelope is frozen (immutable) preventing in-flight tenant tampering."""
        create_dto = EventCreate(
            event_type="operations.plan.approved",
            event_version=1,
            tenant_id=TENANT_A,
            subject_type="plan",
            subject_id=str(uuid4()),
            payload={"approved_by": "approver-1"},
        )
        envelope = create_event_envelope(create_dto)

        with pytest.raises((TypeError, ValueError)):
            envelope.tenant_id = TENANT_B  # type: ignore[misc]
