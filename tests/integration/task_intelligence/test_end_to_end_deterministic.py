"""Comprehensive live PostgreSQL integration tests for Phase 2A deterministic core.

Covers:
- Scenario 1 (Item 50): Representative end-to-end deterministic flow:
  trusted tenant -> create goal -> deterministic PlannerProtocol fixture -> persist GENERATED
  -> validate plan -> NetworkX DAG validation -> CPM -> VALIDATED -> APPROVAL_PENDING
  -> APPROVED frozen baseline -> outbox events -> append-only audit trail
- Scenario 2 (Item 51): Cycle negative flow:
  cyclic plan -> validation fails with explicit cycle path -> cannot reach VALIDATED
  -> cannot be approved -> zero plan.approved events
- Scenario 3 (Item 52): Cross-tenant negative isolation:
  Tenant A planning entities are completely inaccessible to Tenant B
"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEventRecord
from app.events.outbox.models import OutboxEventRecord
from app.task_intelligence.schemas import (
    DependencyDraft,
    GoalCreate,
    InvalidPlanStateTransitionError,
    MilestoneDraft,
    PlanSchema,
    PlanState,
    TaskDraft,
    ValidationIssueCode,
)
from app.task_intelligence.service import (
    PlanNotFoundError,
    PlanService,
)


class DeterministicExamPlanner:
    """Deterministic, provider-neutral planner fixture conforming to PlannerProtocol.

    Simulates the proposal of a semester examination plan without calling any LLM.
    """

    async def generate_plan(
        self,
        goal: GoalCreate,
        context: dict[str, Any] | None = None,
    ) -> PlanSchema:
        return PlanSchema(
            plan_name="Grade 10 Semester Examination Operations Plan",
            tasks=[
                TaskDraft(
                    temporary_id="T1_PLANNING",
                    title="Define examination schedule and guidelines",
                    work_category="PLANNING",
                    estimated_duration_minutes=120,
                ),
                TaskDraft(
                    temporary_id="T2_QP_DRAFT",
                    title="Draft question papers for major subjects",
                    work_category="QUESTION_PAPER_READINESS",
                    estimated_duration_minutes=180,
                ),
                TaskDraft(
                    temporary_id="T3_QP_REVIEW",
                    title="Review and approve question papers",
                    work_category="APPROVALS",
                    estimated_duration_minutes=90,
                ),
                TaskDraft(
                    temporary_id="T4_ROOM_PREP",
                    title="Prepare and sanitize examination halls",
                    work_category="ROOM_PREPARATION",
                    estimated_duration_minutes=60,
                ),
                TaskDraft(
                    temporary_id="T5_INVIGILATOR_BRIEF",
                    title="Brief invigilation roster staff",
                    work_category="INVIGILATOR_PREPARATION",
                    estimated_duration_minutes=60,
                ),
                TaskDraft(
                    temporary_id="T6_EXAM_CONDUCT",
                    title="Conduct examination sessions",
                    work_category="EXAM_EXECUTION",
                    estimated_duration_minutes=240,
                ),
                TaskDraft(
                    temporary_id="T7_CLOSURE",
                    title="Collate answer booklets and seal records",
                    work_category="CLOSURE",
                    estimated_duration_minutes=120,
                ),
            ],
            dependencies=[
                DependencyDraft(predecessor_id="T1_PLANNING", successor_id="T2_QP_DRAFT"),
                DependencyDraft(predecessor_id="T2_QP_DRAFT", successor_id="T3_QP_REVIEW"),
                DependencyDraft(predecessor_id="T1_PLANNING", successor_id="T4_ROOM_PREP"),
                DependencyDraft(predecessor_id="T1_PLANNING", successor_id="T5_INVIGILATOR_BRIEF"),
                DependencyDraft(predecessor_id="T3_QP_REVIEW", successor_id="T6_EXAM_CONDUCT"),
                DependencyDraft(predecessor_id="T4_ROOM_PREP", successor_id="T6_EXAM_CONDUCT"),
                DependencyDraft(
                    predecessor_id="T5_INVIGILATOR_BRIEF", successor_id="T6_EXAM_CONDUCT"
                ),
                DependencyDraft(predecessor_id="T6_EXAM_CONDUCT", successor_id="T7_CLOSURE"),
            ],
            milestones=[
                MilestoneDraft(
                    temporary_id="M1_READINESS",
                    name="Question Paper & Room Readiness",
                    associated_task_ids=["T3_QP_REVIEW", "T4_ROOM_PREP", "T5_INVIGILATOR_BRIEF"],
                ),
                MilestoneDraft(
                    temporary_id="M2_CLOSURE",
                    name="Examination Operations Completed",
                    associated_task_ids=["T7_CLOSURE"],
                ),
            ],
        )


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_representative_end_to_end_deterministic_flow(
    uow_factory: Any,
    test_engine: Any,
) -> None:
    """Test full deterministic lifecycle from goal intake to frozen approved baseline."""
    planner = DeterministicExamPlanner()
    service = PlanService(uow_factory=uow_factory, planner=planner)
    tenant_id = f"tenant-e2e-{uuid4().hex[:8]}"
    actor_id = "principal-user-01"

    # Step 1: Goal Intake
    goal_input = GoalCreate(
        objective="Plan and conduct Grade 10 semester examinations",
        scope="End-to-end semester examination operations",
        constraints=["1 day gap between major exams", "Human approval required"],
        resource_scope=["ROOM", "INVIGILATOR"],
    )
    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=goal_input,
        actor_id=actor_id,
    )
    assert goal.id is not None
    assert goal.tenant_id == tenant_id

    # Step 2: Plan Generation via deterministic PlannerProtocol fixture
    plan_schema = await planner.generate_plan(goal_input)
    assert len(plan_schema.tasks) == 7
    assert len(plan_schema.dependencies) == 8

    # Step 3: Persist GENERATED plan
    plan_record = await service.persist_generated_plan(
        tenant_id=tenant_id,
        goal_id=goal.id,
        plan_schema=plan_schema,
        actor_id=actor_id,
    )
    assert plan_record.status == PlanState.GENERATED
    assert plan_record.version_number == 1
    assert plan_record.is_baseline is False

    # Step 4: Deterministic Validation & CPM
    validation_res = await service.validate_plan(
        tenant_id=tenant_id,
        plan_id=plan_record.id,
        actor_id=actor_id,
    )
    assert validation_res.is_valid is True
    assert validation_res.cycle_path is None
    assert len(validation_res.topological_order or []) == 7
    assert validation_res.critical_path is not None
    # Verify critical path calculation: T1 -> T2 -> T3 -> T6 -> T7
    # Calibrated Category Durations: 120 + 180 + 60 + 180 + 60 = 600 minutes
    assert validation_res.critical_path == [
        "T1_PLANNING",
        "T2_QP_DRAFT",
        "T3_QP_REVIEW",
        "T6_EXAM_CONDUCT",
        "T7_CLOSURE",
    ]

    # Verify plan advanced to VALIDATED in database
    plan_validated = await service.get_plan(tenant_id=tenant_id, plan_id=plan_record.id)
    assert plan_validated is not None
    assert plan_validated.status == PlanState.VALIDATED

    # Step 5: Critical Path Query
    cpm_res = await service.get_critical_path(tenant_id=tenant_id, plan_id=plan_record.id)
    assert cpm_res.project_duration_minutes == 600
    assert cpm_res.critical_path == validation_res.critical_path

    # Step 6: Submit for Approval
    plan_pending = await service.submit_for_approval(
        tenant_id=tenant_id,
        plan_id=plan_record.id,
        actor_id=actor_id,
    )
    assert plan_pending.status == PlanState.APPROVAL_PENDING

    # Step 7: Human Approval -> Freezes Baseline
    approver_id = "board-superintendent-99"
    approved_plan = await service.approve_plan(
        tenant_id=tenant_id,
        plan_id=plan_record.id,
        approver_id=approver_id,
    )
    assert approved_plan.status == PlanState.APPROVED
    assert approved_plan.is_baseline is True
    assert approved_plan.baseline_hash is not None

    # Step 8: Verify database state, outbox events, and audit logs
    async with test_engine.connect() as conn:
        # 1. Outbox events: goal.created, plan.generated, and plan.approved
        outbox_rows = (
            await conn.execute(
                select(OutboxEventRecord.envelope)
                .where(OutboxEventRecord.envelope["tenant_id"].astext == tenant_id)
                .order_by(OutboxEventRecord.created_at)
            )
        ).fetchall()
        assert len(outbox_rows) == 3
        event_types = [r[0]["event_type"] for r in outbox_rows]
        assert event_types == ["goal.created", "plan.generated", "plan.approved"]

        # Minimal data payload check
        approved_evt = [r[0] for r in outbox_rows if r[0]["event_type"] == "plan.approved"][0]
        assert approved_evt["payload"]["baseline_hash"] == approved_plan.baseline_hash
        assert approved_evt["payload"]["approver_id"] == approver_id

        # 2. Audit records: goal.created, plan.generated, plan.validated,
        #    plan.approval_submitted, plan.approved
        audit_rows = (
            await conn.execute(
                select(AuditEventRecord.event_type)
                .where(AuditEventRecord.tenant_id == tenant_id)
                .order_by(AuditEventRecord.recorded_at)
            )
        ).fetchall()
        audit_types = [r[0] for r in audit_rows]
        assert "goal.created" in audit_types
        assert "plan.generated" in audit_types
        assert "plan.validated" in audit_types
        assert "plan.approval_submitted" in audit_types
        assert "plan.approved" in audit_types


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_cycle_negative_flow(
    uow_factory: Any,
    test_engine: Any,
) -> None:
    """Verify that a plan with a cycle cannot reach VALIDATED and cannot be approved."""
    service = PlanService(uow_factory=uow_factory)
    tenant_id = f"tenant-cycle-{uuid4().hex[:8]}"

    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=GoalCreate(objective="Exams with cyclic error"),
    )

    # Cyclic plan: T1 -> T2 -> T3 -> T1
    cyclic_plan = PlanSchema(
        plan_name="Corrupt Cyclic Plan",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=30),
            TaskDraft(temporary_id="T2", title="Task 2", estimated_duration_minutes=45),
            TaskDraft(temporary_id="T3", title="Task 3", estimated_duration_minutes=60),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
            DependencyDraft(predecessor_id="T2", successor_id="T3"),
            DependencyDraft(predecessor_id="T3", successor_id="T1"),
        ],
    )

    plan_rec = await service.persist_generated_plan(
        tenant_id=tenant_id,
        goal_id=goal.id,
        plan_schema=cyclic_plan,
    )

    # Validation must fail and return explicit cycle path
    val_res = await service.validate_plan(tenant_id=tenant_id, plan_id=plan_rec.id)
    assert val_res.is_valid is False
    assert val_res.cycle_path is not None
    assert val_res.cycle_path[0] == val_res.cycle_path[-1]
    assert set(val_res.cycle_path) == {"T1", "T2", "T3"}
    assert any(i.code == ValidationIssueCode.CYCLE_DETECTED for i in val_res.issues)

    # Status must NOT transition to VALIDATED
    curr_plan = await service.get_plan(tenant_id=tenant_id, plan_id=plan_rec.id)
    assert curr_plan is not None
    assert curr_plan.status == PlanState.GENERATED

    # Attempt to submit for approval must fail
    with pytest.raises(InvalidPlanStateTransitionError):
        await service.submit_for_approval(tenant_id=tenant_id, plan_id=plan_rec.id)

    # Attempt to approve must fail
    with pytest.raises(InvalidPlanStateTransitionError):
        await service.approve_plan(
            tenant_id=tenant_id,
            plan_id=plan_rec.id,
            approver_id="unauthorized",
        )

    # Verify zero plan.approved events in outbox
    async with test_engine.connect() as conn:
        approved_events = (
            await conn.execute(
                select(OutboxEventRecord).where(
                    OutboxEventRecord.envelope["tenant_id"].astext == tenant_id,
                    OutboxEventRecord.envelope["event_type"].astext == "plan.approved",
                )
            )
        ).fetchall()
        assert len(approved_events) == 0


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_cross_tenant_negative_isolation(
    uow_factory: Any,
) -> None:
    """Verify that Tenant A's planning entities cannot be read,
    validated, or approved by Tenant B.
    """
    service = PlanService(uow_factory=uow_factory)
    tenant_a = f"tenant-a-{uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid4().hex[:8]}"

    # Tenant A creates goal and plan
    goal_a = await service.create_goal(
        tenant_id=tenant_a,
        goal_data=GoalCreate(objective="Tenant A Confidential Exams"),
    )
    plan_a = await service.persist_generated_plan(
        tenant_id=tenant_a,
        goal_id=goal_a.id,
        plan_schema=PlanSchema(
            plan_name="Tenant A Plan",
            tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        ),
    )

    # 1. Tenant B cannot read Tenant A's plan
    plan_read = await service.get_plan(tenant_id=tenant_b, plan_id=plan_a.id)
    assert plan_read is None

    # 2. Tenant B cannot validate Tenant A's plan
    with pytest.raises(PlanNotFoundError):
        await service.validate_plan(tenant_id=tenant_b, plan_id=plan_a.id)

    # 3. Tenant B cannot submit Tenant A's plan for approval
    with pytest.raises(PlanNotFoundError):
        await service.submit_for_approval(tenant_id=tenant_b, plan_id=plan_a.id)

    # 4. Tenant B cannot approve Tenant A's plan
    with pytest.raises(PlanNotFoundError):
        await service.approve_plan(
            tenant_id=tenant_b,
            plan_id=plan_a.id,
            approver_id="intruder",
        )

    # 5. Tenant B cannot get critical path for Tenant A's plan
    with pytest.raises(PlanNotFoundError):
        await service.get_critical_path(tenant_id=tenant_b, plan_id=plan_a.id)


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_historical_estimation_postgres_callchain(
    uow_factory: Any,
) -> None:
    """PostgreSQL integration test for FR-TI-04:
    Completed task actuals -> historical aggregation -> new task estimate -> persisted plan output.
    Verifies tenant isolation and fallback behavior.
    """
    from app.task_intelligence.progress import TaskProgressStatus, TaskProgressUpdate

    service = PlanService(uow_factory=uow_factory)
    tenant_a = f"tenant-hist-a-{uuid4().hex[:8]}"
    tenant_b = f"tenant-hist-b-{uuid4().hex[:8]}"

    # Step 1: Tenant A creates initial plan with a task in PLANNING category
    goal_a = await service.create_goal(
        tenant_id=tenant_a,
        goal_data=GoalCreate(objective="Semester Exam Preparation"),
    )
    plan_a1 = await service.persist_generated_plan(
        tenant_id=tenant_a,
        goal_id=goal_a.id,
        plan_schema=PlanSchema(
            plan_name="Exam Setup Plan 1",
            tasks=[
                TaskDraft(
                    temporary_id="T1_PLAN",
                    title="Planning session",
                    work_category="PLANNING",
                    estimated_duration_minutes=120,
                )
            ],
        ),
    )

    # Step 2: Tenant A completes the task with actual_duration_minutes = 75
    detail_a1 = await service.get_plan(tenant_id=tenant_a, plan_id=plan_a1.id)
    assert detail_a1 is not None and detail_a1.tasks
    task_a1 = detail_a1.tasks[0]

    await service.update_task_progress(
        tenant_id=tenant_a,
        task_id=task_a1.id,
        update_data=TaskProgressUpdate(
            status=TaskProgressStatus.COMPLETED,
            progress_percentage=100,
            actual_duration_minutes=75,
            completion_evidence="https://audit.internal/evidence/task_a1_done.pdf",
        ),
    )

    # Verify FR-TI-08 persistence under PostgreSQL
    detail_a1_updated = await service.get_plan(tenant_id=tenant_a, plan_id=plan_a1.id)
    assert detail_a1_updated is not None
    assert detail_a1_updated.tasks[0].completion_evidence == "https://audit.internal/evidence/task_a1_done.pdf"
    assert detail_a1_updated.tasks[0].actual_duration_minutes == 75
    assert detail_a1_updated.tasks[0].variance_minutes == -45  # 75 actual - 120 estimated

    # Step 3: Tenant A persists a second plan with a new task in PLANNING
    plan_a2 = await service.persist_generated_plan(
        tenant_id=tenant_a,
        goal_id=goal_a.id,
        plan_schema=PlanSchema(
            plan_name="Exam Setup Plan 2",
            tasks=[
                TaskDraft(
                    temporary_id="T2_PLAN",
                    title="Follow-up planning session",
                    work_category="PLANNING",
                    estimated_duration_minutes=120,
                )
            ],
        ),
    )

    detail_a2 = await service.get_plan(tenant_id=tenant_a, plan_id=plan_a2.id)
    assert detail_a2 is not None and detail_a2.tasks
    task_a2 = detail_a2.tasks[0]

    # Verify task was calibrated to historical median (75 minutes)
    assert task_a2.estimated_duration_minutes == 75
    assert task_a2.effort_estimate_json is not None
    assert task_a2.effort_estimate_json["basis"] == "historical_median"
    assert task_a2.effort_estimate_json["confidence"] == 0.85

    # Step 4: Tenant B persists a plan with a PLANNING task (has zero completed tasks)
    goal_b = await service.create_goal(
        tenant_id=tenant_b,
        goal_data=GoalCreate(objective="Tenant B Examinations"),
    )
    plan_b = await service.persist_generated_plan(
        tenant_id=tenant_b,
        goal_id=goal_b.id,
        plan_schema=PlanSchema(
            plan_name="Tenant B Plan",
            tasks=[
                TaskDraft(
                    temporary_id="TB_PLAN",
                    title="Tenant B planning",
                    work_category="PLANNING",
                    estimated_duration_minutes=60,
                )
            ],
        ),
    )

    detail_b = await service.get_plan(tenant_id=tenant_b, plan_id=plan_b.id)
    assert detail_b is not None and detail_b.tasks
    task_b = detail_b.tasks[0]

    # Tenant B must NOT use Tenant A's 75m median; falls back to category heuristic (120m)
    assert task_b.estimated_duration_minutes == 120
    assert task_b.effort_estimate_json is not None
    assert task_b.effort_estimate_json["basis"] == "category_heuristic:PLANNING"
    assert task_b.effort_estimate_json["confidence"] == 0.7

