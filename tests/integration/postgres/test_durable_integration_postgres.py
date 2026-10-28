"""Live PostgreSQL Integration Tests for Durable Architecture (Phase DQ-4 Option C).

Enforces real database integration verification across 13 core production scenarios:
1. Notification command persistence.
2. Inbox + command transactional atomicity.
3. Database rollback.
4. Concurrent command claims (FOR UPDATE SKIP LOCKED).
5. Lease expiration and stale worker completion.
6. Notification retry state transitions.
7. Tenant-scoped recovery.
8. Persisted SLA registration.
9. Persisted pause/resume.
10. Restart recovery.
11. Concurrent SLA warning evaluation.
12. Cross-tenant negative access.
13. Complete trigger-provenance round-trip.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.db.models.sla import TaskSlaStateRecord
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.inbox.models import InboxMessageRecord
from app.events.inbox.repository import InboxRepository
from app.events.schemas import EventEnvelope
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.delivery_worker import NotificationDeliveryWorker
from app.integrations.engagement.mock import MockNotificationDispatcher
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)
from app.integrations.engagement.repository import NotificationOutboxRepository
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.service import WorkflowService
from app.workflow.sla.models import (
    SlaPolicyConfig,
    SlaStatus,
    SlaTaskStateNotFoundError,
    TaskSlaState,
)
from app.workflow.sla.repository import SlaRepository

# ==============================================================================
# 1. Notification Command Persistence
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_notification_command_persistence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Staging a NotificationCommand in PostgreSQL persists and is visible in a fresh session."""
    tenant_id = f"tenant-notif-persist-{uuid4().hex[:8]}"
    command_id = f"cmd-persist-{uuid4().hex[:8]}"

    cmd = NotificationCommand(
        command_id=command_id,
        correlation_reference="corr-pg-01",
        organization_scope_reference=tenant_id,
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={"schedule_id": "sch-01", "version": 1},
    )

    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        staged = await repo.stage_command(command=cmd)
        assert staged.command_id == command_id
        await session.commit()

    async with session_factory() as verify_session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        result = await verify_session.execute(stmt)
        record = result.scalars().first()
        assert record is not None
        assert record.tenant_id == tenant_id
        assert record.dispatch_state == "PENDING"
        assert record.recipient_references == ["urn:e7:cohort:faculty"]


