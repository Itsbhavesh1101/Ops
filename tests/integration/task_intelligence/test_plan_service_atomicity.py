"""Live PostgreSQL atomicity tests for PlanService (Phase 2A Stage D).

Verifies that:
- Plan state + audit events + outbox events commit atomically.
- On exception / transaction rollback, zero partial state, audit, or outbox records persist.
- Approval freezes baseline and atomically emits plan.approved outbox event and audit record.
"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEventRecord
from app.db.models.planning import PlanRecord, TaskRecord
from app.events.outbox.models import OutboxEventRecord
from app.task_intelligence.schemas import (
    DependencyDraft,
    GoalCreate,
    PlanSchema,
    PlanState,
    TaskDraft,
)
from app.task_intelligence.service import PlanService


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_plan_generation_atomicity_commit(uow_factory: Any, test_engine: Any) -> None:
    service = PlanService(uow_factory=uow_factory)
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    # 1. Create Goal
    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=GoalCreate(objective="Conduct semester exams"),
    )

    # 2. Persist Generated Plan
    plan_schema = PlanSchema(
        plan_name="Exam Plan V1",
        goal_id=goal.id,
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=30),
            TaskDraft(temporary_id="T2", title="Task 2", estimated_duration_minutes=45),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
        ],
    )

    plan = await service.persist_generated_plan(
        tenant_id=tenant_id,
        goal_id=goal.id,
        plan_schema=plan_schema,
    )

    # 3. Verify in database: plan record, tasks, audit event, outbox event all present
    async with test_engine.connect() as conn:
        # Plan exists
        p_row = await conn.execute(select(PlanRecord).where(PlanRecord.id == plan.id))
        assert p_row.scalar_one_or_none() is not None

        # Tasks exist
        t_rows = await conn.execute(select(TaskRecord).where(TaskRecord.plan_id == plan.id))
        assert len(list(t_rows.fetchall())) == 2

        # Outbox event exists
        o_rows = await conn.execute(
            select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["subject_id"].astext == str(plan.id)
            )
        )
        outbox_records = list(o_rows.fetchall())
        assert len(outbox_records) == 1
        assert outbox_records[0][1]["event_type"] == "plan.generated"

        # Audit event exists
        a_rows = await conn.execute(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == str(plan.id),
                AuditEventRecord.event_type == "plan.generated",
            )
        )
        assert len(list(a_rows.fetchall())) == 1


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_plan_generation_atomicity_rollback_on_failure(
    uow_factory: Any, test_engine: Any
) -> None:
    service = PlanService(uow_factory=uow_factory)
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    # Non-existent goal triggers GoalNotFoundError inside transaction
    ghost_goal_id = uuid4()
    plan_schema = PlanSchema(
        plan_name="Failing Plan",
        goal_id=ghost_goal_id,
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
    )

    from app.task_intelligence.service import GoalNotFoundError

    with pytest.raises(GoalNotFoundError):
        await service.persist_generated_plan(
            tenant_id=tenant_id,
            goal_id=ghost_goal_id,
            plan_schema=plan_schema,
        )

    # Verify zero partial state in database
    async with test_engine.connect() as conn:
        p_rows = await conn.execute(select(PlanRecord).where(PlanRecord.tenant_id == tenant_id))
        assert len(list(p_rows.fetchall())) == 0

        a_rows = await conn.execute(
            select(AuditEventRecord).where(AuditEventRecord.tenant_id == tenant_id)
        )
        assert len(list(a_rows.fetchall())) == 0

        o_rows = await conn.execute(
            select(OutboxEventRecord).where(
                OutboxEventRecord.envelope["tenant_id"].astext == tenant_id
            )
        )
        assert len(list(o_rows.fetchall())) == 0


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_plan_approval_freezes_baseline_and_emits_event(
    uow_factory: Any, test_engine: Any
) -> None:
    service = PlanService(uow_factory=uow_factory)
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    # 1. Goal
    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=GoalCreate(objective="Conduct semester exams"),
    )

    # 2. Plan
    plan_schema = PlanSchema(
        plan_name="Exam Baseline Plan",
        goal_id=goal.id,
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=30),
            TaskDraft(temporary_id="T2", title="Task 2", estimated_duration_minutes=45),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
        ],
    )
    plan = await service.persist_generated_plan(
        tenant_id=tenant_id,
        goal_id=goal.id,
        plan_schema=plan_schema,
    )

    # 3. Validate
    val_res = await service.validate_plan(tenant_id=tenant_id, plan_id=plan.id)
    assert val_res.is_valid is True

    # 4. Submit for approval
    await service.submit_for_approval(tenant_id=tenant_id, plan_id=plan.id)

    # 5. Approve
    approved = await service.approve_plan(
        tenant_id=tenant_id,
        plan_id=plan.id,
        approver_id="principal-lead-01",
    )
    assert approved.status == PlanState.APPROVED
    assert approved.is_baseline is True
    assert approved.baseline_hash is not None

    # 6. Verify database atomicity of approval
    async with test_engine.connect() as conn:
        # Check plan is approved & baseline
        p_row = (
            await conn.execute(
                select(PlanRecord.status, PlanRecord.is_baseline, PlanRecord.baseline_hash).where(
                    PlanRecord.id == plan.id
                )
            )
        ).one()
        assert p_row.status == "APPROVED"
        assert p_row.is_baseline is True
        assert p_row.baseline_hash == approved.baseline_hash

        # Check outbox event plan.approved
        o_rows = (
            await conn.execute(
                select(OutboxEventRecord).where(
                    OutboxEventRecord.envelope["subject_id"].astext == str(plan.id),
                    OutboxEventRecord.envelope["event_type"].astext == "plan.approved",
                )
            )
        ).fetchall()
        assert len(o_rows) == 1
        assert o_rows[0][1]["payload"]["approver_id"] == "principal-lead-01"

        # Check audit event plan.approved
        a_rows = (
            await conn.execute(
                select(AuditEventRecord).where(
                    AuditEventRecord.resource_id == str(plan.id),
                    AuditEventRecord.event_type == "plan.approved",
                )
            )
        ).fetchall()
        assert len(a_rows) == 1
        assert a_rows[0].actor_principal_id == "principal-lead-01"


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_goal_creation_atomicity_commit(uow_factory: Any, test_engine: Any) -> None:
    service = PlanService(uow_factory=uow_factory)
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    correlation_id = f"corr-{uuid4().hex[:8]}"

    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=GoalCreate(objective="Conduct semester exams", scope="All schools"),
        actor_id="operator-01",
        correlation_id=correlation_id,
    )

    async with test_engine.connect() as conn:
        o_rows = (
            await conn.execute(
                select(OutboxEventRecord).where(
                    OutboxEventRecord.envelope["subject_id"].astext == str(goal.id),
                    OutboxEventRecord.envelope["event_type"].astext == "goal.created",
                )
            )
        ).fetchall()
        assert len(o_rows) == 1
        envelope = o_rows[0][1]
        assert envelope["event_type"] == "goal.created"
        assert envelope["tenant_id"] == tenant_id
        assert envelope["correlation_id"] == correlation_id
        assert envelope["payload"]["goal_id"] == str(goal.id)

        a_rows = (
            await conn.execute(
                select(AuditEventRecord).where(
                    AuditEventRecord.resource_id == str(goal.id),
                    AuditEventRecord.event_type == "goal.created",
                )
            )
        ).fetchall()
        assert len(a_rows) == 1
        assert a_rows[0].correlation_id == correlation_id
