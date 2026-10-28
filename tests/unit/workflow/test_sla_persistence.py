"""Comprehensive verification of PostgreSQL-backed SLA persistence (Phase DQ-4 AD-DQ4-02).

Enforces:
1. SlaRepository CRUD & Mapping:
   - Strict tenant-scoped persistence of task_sla_states.
   - Mapping between SQLAlchemy TaskSlaStateRecord and domain TaskSlaState model.
   - Querying evaluable SLAs (status RUNNING/WARNING and threshold <= as_of).
2. SlaService Persistent Evaluation:
   - Warning threshold evaluation updates DB state and emits canonical workflow.sla.warning event.
   - Breach threshold evaluation updates DB state and emits canonical workflow.sla.breached event.
   - Idempotency & Concurrency: subsequent evaluations at same timestamp suppress
     duplicate event emission.
3. Worker Restart Recovery:
   - New SlaService process instance (cold start with empty in-memory dictionary)
     recovers active state from task_sla_states without duplicate trigger execution.
4. Cross-Tenant Isolation:
   - Evaluating Tenant A never scans, modifies, or fires events for Tenant B's tasks in DB.
5. Pause & Resume Semantics:
   - Pausing freezes business duration; resuming recalculates thresholds deterministically.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.db.models.sla import TaskSlaStateRecord
from app.events.schemas import EventEnvelope
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.service import WorkflowService
from app.workflow.sla.models import (
    EscalationAction,
    SlaEscalationRule,
    SlaPolicyConfig,
    SlaStatus,
    SlaTaskStateNotFoundError,
    TaskSlaState,
)
from app.workflow.sla.repository import SlaRepository
from app.workflow.sla.service import SlaService

# ==============================================================================
# In-Memory DB Session Double for TaskSlaStateRecord
# ==============================================================================


class InMemorySlaDbSession:
    """In-memory session double simulating SQLAlchemy operations for task_sla_states."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, UUID], TaskSlaStateRecord] = {}
        self.bind = MagicMock()
        self.bind.dialect.name = "postgresql"

    def add(self, obj: Any) -> None:
        if isinstance(obj, TaskSlaStateRecord):
            self.records[(obj.tenant_id, obj.task_id)] = obj

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        stmt_str = str(stmt)
        res = MagicMock()
        compiled = stmt.compile()

        # Handle point lookups filtering by task_id (get_task_sla)
        has_task_id = any("task_id" in k for k in compiled.params)
        if has_task_id:
            t_id = next((v for k, v in compiled.params.items() if "tenant_id" in k), None)
            t_uuid = next((v for k, v in compiled.params.items() if "task_id" in k), None)
            matched = []
            for (tenant, task_id), rec in self.records.items():
                if t_id and tenant != t_id:
                    continue
                if t_uuid and task_id != t_uuid:
                    continue
                matched.append(rec)

            res.scalars.return_value.first.return_value = matched[0] if matched else None
            res.scalars.return_value.all.return_value = matched
            return res

        # Handle list_evaluable_slas:
        if "dispatch_state" in stmt_str or "status" in stmt_str or "warning_at" in stmt_str:
            t_id = next((v for k, v in compiled.params.items() if "tenant_id" in k), None)
            as_of = next(
                (v for k, v in compiled.params.items() if "warning_at" in k or "breach_at" in k),
                None,
            )
            evaluable = []
            for (tenant, _task_id), rec in self.records.items():
                if t_id and tenant != t_id:
                    continue
                if rec.status in (SlaStatus.RUNNING.value, SlaStatus.WARNING.value):
                    if as_of is None or rec.warning_at <= as_of or rec.breach_at <= as_of:
                        evaluable.append(rec)
            res.scalars.return_value.all.return_value = evaluable
            res.scalars.return_value.first.return_value = evaluable[0] if evaluable else None
            return res

        all_records = list(self.records.values())
        res.scalars.return_value.all.return_value = all_records
        res.scalars.return_value.first.return_value = all_records[0] if all_records else None
        return res