# ==============================================================================
# 2. Inbox + Command Transactional Atomicity
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_inbox_and_command_transactional_atomicity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Inbox deduplication record and staged outbox command commit atomically in same UoW."""
    tenant_id = f"tenant-atomic-{uuid4().hex[:8]}"
    event_id = uuid4()
    consumer_name = "e7.notif.integration.worker"
    command_id = f"cmd-atomic-{event_id}"

    envelope = EventEnvelope(
        event_id=event_id,
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_id,
        payload={"schedule_id": "sch-atomic-1", "version": 1},
    )

    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        inbox_repo = InboxRepository(uow.session)
        is_new = await inbox_repo.try_record(
            consumer_name=consumer_name,
            envelope=envelope,
        )
        assert is_new is True

        outbox_repo = NotificationOutboxRepository(uow.session)
        await outbox_repo.stage_command(
            tenant_id=tenant_id,
            command_id=command_id,
            correlation_reference=str(event_id),
            trigger_kind="SOURCE_VERIFIED_EVENT",
            source_event_name="schedule.published",
            recipient_references=["urn:e7:cohort:faculty"],
            notification_context={"schedule_id": "sch-atomic-1"},
        )
        await uow.commit()

    # Verify both records exist durably in separate session
    async with session_factory() as verify_session:
        stmt_inbox = select(InboxMessageRecord).where(
            InboxMessageRecord.consumer_name == consumer_name,
            InboxMessageRecord.event_id == event_id,
        )
        res_inbox = await verify_session.execute(stmt_inbox)
        assert res_inbox.scalars().first() is not None

        stmt_outbox = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res_outbox = await verify_session.execute(stmt_outbox)
        assert res_outbox.scalars().first() is not None


# ==============================================================================
# 3. Database Rollback
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_database_rollback_prevents_partial_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Transaction failure rolls back both inbox record and outbox command cleanly."""
    tenant_id = f"tenant-rollback-{uuid4().hex[:8]}"
    event_id = uuid4()
    consumer_name = "e7.notif.rollback.worker"
    command_id = f"cmd-rb-{event_id}"

    envelope = EventEnvelope(
        event_id=event_id,
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_id,
        payload={"schedule_id": "sch-rb-1"},
    )

    uow = SqlAlchemyUnitOfWork(session_factory)
    try:
        async with uow:
            inbox_repo = InboxRepository(uow.session)
            await inbox_repo.try_record(
                consumer_name=consumer_name,
                envelope=envelope,
            )
            outbox_repo = NotificationOutboxRepository(uow.session)
            await outbox_repo.stage_command(
                tenant_id=tenant_id,
                command_id=command_id,
                correlation_reference=str(event_id),
                trigger_kind="SOURCE_VERIFIED_EVENT",
                source_event_name="schedule.published",
            )
            raise RuntimeError("Simulated transaction crash before commit")
    except RuntimeError:
        pass

    async with session_factory() as verify_session:
        res_inbox = await verify_session.execute(
            select(InboxMessageRecord).where(
                InboxMessageRecord.consumer_name == consumer_name,
                InboxMessageRecord.event_id == event_id,
            )
        )
        assert res_inbox.scalars().first() is None

        res_outbox = await verify_session.execute(
            select(NotificationOutboxCommandRecord).where(
                NotificationOutboxCommandRecord.command_id == command_id
            )
        )
        assert res_outbox.scalars().first() is None


# ==============================================================================
# 4. Concurrent Command Claims (FOR UPDATE SKIP LOCKED)
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_concurrent_command_claims_skip_locked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent workers claim disjoint subsets using SELECT FOR UPDATE SKIP LOCKED."""
    tenant_id = f"tenant-skip-locked-{uuid4().hex[:8]}"

    # Seed 10 pending records
    async with session_factory() as seed_session:
        repo = NotificationOutboxRepository(seed_session)
        for i in range(10):
            await repo.stage_command(
                tenant_id=tenant_id,
                command_id=f"cmd-skip-{tenant_id}-{i}",
                correlation_reference=f"corr-{i}",
                trigger_kind="SOURCE_VERIFIED_EVENT",
                source_event_name="task.blocked",
            )
        await seed_session.commit()

    claimed_w1: list[str] = []
    claimed_w2: list[str] = []

    async def worker_claim(worker_id: str, dest_list: list[str]) -> None:
        async with session_factory() as session:
            repo = NotificationOutboxRepository(session)
            records = await repo.claim_pending_batch(
                limit=5,
                lease_duration_seconds=30.0,
                lease_owner=worker_id,
            )
            await session.commit()
            dest_list.extend([r.command_id for r in records])

    # Run concurrent claims
    await asyncio.gather(
        worker_claim("worker-alpha", claimed_w1),
        worker_claim("worker-beta", claimed_w2),
    )

    # Disjoint sets: no single command claimed by both workers
    intersection = set(claimed_w1).intersection(set(claimed_w2))
    assert len(intersection) == 0, f"Overlapping claims detected: {intersection}"
    assert len(claimed_w1) + len(claimed_w2) == 10


# ==============================================================================
# 5. Lease Expiration and Stale Worker Completion
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_lease_expiration_and_stale_worker_rejection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Expired lease allows reclaiming; stale worker completion attempt is safely rejected."""
    tenant_id = f"tenant-lease-exp-{uuid4().hex[:8]}"
    command_id = f"cmd-lease-exp-{uuid4().hex[:8]}"

    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await repo.stage_command(
            tenant_id=tenant_id,
            command_id=command_id,
            correlation_reference="corr-exp-01",
            trigger_kind="SOURCE_VERIFIED_EVENT",
            source_event_name="schedule.published",
        )
        await session.commit()

    # 1. Worker 1 claims with expired lease (simulated via past lease_expires_at)
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        claimed = await repo.claim_pending_batch(limit=1, lease_owner="worker-slow-1")
        assert len(claimed) == 1
        rec = claimed[0]
        rec.lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        await session.commit()

    # 2. Worker 2 reclaims expired lease
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        claimed_2 = await repo.claim_pending_batch(limit=1, lease_owner="worker-active-2")
        assert len(claimed_2) == 1
        assert claimed_2[0].command_id == command_id
        await session.commit()

    # 3. Stale Worker 1 attempts to mark dispatched -> REJECTED
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        updated = await repo.mark_dispatched(
            command_id=command_id,
            lease_owner="worker-slow-1",
            tenant_id=tenant_id,
        )
        assert updated is False
        await session.commit()

    # 4. Active Worker 2 marks dispatched -> SUCCEEDS
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        updated = await repo.mark_dispatched(
            command_id=command_id,
            lease_owner="worker-active-2",
            tenant_id=tenant_id,
        )
        assert updated is True
        await session.commit()

    # Verify final state in DB
    async with session_factory() as verify_session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res = await verify_session.execute(stmt)
        record = res.scalars().first()
        assert record is not None
        assert record.dispatch_state == "DISPATCHED"


