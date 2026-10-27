"""Comprehensive unit tests for Risk & Blocker Detection (FR-TI-07).

Tests:
- Critical-path delay vs float consumption detection
- Declared task blocker detection and dependency chain propagation
- Authoritative resource capacity shortfall evaluation
- Explicit not-evaluable status when authoritative resource data is missing
- Risk recalculation following task progress transitions
"""

from typing import Any
from uuid import uuid4

import pytest

from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.risks import (
    DeterministicRiskDetector,
    RiskCategory,
    RiskSeverity,
    TaskRiskItem,
)


class DummyTask:
    def __init__(
        self,
        temporary_id: str,
        status: str = "TODO",
        blocked_reason: str | None = None,
    ) -> None:
        self.id = uuid4()
        self.temporary_id = temporary_id
        self.status = status
        self.blocked_reason = blocked_reason


class DummyDependency:
    def __init__(self, predecessor_id: Any, successor_id: Any) -> None:
        self.predecessor_id = predecessor_id
        self.successor_id = successor_id


def test_critical_path_vs_slack_delay_detection() -> None:
    """Delay on critical path is CRITICAL; delay on slack task is MEDIUM float consumption."""
    graph = TaskDependencyGraph()
    graph.add_task("T_CRIT", duration_minutes=120)
    graph.add_task("T_FLOAT", duration_minutes=30)
    graph.add_task("T_END", duration_minutes=15)
    graph.add_dependency("T_CRIT", "T_END")
    graph.add_dependency("T_FLOAT", "T_END")

    cpm = CriticalPathCalculator.calculate(graph)

    risks = DeterministicRiskDetector.detect_critical_path_slippage(
        cpm_result=cpm,
        delayed_task_ids={"T_CRIT", "T_FLOAT"},
    )
    assert len(risks) == 2

    crit_risk = next(r for r in risks if r.task_id == "T_CRIT")
    assert crit_risk.category == RiskCategory.CRITICAL_PATH_SLIPPAGE
    assert crit_risk.severity == RiskSeverity.CRITICAL
    assert crit_risk.is_evaluable is True
    assert "critical path (zero slack)" in crit_risk.description

    float_risk = next(r for r in risks if r.task_id == "T_FLOAT")
    assert float_risk.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert float_risk.severity == RiskSeverity.MEDIUM
    assert float_risk.is_evaluable is True
    assert "total slack" in float_risk.description


def test_declared_blocker_and_dependency_propagation() -> None:
    """Declared blockers are detected and propagated down dependency chains."""
    t1 = DummyTask("T1", status="BLOCKED", blocked_reason="Awaiting question paper moderation")
    t2 = DummyTask("T2", status="TODO")
    t3 = DummyTask("T3", status="TODO")

    dep1 = DummyDependency(t1.id, t2.id)
    dep2 = DummyDependency(t2.id, t3.id)

    risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2, t3],
        dependencies=[dep1, dep2],
    )

    # T1 is directly blocked; T2 is waiting on blocked T1
    assert len(risks) == 2

    direct_risk = next(r for r in risks if r.task_id == "T1")
    assert direct_risk.category == RiskCategory.DECLARED_BLOCKER
    assert direct_risk.severity == RiskSeverity.HIGH
    assert "Awaiting question paper moderation" in direct_risk.description

    downstream_risk = next(r for r in risks if r.task_id == "T2")
    assert downstream_risk.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert downstream_risk.severity == RiskSeverity.HIGH
    assert "waiting on predecessor 'T1' which is BLOCKED" in downstream_risk.description