# ==============================================================================
# 1. SlaRepository Unit Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_repository_create_and_get() -> None:
    """SlaRepository creates and retrieves TaskSlaStateRecord within transaction."""
    session = InMemorySlaDbSession()
    repo = SlaRepository(session)  # type: ignore[arg-type]

    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    state = TaskSlaState(
        task_id=task_id,
        instance_id=inst_id,
        tenant_id="tenant-sla-01",
        policy_key="standard_24h",
        status=SlaStatus.RUNNING,
        created_at=now,
        warning_at=now + timedelta(hours=12),
        breach_at=now + timedelta(hours=24),
    )

    created_rec = await repo.create_task_sla(state)
    assert created_rec.task_id == task_id
    assert created_rec.tenant_id == "tenant-sla-01"
    assert created_rec.status == "RUNNING"

    fetched = await repo.get_task_sla(tenant_id="tenant-sla-01", task_id=task_id)
    assert fetched is not None
    assert fetched.task_id == task_id

    # Model mapping
    model = SlaRepository.to_model(fetched)
    assert model.task_id == task_id
    assert model.status == SlaStatus.RUNNING
    assert model.warning_at == now + timedelta(hours=12)


@pytest.mark.asyncio
async def test_sla_repository_list_evaluable_slas_filters_by_threshold() -> None:
    """list_evaluable_slas returns only active SLAs whose warning or breach has arrived."""
    session = InMemorySlaDbSession()
    repo = SlaRepository(session)  # type: ignore[arg-type]

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)
    task1 = uuid4()
    task2 = uuid4()

    # Task 1: warning at +4h, breach at +8h
    s1 = TaskSlaState(
        task_id=task1,
        instance_id=uuid4(),
        tenant_id="tenant-eval",
        policy_key="fast_policy",
        status=SlaStatus.RUNNING,
        created_at=t0,
        warning_at=t0 + timedelta(hours=4),
        breach_at=t0 + timedelta(hours=8),
    )
    # Task 2: warning at +12h, breach at +24h
    s2 = TaskSlaState(
        task_id=task2,
        instance_id=uuid4(),
        tenant_id="tenant-eval",
        policy_key="slow_policy",
        status=SlaStatus.RUNNING,
        created_at=t0,
        warning_at=t0 + timedelta(hours=12),
        breach_at=t0 + timedelta(hours=24),
    )

    await repo.create_task_sla(s1)
    await repo.create_task_sla(s2)

    # Evaluate at +5h -> only task1 warning reached
    eval_time = t0 + timedelta(hours=5)
    results = await repo.list_evaluable_slas(tenant_id="tenant-eval", as_of=eval_time)
    task_ids = [r.task_id for r in results]
    assert task1 in task_ids