# ==============================================================================
# 6. Notification Retry State Transitions
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_notification_retry_state_transitions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Transient failures increment retries and calculate backoff; terminal failure sets FAILED."""
    tenant_id = f"tenant-retry-{uuid4().hex[:8]}"
    command_id = f"cmd-retry-{uuid4().hex[:8]}"

    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await repo.stage_command(
            tenant_id=tenant_id,
            command_id=command_id,
            correlation_reference="corr-rt-01",
            trigger_kind="SOURCE_VERIFIED_EVENT",
            source_event_name="schedule.published",
            max_retries=2,
        )
        await session.commit()

    # Claim and fail 1st time
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        claimed = await repo.claim_pending_batch(limit=1, lease_owner="worker-rt")
        assert len(claimed) == 1
        failed_ok = await repo.mark_failed(
            command_id=command_id,
            error="Connection reset by peer",
            lease_owner="worker-rt",
            retry_backoff_seconds=1.0,
            tenant_id=tenant_id,
        )
        assert failed_ok is True
        await session.commit()

    # Verify intermediate state in DB
    async with session_factory() as session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res = await session.execute(stmt)
        rec = res.scalars().first()
        assert rec is not None
        assert rec.retry_count == 1
        assert rec.dispatch_state == "PENDING"
        # Reset next_attempt_at to now for immediate re-claim
        rec.next_attempt_at = datetime.now(UTC)
        await session.commit()

    # Claim and fail 2nd time -> reaches max_retries (2) -> FAILED
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        claimed = await repo.claim_pending_batch(limit=1, lease_owner="worker-rt")
        assert len(claimed) == 1
        failed_ok = await repo.mark_failed(
            command_id=command_id,
            error="Remote gateway 503",
            lease_owner="worker-rt",
            tenant_id=tenant_id,
        )
        assert failed_ok is True
        await session.commit()

    async with session_factory() as verify_session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res = await verify_session.execute(stmt)
        rec = res.scalars().first()
        assert rec is not None
        assert rec.retry_count == 2
        assert rec.dispatch_state == "FAILED"


# ==============================================================================
# 7. Tenant-Scoped Recovery
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_tenant_scoped_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Blocked command can be unblocked by authorized tenant admin; cross-tenant recovery fails."""
    tenant_a = f"tenant-rec-A-{uuid4().hex[:8]}"
    tenant_b = f"tenant-rec-B-{uuid4().hex[:8]}"
    command_id = f"cmd-rec-{uuid4().hex[:8]}"

    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await repo.stage_command(
            tenant_id=tenant_a,
            command_id=command_id,
            correlation_reference="corr-rec-01",
            trigger_kind="SOURCE_VERIFIED_EVENT",
            source_event_name="schedule.published",
            dispatch_state="BLOCKED",
            block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
        )
        await session.commit()

    # Tenant B tries to unblock Tenant A's command -> returns None
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        recovered_b = await repo.recover_blocked_command(
            tenant_id=tenant_b,
            command_id=command_id,
            resolved_recipients=["urn:e7:faculty:dean"],
        )
        assert recovered_b is None
        await session.commit()

    # Tenant A unblocks -> returns updated record with PENDING state
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        recovered_a = await repo.recover_blocked_command(
            tenant_id=tenant_a,
            command_id=command_id,
            resolved_recipients=["urn:e7:faculty:dean"],
        )
        assert recovered_a is not None
        assert recovered_a.dispatch_state == "PENDING"
        assert recovered_a.recipient_references == ["urn:e7:faculty:dean"]
        await session.commit()