def test_authoritative_resource_capacity_shortfall() -> None:
    """Overloaded resources generate RESOURCE_CAPACITY_SHORTFALL risks."""
    required = {
        "ROOM:EXAM_HALL_A": 1,
        "INVIGILATOR": 10,
        "PROJECTOR": 2,
    }
    available = {
        "ROOM:EXAM_HALL_A": 1,
        "INVIGILATOR": 6,  # Shortfall: 10 required, 6 available
        "PROJECTOR": 1,    # Shortfall: 2 required, 1 available
    }

    risks = DeterministicRiskDetector.evaluate_resource_shortfalls(
        required_capacities=required,
        available_capacities=available,
    )

    assert len(risks) == 2
    invig_risk = next(r for r in risks if r.task_id == "RESOURCE:INVIGILATOR")
    assert invig_risk.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL
    assert invig_risk.severity == RiskSeverity.HIGH
    assert invig_risk.is_evaluable is True
    assert "requires 10 units but only 6 are available" in invig_risk.description

    proj_risk = next(r for r in risks if r.task_id == "RESOURCE:PROJECTOR")
    assert proj_risk.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL
    assert proj_risk.severity == RiskSeverity.HIGH
    assert "requires 2 units but only 1 are available" in proj_risk.description


def test_sufficient_resource_capacity_generates_zero_risks() -> None:
    """When available capacity meets or exceeds demand, zero risks are emitted."""
    required = {"ROOM:101": 1, "PROCTOR": 4}
    available = {"ROOM:101": 2, "PROCTOR": 8}

    risks = DeterministicRiskDetector.evaluate_resource_shortfalls(
        required_capacities=required,
        available_capacities=available,
    )
    assert len(risks) == 0


def test_missing_resource_data_returns_explicit_unevaluable_status() -> None:
    """When authoritative resource data is unavailable, return explicit unevaluable status.

    Never fabricates data.
    """
    risk = DeterministicRiskDetector.evaluate_resource_capacity()
    assert risk.category == RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED
    assert risk.is_evaluable is False
    assert "cannot be evaluated without authoritative" in risk.description


def test_risk_recalculation_after_progress_update() -> None:
    """Risks update dynamically when a task transitions from BLOCKED to COMPLETED."""
    t1 = DummyTask("T1", status="BLOCKED", blocked_reason="Security seal missing")
    t2 = DummyTask("T2", status="TODO")
    dep = DummyDependency(t1.id, t2.id)

    # Initial state: risks present
    initial_risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2],
        dependencies=[dep],
    )
    assert len(initial_risks) == 2

    # Task progress update: unblocked and completed
    t1.status = "COMPLETED"
    t1.blocked_reason = None

    # Recalculated state: zero blocker risks
    updated_risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2],
        dependencies=[dep],
    )
    assert len(updated_risks) == 0