# ==============================================================================
# 2. SlaService Persistent Evaluation & Concurrency Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_service_persistent_warning_and_breach_evaluation() -> None:
    """SlaService evaluates persistent PostgreSQL task_sla_states and updates DB records."""
    session = InMemorySlaDbSession()

    service = SlaService()
    policy = SlaPolicyConfig(
        policy_key="policy_sla_test",
        name="Test SLA Policy",
        warning_duration_seconds=3600,  # 1h
        breach_duration_seconds=7200,   # 2h
        warning_escalation=SlaEscalationRule(action=EscalationAction.REMINDER),
        breach_escalation=SlaEscalationRule(action=EscalationAction.ESCALATE),
    )
    service.register_policy(policy)

    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    state = service.attach_task_sla(
        tenant_id="tenant-sla-live",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="policy_sla_test",
        created_at=t0,
        session=session,  # type: ignore[arg-type]
    )
    assert (state.tenant_id, state.task_id) in session.records

    outbox_events: list[EventEnvelope] = []
    audit_events: list[Any] = []

    mock_outbox = AsyncMock()
    mock_outbox.enqueue = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.append = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.session = session

    mock_audit = AsyncMock()
    mock_audit.append = AsyncMock(side_effect=lambda a: audit_events.append(a))
    mock_audit.session = session

    # 1. Evaluate at +1h30m -> Warning fires
    t_warning = t0 + timedelta(minutes=90)
    actions1 = await service.evaluate_triggers(
        as_of=t_warning,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-sla-live",
        session=session,  # type: ignore[arg-type]
    )

    assert len(actions1) == 1
    assert actions1[0] == f"WARNING:{task_id}"

    db_rec = session.records[("tenant-sla-live", task_id)]
    assert db_rec.status == "WARNING"
    assert db_rec.warning_fired_at == t_warning
    assert db_rec.breach_fired_at is None
    assert len(outbox_events) == 1
    assert outbox_events[0].event_type == "workflow.sla.warning"

    # 2. Subsequent evaluation at +1h45m -> Warning NOT duplicated
    t_check = t0 + timedelta(minutes=105)
    actions_dup = await service.evaluate_triggers(
        as_of=t_check,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-sla-live",
        session=session,  # type: ignore[arg-type]
    )
    assert len(actions_dup) == 0
    assert len(outbox_events) == 1  # No duplicate event

    # 3. Evaluate at +2h30m -> Breach fires
    t_breach = t0 + timedelta(minutes=150)
    actions_breach = await service.evaluate_triggers(
        as_of=t_breach,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-sla-live",
        session=session,  # type: ignore[arg-type]
    )
    assert len(actions_breach) == 1
    assert actions_breach[0] == f"BREACH:{task_id}"

    assert db_rec.status == "BREACHED"
    assert db_rec.breach_fired_at == t_breach
    assert len(outbox_events) == 2
    assert outbox_events[1].event_type == "workflow.sla.breached"