# ==============================================================================
# 8. Persisted SLA Registration
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_persisted_sla_registration(
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Any,
) -> None:
    """WorkflowService.attach_task_sla persists a TaskSlaStateRecord into PostgreSQL."""
    tenant_id = f"tenant-sla-reg-{uuid4().hex[:8]}"
    task_id = uuid4()
    inst_id = uuid4()

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    wf_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=adapter,
        persistence_mode="database",
    )

    policy = SlaPolicyConfig(
        policy_key="pg_sla_policy",
        name="Postgres SLA Policy",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )
    wf_service.register_sla_policy(policy)

    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
    state = await wf_service.attach_task_sla(
        tenant_id=tenant_id,
        instance_id=inst_id,
        task_id=task_id,
        policy_key="pg_sla_policy",
        created_at=t0,
    )
    assert state.task_id == task_id

    # Verify directly in PostgreSQL via independent session
    async with session_factory() as verify_session:
        stmt = select(TaskSlaStateRecord).where(
            TaskSlaStateRecord.tenant_id == tenant_id,
            TaskSlaStateRecord.task_id == task_id,
        )
        res = await verify_session.execute(stmt)
        record = res.scalars().first()
        assert record is not None
        assert record.policy_key == "pg_sla_policy"
        assert record.status == "RUNNING"


# ==============================================================================
# 9. Persisted Pause/Resume
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_persisted_pause_and_resume(
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Any,
) -> None:
    """WorkflowService pause and resume operations persist to PostgreSQL."""
    tenant_id = f"tenant-sla-pr-{uuid4().hex[:8]}"
    task_id = uuid4()
    inst_id = uuid4()

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    wf_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=adapter,
        persistence_mode="database",
    )

    policy = SlaPolicyConfig(
        policy_key="pg_pause_policy",
        name="Postgres Pause Policy",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )
    wf_service.register_sla_policy(policy)

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)
    await wf_service.attach_task_sla(
        tenant_id=tenant_id,
        instance_id=inst_id,
        task_id=task_id,
        policy_key="pg_pause_policy",
        created_at=t0,
    )

    # Pause at +30m
    t_pause = t0 + timedelta(minutes=30)
    paused = await wf_service.pause_task_sla(
        tenant_id=tenant_id,
        task_id=task_id,
        paused_at=t_pause,
    )
    assert paused.status == SlaStatus.PAUSED

    async with session_factory() as verify_session:
        repo = SlaRepository(verify_session)
        rec = await repo.get_task_sla(tenant_id=tenant_id, task_id=task_id)
        assert rec is not None
        assert rec.status == "PAUSED"
        assert rec.remaining_warning_seconds == 1800

    # Resume at +1h30m (after 1h pause)
    t_resume = t_pause + timedelta(hours=1)
    resumed = await wf_service.resume_task_sla(
        tenant_id=tenant_id,
        task_id=task_id,
        resumed_at=t_resume,
    )
    assert resumed.status == SlaStatus.RUNNING

    async with session_factory() as verify_session:
        repo = SlaRepository(verify_session)
        rec = await repo.get_task_sla(tenant_id=tenant_id, task_id=task_id)
        assert rec is not None
        assert rec.status == "RUNNING"
        assert rec.total_paused_seconds == 3600


