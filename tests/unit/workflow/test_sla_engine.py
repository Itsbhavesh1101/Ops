"""Unit tests for the SLA Policy Engine, business calendar calculations, and escalation triggers."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.workflow.sla.models import (
    EscalationAction,
    SlaEscalationRule,
    SlaPolicyConfig,
    SlaStatus,
)
from app.workflow.sla.service import SlaService


class DummyOutboxRepo:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


class DummyEnqueueOutboxRepo:
    def __init__(self):
        self.events = []

    async def enqueue(self, event):
        self.events.append(event)


class DummyAuditRepo:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


@pytest.fixture
def sla_service():
    service = SlaService()
    policy = SlaPolicyConfig(
        policy_key="standard_approval_sla",
        name="Standard Approval SLA",
        warning_duration_seconds=18 * 3600,
        breach_duration_seconds=24 * 3600,
        calendar_ref="CALENDAR_24X7",
        warning_escalation=SlaEscalationRule(
            action=EscalationAction.REMINDER,
            target_role="supervisor",
        ),
        breach_escalation=SlaEscalationRule(
            action=EscalationAction.REASSIGN,
            target_role="escalation_pool",
        ),
    )
    service.register_policy(policy)
    return service


def test_sla_attach_calculates_thresholds(sla_service):
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    state = sla_service.attach_task_sla(
        tenant_id="tenant_alpha",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="standard_approval_sla",
        created_at=base_time,
    )

    assert state.status == SlaStatus.RUNNING
    assert state.warning_at == base_time + timedelta(hours=18)
    assert state.breach_at == base_time + timedelta(hours=24)


def test_sla_pause_and_resume_shifts_deadline(sla_service):
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    sla_service.attach_task_sla(
        tenant_id="tenant_alpha",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="standard_approval_sla",
        created_at=base_time,
    )

    # Pause 5 hours in
    pause_time = base_time + timedelta(hours=5)
    paused_state = sla_service.pause_task_sla(
        tenant_id="tenant_alpha",
        task_id=task_id,
        paused_at=pause_time,
    )
    assert paused_state.status == SlaStatus.PAUSED
    assert paused_state.paused_at == pause_time

    # Resume 3 hours later
    resume_time = pause_time + timedelta(hours=3)
    resumed_state = sla_service.resume_task_sla(
        tenant_id="tenant_alpha",
        task_id=task_id,
        resumed_at=resume_time,
    )
    assert resumed_state.status == SlaStatus.RUNNING
    assert resumed_state.paused_at is None
    # Deadlines shifted forward by 3 hours
    assert resumed_state.breach_at == base_time + timedelta(hours=24 + 3)
    assert resumed_state.warning_at == base_time + timedelta(hours=18 + 3)


@pytest.mark.asyncio
async def test_sla_evaluation_triggers_warning_and_breach(sla_service):
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    sla_service.attach_task_sla(
        tenant_id="tenant_alpha",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="standard_approval_sla",
        created_at=base_time,
    )

    outbox = DummyOutboxRepo()
    audit = DummyAuditRepo()

    # 1. Evaluate at 10h (before warning) -> 0 triggers
    res1 = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=10),
        outbox_repo=outbox,
        audit_repo=audit,
    )
    assert len(res1) == 0
    assert len(outbox.events) == 0

    # 2. Evaluate at 19h (warning threshold reached) -> warning emitted
    res2 = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=19),
        outbox_repo=outbox,
        audit_repo=audit,
    )
    assert len(res2) == 1
    assert "WARNING" in res2[0]
    assert len(outbox.events) == 1
    assert outbox.events[0].event_type == "workflow.sla.warning"
    assert outbox.events[0].tenant_id == "tenant_alpha"

    # 3. Evaluate at 20h (idempotent, warning not re-triggered)
    res3 = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=20),
        outbox_repo=outbox,
        audit_repo=audit,
    )
    assert len(res3) == 0
    assert len(outbox.events) == 1

    # 4. Evaluate at 25h (breached) -> breach emitted
    res4 = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=25),
        outbox_repo=outbox,
        audit_repo=audit,
    )
    assert len(res4) == 1
    assert "BREACH" in res4[0]
    assert len(outbox.events) == 2
    assert outbox.events[1].event_type == "workflow.sla.breached"
    assert outbox.events[1].tenant_id == "tenant_alpha"


def test_business_calendar_5x8_pause_resume_weekend():
    """Verify that 5x8 calendar preserves remaining business hours across weekend pause/resume."""
    service = SlaService()
    policy = SlaPolicyConfig(
        policy_key="business_5x8_sla",
        name="5x8 Business Hours SLA",
        warning_duration_seconds=4 * 3600,
        breach_duration_seconds=8 * 3600,
        calendar_ref="DEFAULT_5X8",
    )
    service.register_policy(policy)

    fri_start = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    state = service.attach_task_sla(
        tenant_id="tenant_beta",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="business_5x8_sla",
        created_at=fri_start,
    )
    assert state.warning_at == datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    assert state.breach_at == datetime(2026, 10, 5, 10, 0, tzinfo=UTC)

    fri_pause = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    service.pause_task_sla(tenant_id="tenant_beta", task_id=task_id, paused_at=fri_pause)
    assert state.remaining_breach_seconds == 2 * 3600

    mon_resume = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
    resumed = service.resume_task_sla(
        tenant_id="tenant_beta", task_id=task_id, resumed_at=mon_resume
    )
    assert resumed.status == SlaStatus.RUNNING
    assert resumed.breach_at == datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sla_evaluation_propagates_tenant_id_and_supports_enqueue(sla_service):
    """Verify tenant_id propagation to outbox events and compatibility with enqueue() repos."""
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    sla_service.attach_task_sla(
        tenant_id="tenant_gamma",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="standard_approval_sla",
        created_at=base_time,
    )

    outbox = DummyEnqueueOutboxRepo()
    audit = DummyAuditRepo()

    # Trigger warning
    res = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=19),
        outbox_repo=outbox,
        audit_repo=audit,
        tenant_id="tenant_gamma",
    )
    assert len(res) == 1
    assert len(outbox.events) == 1
    assert outbox.events[0].tenant_id == "tenant_gamma"
    assert outbox.events[0].event_type == "workflow.sla.warning"

    # Trigger breach
    res_breach = await sla_service.evaluate_triggers(
        as_of=base_time + timedelta(hours=25),
        outbox_repo=outbox,
        audit_repo=audit,
        tenant_id="tenant_gamma",
    )
    assert len(res_breach) == 1
    assert len(outbox.events) == 2
    assert outbox.events[1].tenant_id == "tenant_gamma"
    assert outbox.events[1].event_type == "workflow.sla.breached"


def test_sla_service_transaction_rolls_back_mutations_on_error(sla_service):
    """SlaService.transaction context manager restores snapshot on exception."""
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    task_id = uuid4()
    inst_id = uuid4()

    state = sla_service.attach_task_sla(
        tenant_id="tenant_epsilon",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="standard_approval_sla",
        created_at=base_time,
    )
    assert state.warning_fired_at is None
    assert state.status == SlaStatus.RUNNING

    with pytest.raises(RuntimeError, match="Simulated crash"):
        with sla_service.transaction(tenant_id="tenant_epsilon"):
            state.warning_fired_at = base_time + timedelta(hours=19)
            state.status = SlaStatus.WARNING
            raise RuntimeError("Simulated crash")

    # State was restored
    restored = sla_service.get_task_sla(task_id)
    assert restored.warning_fired_at is None
    assert restored.status == SlaStatus.RUNNING


@pytest.mark.asyncio
async def test_sla_service_evaluate_triggers_rejects_empty_or_whitespace_tenant_id(sla_service):
    """SlaService.evaluate_triggers rejects empty or whitespace-only tenant_id."""
    base_time = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    outbox = DummyEnqueueOutboxRepo()
    audit = DummyAuditRepo()

    with pytest.raises(ValueError, match="tenant_id must not be empty or whitespace-only"):
        await sla_service.evaluate_triggers(
            as_of=base_time,
            outbox_repo=outbox,
            audit_repo=audit,
            tenant_id="",
        )

    with pytest.raises(ValueError, match="tenant_id must not be empty or whitespace-only"):
        await sla_service.evaluate_triggers(
            as_of=base_time,
            outbox_repo=outbox,
            audit_repo=audit,
            tenant_id="   ",
        )



