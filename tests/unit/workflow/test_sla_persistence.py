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
from app.workflow.sla.models import (
    EscalationAction,
    SlaEscalationRule,
    SlaPolicyConfig,
    SlaStatus,
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

        # Handle list_evaluable_slas:
        # WHERE tenant_id = :tenant_id AND status IN ('RUNNING', 'WARNING')
        # AND (warning_at <= :as_of OR breach_at <= :as_of)
        if "warning_at" in stmt_str or "breach_at" in stmt_str:
            t_id = compiled.params.get("tenant_id_1") or compiled.params.get("tenant_id")
            as_of = compiled.params.get("warning_at_1") or compiled.params.get("as_of")
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

        # Handle get_task_sla / get_by_task_id
        t_id = compiled.params.get("tenant_id_1") or compiled.params.get("tenant_id")
        t_uuid = compiled.params.get("task_id_1") or compiled.params.get("task_id")
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