# ==============================================================================
# 10. Restart Recovery
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_restart_recovery(
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Any,
) -> None:
    """Cold-started service recovers persisted SLA state from PostgreSQL and evaluates warning."""
    tenant_id = f"tenant-sla-rec-{uuid4().hex[:8]}"
    task_id = uuid4()
    inst_id = uuid4()

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    policy = SlaPolicyConfig(
        policy_key="pg_rec_policy",
        name="Postgres Recovery Policy",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)

    # Instance 1: Registers and persists SLA
    wf_1 = WorkflowService(uow_factory=uow_factory, adapter=adapter, persistence_mode="database")
    wf_1.register_sla_policy(policy)
    await wf_1.attach_task_sla(
        tenant_id=tenant_id,
        instance_id=inst_id,
        task_id=task_id,
        policy_key="pg_rec_policy",
        created_at=t0,
    )

    # Instance 2: Brand new service, empty in-memory state
    wf_2 = WorkflowService(uow_factory=uow_factory, adapter=adapter, persistence_mode="database")
    wf_2.register_sla_policy(policy)

    # Evaluate at +1h30m -> warning threshold reached
    t_eval = t0 + timedelta(hours=1, minutes=30)
    actions = await wf_2.evaluate_sla_triggers(tenant_id=tenant_id, as_of=t_eval)
    assert len(actions) == 1
    assert actions[0] == f"WARNING:{task_id}"

    # Verify persisted in PostgreSQL
    async with session_factory() as verify_session:
        repo = SlaRepository(verify_session)
        rec = await repo.get_task_sla(tenant_id=tenant_id, task_id=task_id)
        assert rec is not None
        assert rec.status == "WARNING"
        assert rec.warning_fired_at is not None