# --- Service and API Integration Tests for Risks (FR-TI-07) ---


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_resource_unsupported() -> None:
    """When tenant lacks resources, evaluate_plan_risks emits unsupported status."""
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    # Mock PlanRepo.get_by_id_and_tenant returning a valid plan
    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    # Mock TaskRecord and zero ResourceRecords
    t1 = MagicMock()
    t1.id = uuid4()
    t1.temporary_id = "T1"
    t1.estimated_duration_minutes = 60
    t1.actual_duration_minutes = None
    t1.status = "TODO"
    t1.blocked_reason = None
    t1.owner_role = "OPERATOR"
    t1.work_category = "PLANNING"

    # Setup execute returns:
    # 1. plan query
    # 2. tasks query
    # 3. dependencies query
    # 4. resources query (empty)
    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan

    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1]

    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = []

    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = []

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks: list[TaskRiskItem] = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    # Must contain RESOURCE_CAPACITY_UNSUPPORTED
    unsupported = next(
        (r for r in risks if r.category == RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED),
        None,
    )
    assert unsupported is not None
    assert unsupported.is_evaluable is False


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_capacity_shortfall() -> None:
    """When concurrent demand exceeds active eligible resource capacity,
    emits RESOURCE_CAPACITY_SHORTFALL.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    # Two concurrent tasks requiring INVIGILATOR (no dependency -> [0, 60))
    t1 = MagicMock(
        id=uuid4(),
        temporary_id="T1",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="INVIGILATOR",
        owner_reference=None,
        work_category="EXAM_EXECUTION",
    )
    t2 = MagicMock(
        id=uuid4(),
        temporary_id="T2",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="INVIGILATOR",
        owner_reference=None,
        work_category="EXAM_EXECUTION",
    )

    # One active resource of canonical type PERSON with skills=['INVIGILATOR'] and capacity=1
    res_record = MagicMock()
    res_record.type = "PERSON"
    res_record.skills = ["INVIGILATOR"]
    res_record.attributes = {"role": "INVIGILATOR"}
    res_record.resource_key = "STAFF_01"
    res_record.capacity = 1
    res_record.active = True

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan

    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1, t2]

    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = []

    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = [res_record]

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks: list[TaskRiskItem] = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    shortfall = next(
        (r for r in risks if r.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL),
        None,
    )
    assert shortfall is not None
    assert shortfall.is_evaluable is True
    assert "peak concurrent demand is 2 units, but eligible capacity is 1" in shortfall.description


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_non_overlapping_no_shortfall() -> None:
    """When two tasks run sequentially (non-overlapping), peak concurrent demand is 1,
    so capacity=1 suffices.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    uid_1 = uuid4()
    uid_2 = uuid4()

    t1 = MagicMock(
        id=uid_1,
        temporary_id="T1",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="INVIGILATOR",
        owner_reference=None,
        work_category="EXAM_EXECUTION",
    )
    t2 = MagicMock(
        id=uid_2,
        temporary_id="T2",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="INVIGILATOR",
        owner_reference=None,
        work_category="EXAM_EXECUTION",
    )

    # T2 depends on T1 -> T1 [0, 60), T2 [60, 120). Non-overlapping!
    dep = MagicMock(predecessor_id=uid_1, successor_id=uid_2, lag_minutes=0)

    res_record = MagicMock()
    res_record.type = "PERSON"
    res_record.skills = ["INVIGILATOR"]
    res_record.attributes = {"role": "INVIGILATOR"}
    res_record.resource_key = "STAFF_01"
    res_record.capacity = 1
    res_record.active = True

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan
    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1, t2]
    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = [dep]
    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = [res_record]

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks: list[TaskRiskItem] = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    shortfalls = [r for r in risks if r.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL]
    assert len(shortfalls) == 0


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_incorrect_skill_matching() -> None:
    """When resources exist but lack the demanded skill, emits shortfall explaining
    missing skill.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    t1 = MagicMock(
        id=uuid4(),
        temporary_id="T1",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="SECURITY_OFFICER",
        owner_reference=None,
        work_category="SECURITY",
    )

    # Active resource exists, but type is PERSON with skills=['INVIGILATOR']
    res_record = MagicMock()
    res_record.type = "PERSON"
    res_record.skills = ["INVIGILATOR"]
    res_record.attributes = {"role": "INVIGILATOR"}
    res_record.resource_key = "STAFF_01"
    res_record.capacity = 5
    res_record.active = True

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan
    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1]
    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = []
    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = [res_record]

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks: list[TaskRiskItem] = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    shortfall = next(
        (r for r in risks if r.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL),
        None,
    )
    assert shortfall is not None
    assert "no active eligible resource with matching skills" in shortfall.description


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_cpm_cycle_degraded_diagnostic() -> None:
    """When a dependency cycle exists, CPM failure is reported explicitly
    rather than silently swallowed.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    uid_1 = uuid4()
    uid_2 = uuid4()

    t1 = MagicMock(
        id=uid_1,
        temporary_id="T1",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role=None,
        owner_reference=None,
        work_category="PLANNING",
    )
    t2 = MagicMock(
        id=uid_2,
        temporary_id="T2",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role=None,
        owner_reference=None,
        work_category="PLANNING",
    )

    # Cycle: T1 -> T2 -> T1
    dep1 = MagicMock(predecessor_id=uid_1, successor_id=uid_2, lag_minutes=0)
    dep2 = MagicMock(predecessor_id=uid_2, successor_id=uid_1, lag_minutes=0)

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan
    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1, t2]
    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = [dep1, dep2]
    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = []

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks: list[TaskRiskItem] = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    cpm_degraded = next(
        (r for r in risks if "CPM schedule calculation degraded" in r.description),
        None,
    )
    assert cpm_degraded is not None
    assert cpm_degraded.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert cpm_degraded.severity == RiskSeverity.CRITICAL
    assert "dependency cycle detected" in cpm_degraded.description