# ==============================================================================
# 3. Worker Restart Recovery Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_worker_restart_recovers_state_without_duplicate_events() -> None:
    """Fresh worker instance recovers persisted SLA state from PostgreSQL without re-firing."""
    session = InMemorySlaDbSession()
    policy = SlaPolicyConfig(
        policy_key="restart_policy",
        name="Restart SLA",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )

    # Worker 1: Registers task and fires warning
    worker1_service = SlaService()
    worker1_service.register_policy(policy)

    t0 = datetime(2026, 10, 28, 9, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    worker1_service.attach_task_sla(
        tenant_id="tenant-restart",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="restart_policy",
        created_at=t0,
        session=session,  # type: ignore[arg-type]
    )

    outbox_events: list[EventEnvelope] = []
    mock_outbox = AsyncMock()
    mock_outbox.enqueue = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.append = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.session = session
    mock_audit = AsyncMock()

    t_warn = t0 + timedelta(hours=1, minutes=10)
    await worker1_service.evaluate_triggers(
        as_of=t_warn,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-restart",
        session=session,  # type: ignore[arg-type]
    )
    assert len(outbox_events) == 1

    # SIMULATE WORKER RESTART: New SlaService with empty internal _states!
    worker2_service = SlaService()
    worker2_service.register_policy(policy)
    assert len(worker2_service._states) == 0  # Completely cold in-memory state

    # Worker 2 evaluates at +1h20m (warning already fired in DB)
    t_restart_eval = t0 + timedelta(hours=1, minutes=20)
    actions = await worker2_service.evaluate_triggers(
        as_of=t_restart_eval,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-restart",
        session=session,  # type: ignore[arg-type]
    )

    # Zero actions triggered! State in DB already had warning_fired_at
    assert len(actions) == 0
    assert len(outbox_events) == 1  # No duplicate warning fired


# ==============================================================================
# 4. Cross-Tenant Isolation in Database
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_cross_tenant_database_isolation() -> None:
    """Evaluating Tenant Alpha never evaluates or emits events for Tenant Beta in PostgreSQL."""
    session = InMemorySlaDbSession()
    service = SlaService()
    policy = SlaPolicyConfig(
        policy_key="iso_policy",
        name="Iso Policy",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )
    service.register_policy(policy)

    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
    task_alpha = uuid4()
    task_beta = uuid4()

    # Tenant Alpha task
    service.attach_task_sla(
        tenant_id="tenant-alpha",
        instance_id=uuid4(),
        task_id=task_alpha,
        policy_key="iso_policy",
        created_at=t0,
        session=session,  # type: ignore[arg-type]
    )
    # Tenant Beta task
    service.attach_task_sla(
        tenant_id="tenant-beta",
        instance_id=uuid4(),
        task_id=task_beta,
        policy_key="iso_policy",
        created_at=t0,
        session=session,  # type: ignore[arg-type]
    )

    outbox_events: list[EventEnvelope] = []
    mock_outbox = AsyncMock()
    mock_outbox.enqueue = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.append = AsyncMock(side_effect=lambda e: outbox_events.append(e))
    mock_outbox.session = session
    mock_audit = AsyncMock()

    # Evaluate ONLY Tenant Alpha
    t_eval = t0 + timedelta(hours=1, minutes=30)
    actions = await service.evaluate_triggers(
        as_of=t_eval,
        outbox_repo=mock_outbox,
        audit_repo=mock_audit,
        tenant_id="tenant-alpha",
        session=session,  # type: ignore[arg-type]
    )

    assert len(actions) == 1
    assert actions[0] == f"WARNING:{task_alpha}"

    # Verify DB states
    rec_alpha = session.records[("tenant-alpha", task_alpha)]
    rec_beta = session.records[("tenant-beta", task_beta)]

    assert rec_alpha.status == "WARNING"
    assert rec_alpha.warning_fired_at == t_eval

    # Tenant Beta remains completely untouched in RUNNING state!
    assert rec_beta.status == "RUNNING"
    assert rec_beta.warning_fired_at is None
    assert all(e.tenant_id == "tenant-alpha" for e in outbox_events)


# ==============================================================================
# 5. Pause & Resume Semantics
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_pause_and_resume_durations() -> None:
    """Pausing SLA captures elapsed duration and resumes recalculation deterministically."""
    service = SlaService()
    policy = SlaPolicyConfig(
        policy_key="pause_policy",
        name="Pause Policy",
        warning_duration_seconds=3600,  # 1h
        breach_duration_seconds=7200,   # 2h
    )
    service.register_policy(policy)

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    state = service.attach_task_sla(
        tenant_id="tenant-pause",
        instance_id=uuid4(),
        task_id=task_id,
        policy_key="pause_policy",
        created_at=t0,
    )

    # 1. Pause at +30m
    t_pause = t0 + timedelta(minutes=30)
    service.pause_task_sla(tenant_id="tenant-pause", task_id=task_id, paused_at=t_pause)
    assert state.status == SlaStatus.PAUSED
    assert state.paused_at == t_pause
    assert state.remaining_warning_seconds == 1800  # 30m remaining
    assert state.remaining_breach_seconds == 5400   # 90m remaining

    # 2. Resume after 1 hour of pause (+1h30m from start)
    t_resume = t_pause + timedelta(hours=1)
    service.resume_task_sla(tenant_id="tenant-pause", task_id=task_id, resumed_at=t_resume)
    assert state.status == SlaStatus.RUNNING
    assert state.paused_at is None
    assert state.total_paused_seconds == 3600
    # New warning_at = t_resume + 30m = t0 + 2h
    assert state.warning_at == t_resume + timedelta(seconds=1800)
    # New breach_at = t_resume + 90m = t0 + 3h
    assert state.breach_at == t_resume + timedelta(seconds=5400)


# ==============================================================================
# 6. Public WorkflowService Entry Points & Persistence Call Chain (Task 1)
# ==============================================================================


class SlaTestUnitOfWork:
    """UoW wrapping InMemorySlaDbSession for WorkflowService integration testing."""

    def __init__(self, session: InMemorySlaDbSession) -> None:
        self.session = session
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "SlaTestUnitOfWork":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.rolled_back = True
            await self.session.rollback()

    async def commit(self) -> None:
        self.committed = True
        await self.session.commit()

    async def rollback(self) -> None:
        self.rolled_back = True
        await self.session.rollback()


@pytest.mark.asyncio
async def test_workflow_service_public_sla_persistence_and_restart_recovery() -> None:
    """WorkflowService.attach_task_sla persists through UoW and survives restart."""
    shared_session = InMemorySlaDbSession()

    def uow_factory() -> SlaTestUnitOfWork:
        return SlaTestUnitOfWork(shared_session)

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    sla_svc_1 = SlaService(persistence_mode="database")
    wf_service_1 = WorkflowService(
        uow_factory=uow_factory,  # type: ignore[arg-type]
        adapter=adapter,
        sla_service=sla_svc_1,
    )

    policy = SlaPolicyConfig(
        policy_key="wf_pub_policy",
        name="Workflow Public Policy",
        warning_duration_seconds=3600,
        breach_duration_seconds=7200,
    )
    wf_service_1.register_sla_policy(policy)

    t0 = datetime(2026, 10, 28, 9, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    # 1. Attach task SLA through public entry point
    state = await wf_service_1.attach_task_sla(
        tenant_id="tenant-wf-sla",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="wf_pub_policy",
        created_at=t0,
    )
    assert state.task_id == task_id
    assert state.status == SlaStatus.RUNNING

    # Verify persisted in database session
    db_rec = shared_session.records.get(("tenant-wf-sla", task_id))
    assert db_rec is not None
    assert db_rec.task_id == task_id
    assert db_rec.status == "RUNNING"

    # 2. Pause through public entry point
    t_pause = t0 + timedelta(minutes=20)
    paused = await wf_service_1.pause_task_sla(
        tenant_id="tenant-wf-sla",
        task_id=task_id,
        paused_at=t_pause,
    )
    assert paused.status == SlaStatus.PAUSED
    assert db_rec.status == "PAUSED"
    assert db_rec.paused_at == t_pause

    # 3. Resume through public entry point
    t_resume = t_pause + timedelta(minutes=40)
    resumed = await wf_service_1.resume_task_sla(
        tenant_id="tenant-wf-sla",
        task_id=task_id,
        resumed_at=t_resume,
    )
    assert resumed.status == SlaStatus.RUNNING
    assert db_rec.status == "RUNNING"
    assert db_rec.total_paused_seconds == 2400

    # 4. Service Restart Recovery: Brand new WorkflowService with cold SLA cache
    sla_svc_2 = SlaService(persistence_mode="database")
    sla_svc_2.register_policy(policy)
    assert task_id not in sla_svc_2._states

    wf_service_2 = WorkflowService(
        uow_factory=uow_factory,  # type: ignore[arg-type]
        adapter=adapter,
        sla_service=sla_svc_2,
    )

    # Evaluate SLA triggers using authoritative database records
    t_eval = resumed.warning_at + timedelta(seconds=1)
    actions = await wf_service_2.evaluate_sla_triggers(
        tenant_id="tenant-wf-sla",
        as_of=t_eval,
    )
    assert len(actions) == 1
    assert actions[0] == f"WARNING:{task_id}"
    assert db_rec.status == "WARNING"
    assert db_rec.warning_fired_at == t_eval


@pytest.mark.asyncio
async def test_workflow_service_sync_backwards_compatibility_methods() -> None:
    """Synchronous backwards-compatibility entry points continue functioning in-memory."""
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    shared_session = InMemorySlaDbSession()
    service = WorkflowService(
        uow_factory=lambda: SlaTestUnitOfWork(shared_session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="memory",
    )
    policy = SlaPolicyConfig(
        policy_key="sync_policy",
        name="Sync Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_sla_policy(policy)

    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    # Sync attach
    state = service.attach_task_sla_sync(
        tenant_id="tenant-sync",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="sync_policy",
        created_at=t0,
    )
    assert state.task_id == task_id
    assert state.status == SlaStatus.RUNNING

    # Sync pause
    paused = service.pause_task_sla_sync(
        tenant_id="tenant-sync",
        task_id=task_id,
        paused_at=t0 + timedelta(minutes=10),
    )
    assert paused.status == SlaStatus.PAUSED

    # Sync resume
    resumed = service.resume_task_sla_sync(
        tenant_id="tenant-sync",
        task_id=task_id,
        resumed_at=t0 + timedelta(minutes=20),
    )
    assert resumed.status == SlaStatus.RUNNING


# ==============================================================================
# 7. Failure Propagation & Removal of Unsafe DB Fallback (Task 2)
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_database_error_propagates_and_rolls_back_uow() -> None:
    """Database query failure during SLA evaluation propagates and rolls back without fallback."""
    session = InMemorySlaDbSession()

    async def failing_execute(stmt: Any) -> Any:
        raise RuntimeError("PostgreSQL connection timeout during SLA lock query")

    session.execute = failing_execute  # type: ignore[method-assign]

    service = SlaService(persistence_mode="database")
    policy = SlaPolicyConfig(
        policy_key="err_policy",
        name="Error Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_policy(policy)

    mock_outbox = AsyncMock()
    mock_audit = AsyncMock()

    with pytest.raises(RuntimeError, match="PostgreSQL connection timeout"):
        await service.evaluate_triggers(
            as_of=datetime.now(UTC),
            outbox_repo=mock_outbox,
            audit_repo=mock_audit,
            tenant_id="tenant-fail",
            session=session,  # type: ignore[arg-type]
        )

    # Confirm no outbox or audit events emitted
    mock_outbox.enqueue.assert_not_called()
    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_sla_flush_failure_propagates_without_silent_suppression() -> None:
    """Database flush failure during SLA evaluation propagates and prevents commit."""
    session = InMemorySlaDbSession()

    async def failing_flush() -> None:
        raise RuntimeError("IntegrityError on task_sla_states flush")

    session.flush = failing_flush  # type: ignore[method-assign]

    service = SlaService(persistence_mode="database")
    policy = SlaPolicyConfig(
        policy_key="flush_err_policy",
        name="Flush Error Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_policy(policy)

    t0 = datetime(2026, 10, 28, 8, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    service.attach_task_sla(
        tenant_id="tenant-flush-fail",
        instance_id=uuid4(),
        task_id=task_id,
        policy_key="flush_err_policy",
        created_at=t0,
        session=session,  # type: ignore[arg-type]
    )

    mock_outbox = AsyncMock()
    mock_audit = AsyncMock()

    with pytest.raises(RuntimeError, match="IntegrityError on task_sla_states flush"):
        await service.evaluate_triggers(
            as_of=t0 + timedelta(hours=1),
            outbox_repo=mock_outbox,
            audit_repo=mock_audit,
            tenant_id="tenant-flush-fail",
            session=session,  # type: ignore[arg-type]
        )


# ==============================================================================
# 8. Removal of Cross-Tenant SLA Update Fallback (Task 5)
# ==============================================================================


@pytest.mark.asyncio
async def test_sla_repository_update_task_sla_fails_closed_cross_tenant() -> None:
    """update_task_sla raises SlaTaskStateNotFoundError when targeting another tenant."""
    session = InMemorySlaDbSession()
    repo = SlaRepository(session)  # type: ignore[arg-type]

    now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    # Create record in Tenant A
    state_a = TaskSlaState(
        task_id=task_id,
        instance_id=inst_id,
        tenant_id="tenant-A",
        policy_key="policy_1",
        status=SlaStatus.RUNNING,
        created_at=now,
        warning_at=now + timedelta(hours=1),
        breach_at=now + timedelta(hours=2),
    )
    await repo.create_task_sla(state_a)

    # Attempt to update the same task_id under Tenant B -> MUST FAIL CLOSED
    state_b_attempt = TaskSlaState(
        task_id=task_id,
        instance_id=inst_id,
        tenant_id="tenant-B",  # Malicious or confused tenant
        policy_key="policy_1",
        status=SlaStatus.PAUSED,
        created_at=now,
        warning_at=now + timedelta(hours=1),
        breach_at=now + timedelta(hours=2),
    )

    with pytest.raises(SlaTaskStateNotFoundError, match="not found in tenant 'tenant-B'"):
        await repo.update_task_sla(state_b_attempt)

    # Verify Tenant A record was NOT mutated
    rec_a = await repo.get_task_sla(tenant_id="tenant-A", task_id=task_id)
    assert rec_a is not None
    assert rec_a.status == "RUNNING"


# ==============================================================================
# 9. SLA Transaction Consistency, Rollback Eviction & Multi-Instance Safety
# ==============================================================================


@pytest.mark.asyncio
async def test_attach_task_sla_rollback_evicts_in_memory_state() -> None:
    """Commit failure in attach_task_sla rolls back DB and evicts in-memory state."""
    session = InMemorySlaDbSession()

    class FailingUow(SlaTestUnitOfWork):
        async def commit(self) -> None:
            raise RuntimeError("Simulated PostgreSQL commit conflict")

    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    service = WorkflowService(
        uow_factory=lambda: FailingUow(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )
    policy = SlaPolicyConfig(
        policy_key="rollback_policy",
        name="Rollback Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_sla_policy(policy)

    task_id = uuid4()
    inst_id = uuid4()
    with pytest.raises(RuntimeError, match="Simulated PostgreSQL commit conflict"):
        await service.attach_task_sla(
            tenant_id="tenant-rollback",
            instance_id=inst_id,
            task_id=task_id,
            policy_key="rollback_policy",
        )

    # In-memory cache must NOT retain phantom state
    assert task_id not in service._sla_service._states
    with pytest.raises(SlaTaskStateNotFoundError):
        service._sla_service.get_task_sla(task_id)


@pytest.mark.asyncio
async def test_pause_and_resume_task_sla_rollback_evicts_in_memory_state() -> None:
    """Commit failure in pause or resume evicts in-memory state and does not leave stale state."""
    session = InMemorySlaDbSession()
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)

    should_fail_commit = False

    class MaybeFailingUow(SlaTestUnitOfWork):
        async def commit(self) -> None:
            if should_fail_commit:
                raise RuntimeError("Simulated PostgreSQL commit error on pause")
            await super().commit()

    service = WorkflowService(
        uow_factory=lambda: MaybeFailingUow(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )
    policy = SlaPolicyConfig(
        policy_key="pr_rollback_policy",
        name="PR Rollback Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_sla_policy(policy)

    task_id = uuid4()
    inst_id = uuid4()
    # Initial attach succeeds
    await service.attach_task_sla(
        tenant_id="tenant-pr-rb",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="pr_rollback_policy",
    )
    assert task_id in service._sla_service._states

    # Fail pause commit
    should_fail_commit = True
    with pytest.raises(RuntimeError, match="Simulated PostgreSQL commit error on pause"):
        await service.pause_task_sla(
            tenant_id="tenant-pr-rb",
            task_id=task_id,
        )

    # In-memory state was evicted on failed commit
    assert task_id not in service._sla_service._states


@pytest.mark.asyncio
async def test_multi_instance_sla_cannot_overwrite_newer_database_state_with_stale_memory() -> None:
    """Pod 1 with stale in-memory state cannot overwrite Pod 2's authoritative DB state."""
    session = InMemorySlaDbSession()
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)

    # Pod 1
    pod1 = WorkflowService(
        uow_factory=lambda: SlaTestUnitOfWork(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )
    # Pod 2
    pod2 = WorkflowService(
        uow_factory=lambda: SlaTestUnitOfWork(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )

    policy = SlaPolicyConfig(
        policy_key="multi_inst_policy",
        name="Multi Instance Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    pod1.register_sla_policy(policy)
    pod2.register_sla_policy(policy)

    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    # Step 1: Pod 1 attaches SLA (status RUNNING)
    await pod1.attach_task_sla(
        tenant_id="tenant-multi",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="multi_inst_policy",
        created_at=t0,
    )
    assert pod1._sla_service.get_task_sla(task_id).status == SlaStatus.RUNNING

    # Step 2: Pod 2 pauses the SLA in PostgreSQL
    t_pause = t0 + timedelta(minutes=10)
    paused_state = await pod2.pause_task_sla(
        tenant_id="tenant-multi",
        task_id=task_id,
        paused_at=t_pause,
    )
    assert paused_state.status == SlaStatus.PAUSED

    # Authoritative record in PostgreSQL is PAUSED
    repo = SlaRepository(session)
    rec = await repo.get_task_sla("tenant-multi", task_id)
    assert rec is not None
    assert rec.status == "PAUSED"

    # Step 3: Pod 1 attempts an operation with DB session.
    # Because PostgreSQL is authoritative, Pod 1 reloads from DB rather than
    # using its stale RUNNING memory!
    t_resume = t0 + timedelta(minutes=20)
    resumed = await pod1.resume_task_sla(
        tenant_id="tenant-multi",
        task_id=task_id,
        resumed_at=t_resume,
    )
    assert resumed.status == SlaStatus.RUNNING
    # Paused duration correctly calculated from Pod 2's pause timestamp
    assert resumed.total_paused_seconds == 600


@pytest.mark.asyncio
async def test_synchronous_legacy_methods_fail_closed_in_database_persistence_mode() -> None:
    """When persistence_mode='database', synchronous SLA methods raise RuntimeError."""
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    session = InMemorySlaDbSession()
    service = WorkflowService(
        uow_factory=lambda: SlaTestUnitOfWork(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )
    policy = SlaPolicyConfig(
        policy_key="sync_closed_policy",
        name="Sync Closed Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_sla_policy(policy)

    task_id = uuid4()
    inst_id = uuid4()

    with pytest.raises(RuntimeError, match="persistence_mode='database'"):
        service.attach_task_sla_sync(
            tenant_id="tenant-sc",
            instance_id=inst_id,
            task_id=task_id,
            policy_key="sync_closed_policy",
        )

    with pytest.raises(RuntimeError, match="persistence_mode='database'"):
        service.pause_task_sla_sync(
            tenant_id="tenant-sc",
            task_id=task_id,
        )

    with pytest.raises(RuntimeError, match="persistence_mode='database'"):
        service.resume_task_sla_sync(
            tenant_id="tenant-sc",
            task_id=task_id,
        )


@pytest.mark.asyncio
async def test_workflow_service_get_task_sla_authoritative_reloading() -> None:
    """WorkflowService.get_task_sla queries authoritative database and synchronizes cache."""
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    session = InMemorySlaDbSession()
    service = WorkflowService(
        uow_factory=lambda: SlaTestUnitOfWork(session),  # type: ignore[arg-type]
        adapter=adapter,
        persistence_mode="database",
    )
    policy = SlaPolicyConfig(
        policy_key="reload_policy",
        name="Reload Policy",
        warning_duration_seconds=1800,
        breach_duration_seconds=3600,
    )
    service.register_sla_policy(policy)

    task_id = uuid4()
    inst_id = uuid4()
    t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)

    # 1. Attach SLA
    await service.attach_task_sla(
        tenant_id="tenant-auth",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="reload_policy",
        created_at=t0,
    )

    # 2. Directly update the DB record to simulate external mutation
    repo = SlaRepository(session)  # type: ignore[arg-type]
    rec = await repo.get_task_sla("tenant-auth", task_id)
    assert rec is not None
    rec.status = "PAUSED"
    rec.paused_at = t0 + timedelta(minutes=5)

    # 3. get_task_sla reloads from DB and synchronizes memory cache
    loaded = await service.get_task_sla("tenant-auth", task_id)
    assert loaded.status == SlaStatus.PAUSED
    assert service._sla_service.get_task_sla(task_id).status == SlaStatus.PAUSED

    # 4. If record deleted or wrong tenant, evicts from cache and raises NotFoundError
    other_task = uuid4()
    with pytest.raises(SlaTaskStateNotFoundError):
        await service.get_task_sla("tenant-auth", other_task)

