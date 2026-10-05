"""Unit tests for OutboxRepository persistence contract and same-UoW transaction ownership.

Part of Phase 1 Foundation Task 1G.2.
Validates:
- Enqueue accepts EventEnvelope and returns canonical event_id (UUID) unchanged.
- Complete canonical envelope stored in JSON-native format.
- Staged record has published_at = None.
- session.add called exactly once; flush/commit/rollback/close never called by repository.
- Same-UoW transaction contract: OutboxRepository and AuditRepository share the exact same session.
- Round-trip reconstruction: EventEnvelope.model_validate(record.envelope) succeeds.
- Invalid input types rejected and exceptions propagate without being swallowed.
"""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.events.outbox.models import OutboxEventRecord
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.events.validation import thaw_payload


def _create_sample_envelope() -> EventEnvelope:
    """Helper to create a fully populated sample EventEnvelope."""
    event_input = EventCreate(
        event_type="workflow.task.created",
        event_version=2,
        tenant_id="tenant-alpha-42",
        correlation_id="corr-req-999",
        causation_id=uuid4(),
        subject_type="workflow.instance",
        subject_id="wf-inst-12345",
        payload={
            "task_name": "timetable_solver",
            "priority": "HIGH",
            "items": ["slot_a", "slot_b"],
            "config": {"max_retries": 3, "enabled": True},
        },
    )
    return create_event_envelope(event_input)


@pytest.mark.asyncio
async def test_outbox_enqueue_success_and_invariants() -> None:
    """Verify enqueue stages record, copies event_id, and preserves session boundaries."""
    mock_session = MagicMock(spec=AsyncSession)
    repo = OutboxRepository(mock_session)

    envelope = _create_sample_envelope()
    staged_id = await repo.enqueue(envelope)

    # 1. Returned ID is identical to canonical event_id
    assert staged_id == envelope.event_id
    assert isinstance(staged_id, UUID)

    # 2. session.add called exactly once with OutboxEventRecord
    assert mock_session.add.call_count == 1
    added_record = mock_session.add.call_args[0][0]
    assert isinstance(added_record, OutboxEventRecord)

    # 3. Record fields verification
    assert added_record.event_id == envelope.event_id
    assert added_record.published_at is None
    assert isinstance(added_record.envelope, dict)
    assert added_record.envelope["event_id"] == str(envelope.event_id)
    assert added_record.envelope["event_type"] == envelope.event_type
    assert added_record.envelope["event_version"] == envelope.event_version
    assert added_record.envelope["tenant_id"] == envelope.tenant_id
    assert added_record.envelope["correlation_id"] == envelope.correlation_id
    assert added_record.envelope["causation_id"] == str(envelope.causation_id)
    assert added_record.envelope["subject_type"] == envelope.subject_type
    assert added_record.envelope["subject_id"] == envelope.subject_id

    # 4. Payload in envelope is JSON-native (lists, not tuples)
    assert isinstance(added_record.envelope["payload"]["items"], list)
    assert added_record.envelope["payload"]["items"] == ["slot_a", "slot_b"]

    # 5. Repository boundary invariants: no flush, commit, rollback, or close
    assert mock_session.flush.call_count == 0
    assert mock_session.commit.call_count == 0
    assert mock_session.rollback.call_count == 0
    assert mock_session.close.call_count == 0


@pytest.mark.asyncio
async def test_outbox_enqueue_roundtrip_reconstruction() -> None:
    """Verify stored JSON envelope can reconstruct an equivalent canonical EventEnvelope."""
    mock_session = MagicMock(spec=AsyncSession)
    repo = OutboxRepository(mock_session)

    original_envelope = _create_sample_envelope()
    await repo.enqueue(original_envelope)

    added_record = mock_session.add.call_args[0][0]
    stored_json = added_record.envelope

    # Reconstruct EventEnvelope from stored JSON
    reconstructed = EventEnvelope.model_validate(stored_json)

    assert reconstructed.event_id == original_envelope.event_id
    assert reconstructed.event_type == original_envelope.event_type
    assert reconstructed.event_version == original_envelope.event_version
    assert reconstructed.occurred_at == original_envelope.occurred_at
    assert reconstructed.tenant_id == original_envelope.tenant_id
    assert reconstructed.correlation_id == original_envelope.correlation_id
    assert reconstructed.causation_id == original_envelope.causation_id
    assert reconstructed.subject_type == original_envelope.subject_type
    assert reconstructed.subject_id == original_envelope.subject_id
    assert thaw_payload(reconstructed.payload) == thaw_payload(original_envelope.payload)


@pytest.mark.asyncio
async def test_outbox_enqueue_rejects_non_envelope() -> None:
    """Verify OutboxRepository.enqueue raises TypeError if passed an invalid object."""
    mock_session = MagicMock(spec=AsyncSession)
    repo = OutboxRepository(mock_session)

    with pytest.raises(TypeError, match="requires an EventEnvelope instance"):
        await repo.enqueue({"event_id": str(uuid4())})  # type: ignore[arg-type]

    assert mock_session.add.call_count == 0


@pytest.mark.asyncio
async def test_outbox_repository_requires_active_session() -> None:
    """Verify OutboxRepository raises ValueError if initialized with None session."""
    with pytest.raises(ValueError, match="requires an active AsyncSession"):
        OutboxRepository(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_same_uow_transaction_ownership_contract() -> None:
    """Verify OutboxRepository and AuditRepository share the exact same UoW session."""
    mock_session = MagicMock(spec=AsyncSession)

    outbox_repo = OutboxRepository(mock_session)
    audit_repo = AuditRepository(mock_session)

    # Both repositories share the identical session instance
    assert outbox_repo.session is mock_session
    assert audit_repo.session is mock_session
    assert outbox_repo.session is audit_repo.session

    # Neither repository owns commit or rollback
    assert mock_session.commit.call_count == 0
    assert mock_session.rollback.call_count == 0


@pytest.mark.asyncio
async def test_outbox_enqueue_exception_propagates() -> None:
    """Verify unexpected exceptions inside session.add propagate to caller."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.add.side_effect = RuntimeError("database session failure")
    repo = OutboxRepository(mock_session)

    envelope = _create_sample_envelope()
    with pytest.raises(RuntimeError, match="database session failure"):
        await repo.enqueue(envelope)