@pytest.mark.asyncio
async def test_plan_service_evaluate_plan_risks_variance_and_blockers() -> None:
    """Detects duration overrun > 50% and declared task blockers with propagation."""
    from unittest.mock import AsyncMock, MagicMock

    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__.return_value = mock_uow

    mock_plan = MagicMock()
    mock_plan.id = uuid4()
    mock_plan.tenant_id = "tenant-alpha"

    uid_1 = uuid4()
    uid_2 = uuid4()

    # T1 is blocked and overran estimate (60m est, 100m actual)
    t1 = MagicMock(
        id=uid_1,
        temporary_id="T1",
        estimated_duration_minutes=60,
        actual_duration_minutes=100,
        status="BLOCKED",
        blocked_reason="Seal inspection failed",
        owner_role="OPERATOR",
        work_category="PLANNING",
    )
    # T2 depends on T1
    t2 = MagicMock(
        id=uid_2,
        temporary_id="T2",
        estimated_duration_minutes=60,
        actual_duration_minutes=None,
        status="TODO",
        blocked_reason=None,
        owner_role="OPERATOR",
        work_category="PLANNING",
    )

    dep = MagicMock(predecessor_id=uid_1, successor_id=uid_2, lag_minutes=0)

    res_plan = MagicMock()
    res_plan.scalar_one_or_none.return_value = mock_plan

    res_tasks = MagicMock()
    res_tasks.scalars.return_value.all.return_value = [t1, t2]

    res_deps = MagicMock()
    res_deps.scalars.return_value.all.return_value = [dep]

    res_resources = MagicMock()
    res_resources.scalars.return_value.all.return_value = []

    mock_session.execute.side_effect = [res_plan, res_tasks, res_deps, res_resources]

    service = PlanService(uow_factory=lambda: mock_uow)
    risks = await service.evaluate_plan_risks("tenant-alpha", mock_plan.id)

    # 1. Declared blocker on T1
    assert any(r.task_id == "T1" and r.category == RiskCategory.DECLARED_BLOCKER for r in risks)
    # 2. Propagated blocker to T2
    assert any(r.task_id == "T2" and r.category == RiskCategory.DEPENDENCY_SLIPPAGE for r in risks)
    # 3. Duration overrun on T1 (>50% overrun: (100-60)/60 = 66.7%)
    variance_risk = next(
        r for r in risks if r.task_id == "T1" and "duration exceeded estimate" in r.description
    )
    assert "66.7%" in variance_risk.description


def test_api_get_plan_risks_contract() -> None:
    from unittest.mock import AsyncMock, MagicMock

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

    plan_id = uuid4()
    sample_risk = TaskRiskItem(
        task_id="T1",
        category=RiskCategory.DECLARED_BLOCKER,
        severity=RiskSeverity.HIGH,
        description="Declared blocker on T1",
        is_evaluable=True,
    )

    mock_service = MagicMock()
    mock_service.evaluate_plan_risks = AsyncMock(return_value=[sample_risk])

    app = FastAPI()
    register_api_exception_handlers(app)

    def get_tenant() -> TenantContext:
        return TenantContext(tenant_id="tenant-alpha")

    def get_principal(role: str = "planner") -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            principal_id="user-1",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant-alpha",
            roles=frozenset({role}),
            permissions=frozenset({"plan:read"}),
        )

    router = create_planning_router(
        tenant_dependency=get_tenant,
        plan_service_dependency=lambda: mock_service,
        principal_dependency=lambda: get_principal("planner"),
    )
    app.include_router(router)
    client = TestClient(app)

    # 1. 200 OK
    resp = client.get(f"/plans/{plan_id}/risks")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "T1"
    assert data[0]["category"] == "DECLARED_BLOCKER"
    assert data[0]["severity"] == "HIGH"
    assert data[0]["is_evaluable"] is True

    # 2. 404 Not Found when plan does not exist
    from app.task_intelligence.service import PlanNotFoundError
    mock_service.evaluate_plan_risks.side_effect = PlanNotFoundError(plan_id)
    resp_404 = client.get(f"/plans/{plan_id}/risks")
    assert resp_404.status_code == status.HTTP_404_NOT_FOUND

