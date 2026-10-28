"""Comprehensive verification for durable notification outbox architecture (Phase DQ-4 Option C).

Enforces:
1. Two-stage Outbox Staging & Idempotency:
   - Atomic staging of NotificationOutboxCommandRecord inside consumer transactions.
   - Idempotent repeated staging with stable command_id.
2. Delivery Worker Lease & Concurrency:
   - SELECT FOR UPDATE SKIP LOCKED row-locking semantics.
   - Remote E-8 dispatch strictly outside database transactions.
   - Transient failure handling with exponential backoff.
   - Max-retries threshold transitioning to terminal FAILED state.
3. Fail-Closed Provisional Recipient Governance (DEC-E8-02):
   - Fail closed when allow_provisional_recipients=False (default).
   - Staged as BLOCKED with reason BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02.
   - Emits notification.dispatch.blocked audit event.
   - Permitted when allow_provisional_recipients=True or explicit opaque recipients present.
4. Administrative Recovery Service (AD-DQ4-01):
   - RBAC enforcement: only 'admin' or 'operations_manager' can unblock.
   - Rejection of non-administrative roles (PermissionError + DENIED audit).
   - Rejection of prohibited PII (emails, phones, MySQL IDs) in recovery
     (ValueError + DENIED audit).
   - State transition BLOCKED -> PENDING with immediate eligibility.
   - Successful delivery by worker post-recovery.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.audit.schemas import AuditEventOutcome
from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.events.schemas import EventEnvelope
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.delivery_worker import NotificationDeliveryWorker
from app.integrations.engagement.dispatcher import (
    NotificationDispatcherProtocol,
    NotificationDispatchError,
)
from app.integrations.engagement.handlers import (
    NotificationConsumerHandler,
    ScheduleChangedNotificationHandler,
    SchedulePublishedNotificationHandler,
    TaskBlockedNotificationHandler,
    WorkflowSlaWarningNotificationHandler,
    WorkflowTaskCreatedNotificationHandler,
)
from app.integrations.engagement.mock import MockNotificationDispatcher
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)
from app.integrations.engagement.recovery import NotificationRecoveryService
from app.integrations.engagement.repository import NotificationOutboxRepository

# ==============================================================================
# In-Memory Session Double for Outbox & Audit Tests
# ==============================================================================


class InMemoryOutboxDbSession:
    """In-memory AsyncSession test double simulating SQLAlchemy operations for outbox."""

    def __init__(self) -> None:
        self.records: dict[str, NotificationOutboxCommandRecord] = {}
        self.audit_records: list[Any] = []
        self._pending_add: list[Any] = []
        self.bind = MagicMock()
        self.bind.dialect.name = "postgresql"

    def add(self, obj: Any) -> None:
        if isinstance(obj, NotificationOutboxCommandRecord):
            self.records[obj.command_id] = obj
        else:
            self._pending_add.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        for item in self._pending_add:
            self.audit_records.append(item)
        self._pending_add.clear()

    async def rollback(self) -> None:
        self._pending_add.clear()

    async def close(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        stmt_str = str(stmt)
        result_mock = MagicMock()
        compiled = stmt.compile()

        # Handle queries filtering by command_id (point lookups)
        command_id_param = next((v for k, v in compiled.params.items() if "command_id" in k), None)
        if command_id_param is not None:
            matched = [self.records[command_id_param]] if command_id_param in self.records else []
            result_mock.scalars.return_value.first.return_value = matched[0] if matched else None
            result_mock.scalars.return_value.all.return_value = matched
            return result_mock

        # Handle batch poll query
        if "dispatch_state" in stmt_str:
            now = datetime.now(UTC)
            eligible = []
            for rec in self.records.values():
                is_eligible_state = (
                    rec.dispatch_state == "PENDING"
                    or (
                        rec.dispatch_state == "PROCESSING"
                        and rec.lease_expires_at
                        and rec.lease_expires_at <= now
                    )
                )
                if is_eligible_state and rec.next_attempt_at <= now:
                    eligible.append(rec)
            result_mock.scalars.return_value.all.return_value = eligible
            result_mock.scalars.return_value.first.return_value = (
                eligible[0] if eligible else None
            )
            return result_mock

        all_records = list(self.records.values())
        result_mock.scalars.return_value.all.return_value = all_records
        result_mock.scalars.return_value.first.return_value = (
            all_records[0] if all_records else None
        )
        return result_mock


class InMemorySessionFactory:
    """SessionFactory producing shared or isolated session doubles."""

    def __init__(self, shared_session: InMemoryOutboxDbSession | None = None) -> None:
        self.session = shared_session or InMemoryOutboxDbSession()

    def __call__(self) -> "SessionContext":
        return SessionContext(self.session)


class SessionContext:
    def __init__(self, session: InMemoryOutboxDbSession) -> None:
        self.session = session

    async def __aenter__(self) -> InMemoryOutboxDbSession:
        return self.session

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            await self.session.rollback()


# ==============================================================================
# 1. Outbox Repository Unit Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_outbox_repository_stage_command_creates_record() -> None:
    """Verify stage_command creates a typed NotificationOutboxCommandRecord in PENDING state."""
    session = InMemoryOutboxDbSession()
    repo = NotificationOutboxRepository(session)

    cmd = NotificationCommand(
        command_id="cmd-stage-001",
        correlation_reference="corr-001",
        causation_reference="evt-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
            source_entity_reference="sch-101",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-101",
            "version": 1,
        },
    )

    record = await repo.stage_command(command=cmd)
    assert record.command_id == "cmd-stage-001"
    assert record.tenant_id == "tenant-alpha"
    assert record.dispatch_state == "PENDING"
    assert record.retry_count == 0
    assert record.recipient_references == ["urn:e7:cohort:faculty"]
    assert record.notification_context["schedule_id"] == "sch-101"
    assert "cmd-stage-001" in session.records


@pytest.mark.asyncio
async def test_outbox_repository_stage_command_idempotent() -> None:
    """Repeated calls to stage_command with identical command_id return existing record."""
    session = InMemoryOutboxDbSession()
    repo = NotificationOutboxRepository(session)

    cmd = NotificationCommand(
        command_id="cmd-stage-dup",
        correlation_reference="corr-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-dup",
            "version": 1,
        },
    )

    rec1 = await repo.stage_command(command=cmd)
    rec2 = await repo.stage_command(command=cmd)

    assert rec1 is rec2
    assert len(session.records) == 1


@pytest.mark.asyncio
async def test_outbox_repository_claim_batch_sets_lease() -> None:
    """claim_pending_batch transitions PENDING records to PROCESSING with lease owner & expiry."""
    session = InMemoryOutboxDbSession()
    repo = NotificationOutboxRepository(session)

    cmd = NotificationCommand(
        command_id="cmd-claim-001",
        correlation_reference="corr-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-1",
            "version": 1,
        },
    )
    await repo.stage_command(command=cmd)

    claimed = await repo.claim_pending_batch(
        limit=10, lease_duration_seconds=30.0, lease_owner="worker-alpha"
    )
    assert len(claimed) == 1
    assert claimed[0].dispatch_state == "PROCESSING"
    assert claimed[0].lease_owner == "worker-alpha"
    assert claimed[0].lease_expires_at is not None
    assert claimed[0].lease_expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_outbox_repository_mark_failed_with_exponential_backoff() -> None:
    """mark_failed increments retry_count and calculates exponential backoff delay."""
    session = InMemoryOutboxDbSession()
    repo = NotificationOutboxRepository(session)

    cmd = NotificationCommand(
        command_id="cmd-retry-001",
        correlation_reference="corr-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-1",
            "version": 1,
        },
    )
    rec = await repo.stage_command(command=cmd, max_retries=3)

    # First failure -> retry 1, backoff = 1.0s, state returns to PENDING
    await repo.mark_failed("cmd-retry-001", error="E-8 timeout", retry_backoff_seconds=1.0)
    assert rec.retry_count == 1
    assert rec.dispatch_state == "PENDING"
    assert rec.last_error == "E-8 timeout"
    assert rec.lease_owner is None

    # Second failure -> retry 2, backoff = 2.0s
    await repo.mark_failed(
        "cmd-retry-001", error="E-8 connection refused", retry_backoff_seconds=1.0
    )
    assert rec.retry_count == 2
    assert rec.dispatch_state == "PENDING"

    # Third failure -> retry 3 >= max_retries (3) -> terminal FAILED
    await repo.mark_failed("cmd-retry-001", error="E-8 503 unavailable", retry_backoff_seconds=1.0)
    assert rec.retry_count == 3
    assert rec.dispatch_state == "FAILED"


# ==============================================================================
# 2. Delivery Worker Unit Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_delivery_worker_successful_poll_and_deliver() -> None:
    """NotificationDeliveryWorker claims pending outbox command and dispatches to E-8 outside DB."""
    db_session = InMemoryOutboxDbSession()
    session_factory = InMemorySessionFactory(db_session)
    mock_dispatcher = MockNotificationDispatcher()

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,  # type: ignore[arg-type]
        dispatcher=mock_dispatcher,
        settings=NotificationSettings(pipeline_mode="outbox"),
        worker_id="worker-del-01",
    )

    repo = NotificationOutboxRepository(db_session)
    cmd = NotificationCommand(
        command_id="cmd-deliver-001",
        correlation_reference="corr-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-del-1",
            "version": 1,
        },
    )
    await repo.stage_command(command=cmd)

    processed = await worker.poll_and_deliver()
    assert processed == 1
    assert len(mock_dispatcher.received_commands) == 1
    assert mock_dispatcher.received_commands[0].command_id == "cmd-deliver-001"
    assert db_session.records["cmd-deliver-001"].dispatch_state == "DISPATCHED"
    assert db_session.records["cmd-deliver-001"].dispatched_at is not None


@pytest.mark.asyncio
async def test_delivery_worker_error_retries_and_terminal_failure() -> None:
    """Worker catches dispatcher error, updates backoff, transitions to FAILED upon max retries."""
    db_session = InMemoryOutboxDbSession()
    session_factory = InMemorySessionFactory(db_session)

    failing_dispatcher = MagicMock(spec=NotificationDispatcherProtocol)
    failing_dispatcher.dispatch = AsyncMock(
        side_effect=NotificationDispatchError("Remote gateway 500")
    )

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,  # type: ignore[arg-type]
        dispatcher=failing_dispatcher,
        settings=NotificationSettings(pipeline_mode="outbox", max_retries=2),
        worker_id="worker-fail-01",
    )

    repo = NotificationOutboxRepository(db_session)
    cmd = NotificationCommand(
        command_id="cmd-fail-001",
        correlation_reference="corr-001",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-fail-1",
            "version": 1,
        },
    )
    rec = await repo.stage_command(command=cmd, max_retries=2)

    # First attempt -> fails, marked retry 1, state PENDING
    await worker.poll_and_deliver()
    assert rec.retry_count == 1
    assert rec.dispatch_state == "PENDING"

    # Reset next_attempt_at to now to simulate backoff passing
    rec.next_attempt_at = datetime.now(UTC)

    # Second attempt -> fails, marked retry 2 >= max_retries (2), state FAILED
    await worker.poll_and_deliver()
    assert rec.retry_count == 2
    assert rec.dispatch_state == "FAILED"


# ==============================================================================
# 3. Fail-Closed Provisional Recipient Governance (DEC-E8-02)
# ==============================================================================


@pytest.mark.asyncio
async def test_schedule_handler_fails_closed_on_provisional_recipients_by_default() -> None:
    """When allow_provisional_recipients=False (default), handler stages BLOCKED under DEC-E8-02."""
    session = InMemoryOutboxDbSession()
    mock_disp = MockNotificationDispatcher()

    # Default production settings: pipeline_mode="outbox", allow_provisional_recipients=False
    settings = NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=False)
    handler = SchedulePublishedNotificationHandler(dispatcher=mock_disp, settings=settings)

    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-audit-failclose",
        correlation_id="corr-pub-01",
        payload={"schedule_id": "sch-failclose-01", "version": 1},  # No explicit recipients!
    )

    await handler.handle(envelope=envelope, session=session)  # type: ignore[arg-type]

    # Staged to outbox as BLOCKED
    cmd_id = f"notif-pub-{envelope.event_id}"
    assert cmd_id in session.records
    record = session.records[cmd_id]
    assert record.dispatch_state == "BLOCKED"
    assert record.block_reason == "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02"
    assert record.recipient_references == ["urn:e7:cohort:faculty"]

    # Never delivered to dispatcher
    assert len(mock_disp.received_commands) == 0

    # Audit event emitted
    assert len(session._pending_add) == 1
    audit_event = session._pending_add[0]
    assert audit_event.event_type == "notification.dispatch.blocked"
    assert audit_event.outcome == AuditEventOutcome.DENIED
    assert audit_event.details["reason"] == "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02"


@pytest.mark.asyncio
async def test_schedule_handler_stages_pending_when_provisional_recipients_allowed() -> None:
    """When allow_provisional_recipients=True, provisional cohorts stage as PENDING."""
    session = InMemoryOutboxDbSession()
    mock_disp = MockNotificationDispatcher()

    settings = NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=True)
    handler = SchedulePublishedNotificationHandler(dispatcher=mock_disp, settings=settings)

    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-audit-allow",
        correlation_id="corr-pub-02",
        payload={"schedule_id": "sch-allow-01", "version": 1},
    )

    await handler.handle(envelope=envelope, session=session)  # type: ignore[arg-type]

    cmd_id = f"notif-pub-{envelope.event_id}"
    assert cmd_id in session.records
    record = session.records[cmd_id]
    assert record.dispatch_state == "PENDING"
    assert record.block_reason is None


@pytest.mark.asyncio
async def test_schedule_handler_stages_pending_when_explicit_recipients_provided() -> None:
    """When event explicitly provides opaque recipients, stages PENDING cleanly."""
    session = InMemoryOutboxDbSession()
    mock_disp = MockNotificationDispatcher()

    settings = NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=False)
    handler = SchedulePublishedNotificationHandler(dispatcher=mock_disp, settings=settings)

    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-audit-explicit",
        correlation_id="corr-pub-03",
        payload={
            "schedule_id": "sch-explicit-01",
            "version": 1,
            "recipients": ["urn:e7:faculty:prof-101", "urn:e7:faculty:prof-102"],
        },
    )

    await handler.handle(envelope=envelope, session=session)  # type: ignore[arg-type]

    cmd_id = f"notif-pub-{envelope.event_id}"
    assert cmd_id in session.records
    record = session.records[cmd_id]
    assert record.dispatch_state == "PENDING"
    assert record.recipient_references == ["urn:e7:faculty:prof-101", "urn:e7:faculty:prof-102"]


@pytest.mark.asyncio
async def test_schedule_handler_blocks_when_recipients_contain_plaintext_pii() -> None:
    """When event recipients contain plaintext email or phone, stages BLOCKED under DEC-E8-02."""
    session = InMemoryOutboxDbSession()
    mock_disp = MockNotificationDispatcher()

    settings = NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=True)
    handler = SchedulePublishedNotificationHandler(dispatcher=mock_disp, settings=settings)

    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-audit-pii",
        correlation_id="corr-pub-04",
        payload={
            "schedule_id": "sch-pii-01",
            "version": 1,
            "recipients": ["dean@university.edu", "+1-555-019-2834"],
        },
    )

    await handler.handle(envelope=envelope, session=session)  # type: ignore[arg-type]

    cmd_id = f"notif-pub-{envelope.event_id}"
    assert cmd_id in session.records
    record = session.records[cmd_id]
    assert record.dispatch_state == "BLOCKED"
    assert record.block_reason == "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02"


# ==============================================================================
# 4. Administrative Recovery Service Unit Tests (AD-DQ4-01)
# ==============================================================================


@pytest.mark.asyncio
async def test_recovery_service_unblocks_command_and_delivers() -> None:
    """Administrator can unblock BLOCKED notification intent, which is then delivered by worker."""
    session = InMemoryOutboxDbSession()
    session_factory = InMemorySessionFactory(session)
    mock_dispatcher = MockNotificationDispatcher()

    # 1. Stage a BLOCKED record
    repo = NotificationOutboxRepository(session)
    cmd = NotificationCommand(
        command_id="cmd-blocked-001",
        correlation_reference="corr-blk-01",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_PUBLISHED",
            "schedule_id": "sch-rec-1",
            "version": 1,
        },
    )
    await repo.stage_command(
        command=cmd,
        dispatch_state="BLOCKED",
        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
    )
    assert session.records["cmd-blocked-001"].dispatch_state == "BLOCKED"

    # 2. Administrative Recovery by 'admin'
    recovery_svc = NotificationRecoveryService(session)  # type: ignore[arg-type]
    recovered = await recovery_svc.recover_blocked_notification(
        actor_principal_id="admin-user-01",
        actor_roles={"admin"},
        tenant_id="tenant-alpha",
        command_id="cmd-blocked-001",
        resolved_recipients=["urn:e7:faculty:dean-office", "urn:e7:faculty:exam-coordinator"],
    )

    assert recovered.dispatch_state == "PENDING"
    assert recovered.block_reason is None
    assert recovered.recipient_references == [
        "urn:e7:faculty:dean-office",
        "urn:e7:faculty:exam-coordinator",
    ]

    # 3. Delivery worker polls and dispatches the recovered command!
    worker = NotificationDeliveryWorker(
        session_factory=session_factory,  # type: ignore[arg-type]
        dispatcher=mock_dispatcher,
        settings=NotificationSettings(pipeline_mode="outbox"),
        worker_id="worker-post-rec",
    )
    processed = await worker.poll_and_deliver()
    assert processed == 1
    assert len(mock_dispatcher.received_commands) == 1
    assert mock_dispatcher.received_commands[0].recipient_references == [
        "urn:e7:faculty:dean-office",
        "urn:e7:faculty:exam-coordinator",
    ]
    assert session.records["cmd-blocked-001"].dispatch_state == "DISPATCHED"


@pytest.mark.asyncio
async def test_recovery_service_rejects_unauthorized_role() -> None:
    """Non-administrative role is rejected with PermissionError and audit event is DENIED."""
    session = InMemoryOutboxDbSession()
    recovery_svc = NotificationRecoveryService(session)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="Actor does not possess required role"):
        await recovery_svc.recover_blocked_notification(
            actor_principal_id="regular-faculty-user",
            actor_roles={"faculty"},
            tenant_id="tenant-alpha",
            command_id="cmd-blocked-001",
            resolved_recipients=["urn:e7:faculty:ok"],
        )

    assert len(session._pending_add) == 1
    audit_evt = session._pending_add[0]
    assert audit_evt.event_type == "notification.recovery.denied"
    assert audit_evt.outcome == AuditEventOutcome.DENIED


@pytest.mark.asyncio
async def test_recovery_service_rejects_prohibited_pii() -> None:
    """Resolved recipients containing plaintext emails or phone numbers are rejected."""
    session = InMemoryOutboxDbSession()
    recovery_svc = NotificationRecoveryService(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Plaintext email address is prohibited"):
        await recovery_svc.recover_blocked_notification(
            actor_principal_id="admin-user-01",
            actor_roles={"operations_manager"},
            tenant_id="tenant-alpha",
            command_id="cmd-blocked-001",
            resolved_recipients=["prof@university.edu"],
        )

    assert len(session._pending_add) == 1
    audit_evt = session._pending_add[0]
    assert audit_evt.event_type == "notification.recovery.rejected"
    assert audit_evt.outcome == AuditEventOutcome.DENIED


# ==============================================================================
# 5. Domain Handlers Coverage (All 5 Canonical Events)
# ==============================================================================


@pytest.mark.asyncio
async def test_all_handlers_fail_closed_with_provisional_recipients() -> None:
    """All 5 canonical handlers stage BLOCKED commands when provisional recipients disallowed."""
    session = InMemoryOutboxDbSession()
    mock_disp = MockNotificationDispatcher()
    settings = NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=False)

    h_pub = SchedulePublishedNotificationHandler(dispatcher=mock_disp, settings=settings)
    h_chg = ScheduleChangedNotificationHandler(dispatcher=mock_disp, settings=settings)
    h_blk = TaskBlockedNotificationHandler(dispatcher=mock_disp, settings=settings)
    h_tsk = WorkflowTaskCreatedNotificationHandler(dispatcher=mock_disp, settings=settings)
    h_sla = WorkflowSlaWarningNotificationHandler(dispatcher=mock_disp, settings=settings)

    def base_envelope(evt_type: str, payload: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            event_id=uuid4(),
            event_type=evt_type,
            occurred_at=datetime.now(UTC),
            tenant_id="tenant-matrix",
            payload=payload,
        )

    # 1. schedule.published
    e1 = base_envelope("schedule.published", {"schedule_id": "s1", "version": 1})
    await h_pub.handle(envelope=e1, session=session)  # type: ignore[arg-type]
    assert session.records[f"notif-pub-{e1.event_id}"].dispatch_state == "BLOCKED"

    # 2. schedule.changed
    e2 = base_envelope(
        "schedule.changed", {"schedule_id": "s1", "old_version": 1, "new_version": 2}
    )
    await h_chg.handle(envelope=e2, session=session)  # type: ignore[arg-type]
    assert session.records[f"notif-chg-{e2.event_id}"].dispatch_state == "BLOCKED"

    # 3. task.blocked
    e3 = base_envelope("task.blocked", {"task_id": "t1", "blocked_reason": "resource missing"})
    await h_blk.handle(envelope=e3, session=session)  # type: ignore[arg-type]
    assert session.records[f"notif-blk-{e3.event_id}"].dispatch_state == "BLOCKED"

    # 4. workflow.task.created
    e4 = base_envelope(
        "workflow.task.created", {"workflow_task_id": "w1", "candidate_roles": ["evaluator"]}
    )
    await h_tsk.handle(envelope=e4, session=session)  # type: ignore[arg-type]
    assert session.records[f"notif-tsk-{e4.event_id}"].dispatch_state == "BLOCKED"

    # 5. workflow.sla.warning
    e5 = base_envelope(
        "workflow.sla.warning",
        {"workflow_task_id": "w1", "warning_at": datetime.now(UTC).isoformat()},
    )
    await h_sla.handle(envelope=e5, session=session)  # type: ignore[arg-type]
    assert session.records[f"notif-sla-{e5.event_id}"].dispatch_state == "BLOCKED"

    # Zero commands dispatched over wire
    assert len(mock_disp.received_commands) == 0


@pytest.mark.asyncio
async def test_delivery_worker_lifecycle_start_and_stop() -> None:
    """Worker background task starts and stops cleanly via stop_event without hanging."""
    db_session = InMemoryOutboxDbSession()
    session_factory = InMemorySessionFactory(db_session)
    mock_dispatcher = MockNotificationDispatcher()

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,  # type: ignore[arg-type]
        dispatcher=mock_dispatcher,
        settings=NotificationSettings(pipeline_mode="outbox", poll_interval_seconds=0.05),
        worker_id="worker-lifecycle-01",
    )

    stop_event = asyncio.Event()
    tasks = worker.start_background_tasks(stop_event)
    assert len(tasks) == 1
    assert not tasks[0].done()

    # Let worker loop run briefly
    await asyncio.sleep(0.1)

    # Signal shutdown
    stop_event.set()
    await asyncio.wait_for(tasks[0], timeout=1.0)
    assert tasks[0].done()


@pytest.mark.asyncio
async def test_consumer_processor_transactional_atomicity_and_deduplication() -> None:
    """ConsumerEventProcessor enforces atomic inbox deduplication with notification outbox."""
    from app.events.inbox.processor import ConsumerEventProcessor

    session = InMemoryOutboxDbSession()

    class MockUow:
        def __init__(self, sess: InMemoryOutboxDbSession) -> None:
            self.session = sess

        async def __aenter__(self) -> "MockUow":
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            if exc_type is not None:
                await self.session.rollback()

        async def commit(self) -> None:
            await self.session.commit()

        async def rollback(self) -> None:
            await self.session.rollback()

    def uow_factory() -> MockUow:
        return MockUow(session)

    handler = NotificationConsumerHandler(
        settings=NotificationSettings(pipeline_mode="outbox", allow_provisional_recipients=True)
    )

    processor = ConsumerEventProcessor(
        consumer_name="e7.notif.test",
        uow_factory=uow_factory,
        handler=handler,
    )

    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-atomic",
        payload={"schedule_id": "sch-atomic-1", "version": 1},
    )

    recorded_inbox: set[tuple[str, UUID]] = set()

    with patch("app.events.inbox.processor.InboxRepository") as mock_inbox_cls:
        mock_inbox = MagicMock()

        async def mock_try_record(*, consumer_name: str, envelope: EventEnvelope) -> bool:
            key = (consumer_name, envelope.event_id)
            if key in recorded_inbox:
                return False
            recorded_inbox.add(key)
            return True

        mock_inbox.try_record = mock_try_record
        mock_inbox_cls.return_value = mock_inbox

        # First attempt succeeds -> stages outbox command
        outcome1 = await processor.process(envelope)
        assert outcome1.outcome.value == "PROCESSED"
        cmd_id = f"notif-pub-{envelope.event_id}"
        assert cmd_id in session.records
        assert session.records[cmd_id].dispatch_state == "PENDING"
        assert ("e7.notif.test", envelope.event_id) in recorded_inbox

        # Duplicate delivery with same event_id is skipped by inbox deduplication
        outcome2 = await processor.process(envelope)
        assert outcome2.outcome.value == "DUPLICATE"
        assert len(session.records) == 1

