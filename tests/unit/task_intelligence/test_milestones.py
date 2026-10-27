"""Authoritative unit test suite for Milestone Creation and Health Tracking (FR-TI-06).

Verifies:
- MilestoneDraft schema definition and validation
- Plan validation of milestones (duplicate IDs, unknown task associations)
- Milestone target date derivation and CPM latest finish alignment
- Milestone health indicators and progress percentage roll-up from member tasks
- MilestoneRecord database model mapping integrity
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.db.models.planning import MilestoneRecord, TaskRecord
from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.schemas import (
    MilestoneDraft,
    PlanSchema,
    TaskDraft,
    ValidationIssueCode,
)
from app.task_intelligence.validators import PlanValidator


def test_milestone_draft_schema_validation() -> None:
    """MilestoneDraft requires temporary_id and name, and allows target_date."""
    # Valid milestone draft
    target = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
    m = MilestoneDraft(
        temporary_id="M1_READINESS",
        name="Question Paper Readiness",
        description="All moderation and printing completed",
        target_date=target,
        associated_task_ids=["T1_MODERATE", "T2_PRINT"],
    )
    assert m.temporary_id == "M1_READINESS"
    assert m.name == "Question Paper Readiness"
    assert m.target_date == target
    assert len(m.associated_task_ids) == 2

    # Missing temporary_id or name fails
    with pytest.raises(ValidationError):
        MilestoneDraft(temporary_id="", name="Invalid Milestone")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        MilestoneDraft(temporary_id="M_EMPTY", name="")  # type: ignore[call-arg]


def test_milestone_plan_validator_duplicate_id() -> None:
    """PlanValidator catches duplicate milestone temporary IDs."""
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Duplicate Milestone Plan",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        milestones=[
            MilestoneDraft(temporary_id="M1", name="Milestone Alpha", associated_task_ids=["T1"]),
            MilestoneDraft(
                temporary_id="M1",
                name="Milestone Alpha Duplicate",
                associated_task_ids=["T1"],
            ),
        ],
    )
    result = validator.validate(plan)
    assert not result.is_valid
    assert any(i.code == ValidationIssueCode.DUPLICATE_MILESTONE_ID for i in result.issues)


def test_milestone_plan_validator_unknown_task_reference() -> None:
    """PlanValidator catches milestone referencing a task that does not exist in plan."""
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Unknown Task Milestone Plan",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        milestones=[
            MilestoneDraft(
                temporary_id="M1",
                name="Milestone Alpha",
                associated_task_ids=["T1", "T_NONEXISTENT"],
            )
        ],
    )
    result = validator.validate(plan)
    assert not result.is_valid
    issue = next(i for i in result.issues if i.code == ValidationIssueCode.UNKNOWN_MILESTONE_TASK)
    assert "T_NONEXISTENT" in issue.message


def test_milestone_target_date_derivation_from_cpm() -> None:
    """Milestone target completion matches the maximum latest finish of its member tasks."""
    graph = TaskDependencyGraph()
    graph.add_task("T1_SETUP", duration_minutes=60)
    graph.add_task("T2_EXECUTE", duration_minutes=120)
    graph.add_dependency("T1_SETUP", "T2_EXECUTE")

    cpm = CriticalPathCalculator.calculate(graph)

    # Member tasks for Milestone "M_EXEC" are T1 and T2
    member_task_ids = ["T1_SETUP", "T2_EXECUTE"]
    latest_finish_minutes = max(
        cpm.schedule[tid].late_finish for tid in member_task_ids if tid in cpm.schedule
    )
    # T1 finish is 60; T2 finish is 180
    assert latest_finish_minutes == 180


def test_milestone_health_rollup_evaluation() -> None:
    """Milestone health rolls up status of associated member tasks:
    - ALL COMPLETED -> COMPLETED / 100%
    - ANY BLOCKED -> AT_RISK
    - OVERDUE (now > target_date and < 100%) -> AT_RISK
    - In-progress without blockers -> ON_TRACK
    - No tasks or 0% progress -> PENDING
    """
    from datetime import timedelta

    from app.task_intelligence.milestones import MilestoneHealth, MilestoneHealthCalculator

    plan_id = uuid4()
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    # 1. All tasks completed -> COMPLETED
    m1 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M1",
        name="All Done",
        target_date=now + timedelta(days=1),
    )
    t1 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T1",
        title="T1",
        status="COMPLETED",
        progress_percentage=100,
        milestone_id=m1.id,
    )
    t2 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T2",
        title="T2",
        status="COMPLETED",
        progress_percentage=100,
        milestone_id=m1.id,
    )
    res1 = MilestoneHealthCalculator.evaluate_milestone_health(m1, [t1, t2], now=now)
    assert res1.health == MilestoneHealth.COMPLETED
    assert res1.progress_percentage == 100.0
    assert res1.completed_tasks == 2
    assert res1.blocked_tasks == 0

    # 2. One task blocked -> AT_RISK
    m2 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M2",
        name="Has Blocked",
        target_date=now + timedelta(days=5),
    )
    t3 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T3",
        title="T3",
        status="IN_PROGRESS",
        progress_percentage=50,
        milestone_id=m2.id,
    )
    t4 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T4",
        title="T4",
        status="BLOCKED",
        progress_percentage=20,
        milestone_id=m2.id,
    )
    res2 = MilestoneHealthCalculator.evaluate_milestone_health(m2, [t3, t4], now=now)
    assert res2.health == MilestoneHealth.AT_RISK
    assert res2.blocked_tasks == 1
    assert res2.progress_percentage == 35.0

    # 3. Target date overdue -> AT_RISK
    m3 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M3",
        name="Overdue Milestone",
        target_date=now - timedelta(days=1),
    )
    t5 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T5",
        title="T5",
        status="IN_PROGRESS",
        progress_percentage=80,
        milestone_id=m3.id,
    )
    res3 = MilestoneHealthCalculator.evaluate_milestone_health(m3, [t5], now=now)
    assert res3.health == MilestoneHealth.AT_RISK
    assert res3.is_overdue is True

    # 4. In progress, on schedule -> ON_TRACK
    m4 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M4",
        name="On Schedule",
        target_date=now + timedelta(days=2),
    )
    res4 = MilestoneHealthCalculator.evaluate_milestone_health(m4, [t5], now=now)
    assert res4.health == MilestoneHealth.ON_TRACK
    assert res4.progress_percentage == 80.0

    # 5. No member tasks -> PENDING
    m5 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M5",
        name="Pending Milestone No Tasks",
    )
    res5 = MilestoneHealthCalculator.evaluate_milestone_health(m5, [], now=now)
    assert res5.health == MilestoneHealth.PENDING
    assert res5.total_tasks == 0

    # 6. All member tasks are TODO with 0% progress -> PENDING (not ON_TRACK)
    m6 = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M6",
        name="Pending Milestone Unstarted Tasks",
        target_date=now + timedelta(days=10),
    )
    t_todo1 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T_TODO1",
        title="Todo 1",
        status="TODO",
        progress_percentage=0,
        milestone_id=m6.id,
    )
    t_todo2 = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T_TODO2",
        title="Todo 2",
        status="TODO",
        progress_percentage=0,
        milestone_id=m6.id,
    )
    res6 = MilestoneHealthCalculator.evaluate_milestone_health(m6, [t_todo1, t_todo2], now=now)
    assert res6.health == MilestoneHealth.PENDING
    assert res6.progress_percentage == 0.0
    assert res6.total_tasks == 2
    assert res6.completed_tasks == 0
    assert res6.blocked_tasks == 0

    # 7. Mixed states: TODO + IN_PROGRESS -> ON_TRACK
    t_in_prog = TaskRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="T_INP",
        title="In Progress",
        status="IN_PROGRESS",
        progress_percentage=25,
        milestone_id=m6.id,
    )
    res7 = MilestoneHealthCalculator.evaluate_milestone_health(m6, [t_todo1, t_in_prog], now=now)
    assert res7.health == MilestoneHealth.ON_TRACK
    assert res7.progress_percentage == 12.5

    # 8. Mixed states: TODO + COMPLETED (not all completed) -> ON_TRACK
    res8 = MilestoneHealthCalculator.evaluate_milestone_health(m6, [t_todo1, t1], now=now)
    assert res8.health == MilestoneHealth.ON_TRACK
    assert res8.progress_percentage == 50.0
    assert res8.completed_tasks == 1


def test_milestone_record_model_attributes() -> None:
    """MilestoneRecord maps cleanly to PostgreSQL schema with required constraints."""
    plan_id = uuid4()
    milestone_id = uuid4()
    target = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)

    m_rec = MilestoneRecord(
        id=milestone_id,
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M1_FINAL",
        name="Semester Exam Wrap-up",
        description="Collated records sealed",
        target_date=target,
    )

    assert m_rec.id == milestone_id
    assert m_rec.tenant_id == "tenant-alpha"
    assert m_rec.plan_id == plan_id
    assert m_rec.temporary_id == "M1_FINAL"
    assert m_rec.name == "Semester Exam Wrap-up"
    assert m_rec.target_date == target


@pytest.mark.asyncio
async def test_plan_service_get_plan_milestones_derives_health() -> None:
    """PlanService.get_plan_milestones loads milestones and member tasks, deriving health."""
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow
    mock_uow.__aexit__.return_value = None

    plan_id = uuid4()
    tenant_id = "tenant-alpha"

    # Mock PlanRecord existence
    mock_plan = MagicMock()
    mock_plan.id = plan_id
    mock_plan.tenant_id = tenant_id

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan

    m1 = MilestoneRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        plan_id=plan_id,
        temporary_id="M1",
        name="Milestone 1",
        target_date=datetime(2026, 12, 1, 0, 0, tzinfo=UTC),
    )
    res_milestones = MagicMock()
    res_milestones.scalars.return_value.all.return_value = [m1]

    t1 = TaskRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        plan_id=plan_id,
        temporary_id="T1",
        title="Task 1",
        status="COMPLETED",
        progress_percentage=100,
        milestone_id=m1.id,
    )
    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1]

    mock_session.execute.side_effect = [res_plan, res_milestones, res_tasks]

    service = PlanService(uow_factory=lambda: mock_uow)
    milestones_resp = await service.get_plan_milestones(tenant_id=tenant_id, plan_id=plan_id)
    assert len(milestones_resp) == 1
    item = milestones_resp[0]
    assert item.temporary_id == "M1"
    assert item.name == "Milestone 1"
    assert item.health.value == "COMPLETED"
    assert item.progress_percentage == 100.0


def test_api_get_plan_milestones() -> None:
    """GET /api/v1/plans/{plan_id}/milestones returns 200 with list of milestone health."""
    from typing import Any

    from fastapi import FastAPI, status
    from fastapi.testclient import TestClient

    from app.api.errors import register_api_exception_handlers
    from app.api.v1.planning import create_planning_router
    from app.core.auth.models import (
        AuthenticatedPrincipal,
        AuthenticationMethod,
        PrincipalType,
        TenantContext,
    )
    from app.task_intelligence.milestones import MilestoneHealth, MilestoneHealthResponse

    plan_id = uuid4()
    m_id = uuid4()

    class MockService:
        async def get_plan_milestones(
            self, tenant_id: str, plan_id: UUID
        ) -> list[MilestoneHealthResponse]:
            return [
                MilestoneHealthResponse(
                    id=m_id,
                    tenant_id="tenant-alpha",
                    plan_id=plan_id,
                    temporary_id="M1_PAPER",
                    name="Question Paper Readiness",
                    description="",
                    target_date=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
                    health=MilestoneHealth.ON_TRACK,
                    progress_percentage=75.0,
                    total_tasks=4,
                    completed_tasks=3,
                    blocked_tasks=0,
                    is_overdue=False,
                )
            ]

    app = FastAPI()
    register_api_exception_handlers(app)

    def get_tenant() -> TenantContext:
        return TenantContext(tenant_id="tenant-alpha")

    def get_principal() -> Any:
        return AuthenticatedPrincipal(
            principal_id="user-planner",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant-alpha",
            roles=frozenset({"planner"}),
            permissions=frozenset({"plan:read"}),
        )

    router = create_planning_router(
        tenant_dependency=get_tenant,
        plan_service_dependency=lambda: MockService(),
        principal_dependency=get_principal,
    )
    app.include_router(router)
    client = TestClient(app)

    resp = client.get(f"/plans/{plan_id}/milestones")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["temporary_id"] == "M1_PAPER"
    assert data[0]["health"] == "ON_TRACK"
    assert data[0]["progress_percentage"] == 75.0