# ==============================================================================
# 11. Concurrent SLA Warning Evaluation
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_concurrent_sla_warning_evaluation(
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Any,
) -> None:
    """Two concurrent evaluators with FOR UPDATE SKIP LOCKED trigger warning exactly once."""
    tenant_id = f"tenant-conc-sla-{uuid4().hex[:8]}"
    task_id = uuid4()
    inst_id = uuid4()

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    policy = SlaPolicyConfig(
        policy_key="pg_conc_policy",
        name="Postgres Concurrent Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)
    wf = WorkflowService(uow_factory=uow_factory, adapter=adapter, persistence_mode="database")
    wf.register_sla_policy(policy)
    await wf.attach_task_sla(
        tenant_id=tenant_id,
        instance_id=inst_id,
        task_id=task_id,
        policy_key="pg_conc_policy",
        created_at=t0,
    )

    t_eval = t0 + timedelta(minutes=45)

    # Launch two concurrent evaluations
    results = await asyncio.gather(
        wf.evaluate_sla_triggers(tenant_id=tenant_id, as_of=t_eval),
        wf.evaluate_sla_triggers(tenant_id=tenant_id, as_of=t_eval),
    )

    total_warnings = sum(len(r) for r in results)
    assert total_warnings == 1, f"Expected exactly 1 warning emission, got {total_warnings}"


# ==============================================================================
# 12. Cross-Tenant Negative Access
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_cross_tenant_negative_access(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Tenant A's SLA record cannot be read or updated by Tenant B in PostgreSQL."""
    tenant_a = f"tenant-neg-A-{uuid4().hex[:8]}"
    tenant_b = f"tenant-neg-B-{uuid4().hex[:8]}"
    task_id = uuid4()
    inst_id = uuid4()

    t0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    state_a = TaskSlaState(
        task_id=task_id,
        instance_id=inst_id,
        tenant_id=tenant_a,
        policy_key="policy_sec",
        status=SlaStatus.RUNNING,
        created_at=t0,
        warning_at=t0 + timedelta(hours=1),
        breach_at=t0 + timedelta(hours=2),
    )

    async with session_factory() as session:
        repo = SlaRepository(session)
        await repo.create_task_sla(state_a)
        await session.commit()

    # Tenant B read attempt -> returns None
    async with session_factory() as session:
        repo = SlaRepository(session)
        rec_b = await repo.get_task_sla(tenant_id=tenant_b, task_id=task_id)
        assert rec_b is None

    # Tenant B update attempt -> raises SlaTaskStateNotFoundError (fails closed)
    state_b_attack = TaskSlaState(
        task_id=task_id,
        instance_id=inst_id,
        tenant_id=tenant_b,
        policy_key="policy_sec",
        status=SlaStatus.PAUSED,
        created_at=t0,
        warning_at=t0 + timedelta(hours=1),
        breach_at=t0 + timedelta(hours=2),
    )
    async with session_factory() as session:
        repo = SlaRepository(session)
        with pytest.raises(SlaTaskStateNotFoundError, match="not found in tenant"):
            await repo.update_task_sla(state_b_attack)


# ==============================================================================
# 13. Complete Trigger-Provenance Round-Trip
# ==============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pg_complete_trigger_provenance_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """schedule.changed notification preserves business_condition in PostgreSQL through delivery."""
    tenant_id = f"tenant-prov-{uuid4().hex[:8]}"
    command_id = f"cmd-prov-{uuid4().hex[:8]}"

    cmd = NotificationCommand(
        command_id=command_id,
        correlation_reference="corr-prov-01",
        organization_scope_reference=tenant_id,
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION,
            business_condition="SCHEDULE_CHANGE_NOTICE",
        ),
        recipient_references=["urn:e7:cohort:students"],
        notification_context={
            "template_id": "EXAM_SCHEDULE_CHANGED",
            "schedule_id": "sch-prov-1",
            "old_version": 1,
            "new_version": 2,
            "repair_reason": "Room relocation",
            "effective_date": "2026-11-01",
        },
    )

    # 1. Stage in PostgreSQL
    async with session_factory() as session:
        repo = NotificationOutboxRepository(session)
        await repo.stage_command(command=cmd)
        await session.commit()

    # 2. Verify stored in PostgreSQL with business_condition
    async with session_factory() as verify_session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res = await verify_session.execute(stmt)
        record = res.scalars().first()
        assert record is not None
        assert record.business_condition == "SCHEDULE_CHANGE_NOTICE"
        assert record.trigger_kind == "SOURCE_BACKED_BUSINESS_CONDITION"

    # 3. Deliver via NotificationDeliveryWorker and verify dispatcher receives intact trigger
    mock_dispatcher = MockNotificationDispatcher()
    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        dispatcher=mock_dispatcher,
        settings=NotificationSettings(pipeline_mode="outbox", enabled=True),
        worker_id="worker-prov",
    )
    delivered = await worker.poll_and_deliver()
    assert delivered == 1
    assert len(mock_dispatcher.received_commands) == 1
    dispatched = mock_dispatcher.received_commands[0]
    assert dispatched.trigger.trigger_kind == TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION
    assert dispatched.trigger.business_condition == "SCHEDULE_CHANGE_NOTICE"

    # 4. Verify DB state is DISPATCHED
    async with session_factory() as verify_session:
        stmt = select(NotificationOutboxCommandRecord).where(
            NotificationOutboxCommandRecord.command_id == command_id
        )
        res = await verify_session.execute(stmt)
        record = res.scalars().first()
        assert record is not None
        assert record.dispatch_state == "DISPATCHED"
