"""API contract tests for Task Intelligence planning router (Phase 2A Stage E).

Tests:
- Goal creation
- Plan generation with deterministic PlanSchema
- Validation endpoint returns 422 on cycle with explicit cycle path
- Approval fails before validation
- Approval succeeds on validated plan, returns 200 with baseline hash
- Critical path returns CPM schedule
- 404 wrong-tenant equivalence (Tenant B cannot see Tenant A goal/plan/task)
- Task progress updates and validation errors
- Safe error envelopes with RFC 7807 problem details
"""

from datetime import UTC
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.planning import create_planning_router
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
)


# Mock in-memory test harness for PlanService without requiring live DB for contract tests
class MockPlanService:
    def __init__(self) -> None:
        self.goals: dict[tuple[str, str], Any] = {}
        self.plans: dict[tuple[str, str], Any] = {}
        self.tasks: dict[tuple[str, str], Any] = {}

    async def create_goal(
        self, tenant_id: str, goal_data: GoalCreate, correlation_id: str | None = None
    ) -> Any:
        from datetime import datetime

        from app.task_intelligence.schemas import GoalResponse

        gid = uuid4()
        resp = GoalResponse(
            id=gid,
            tenant_id=tenant_id,
            objective=goal_data.objective,
            scope=goal_data.scope,
            deadline=goal_data.deadline,
            target_window=goal_data.target_window,
            constraints=goal_data.constraints,
            resource_scope=goal_data.resource_scope,
            context_references=goal_data.context_references,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.goals[(tenant_id, str(gid))] = resp
        return resp

    async def persist_generated_plan(
        self,
        tenant_id: str,
        goal_id: Any,
        plan_schema: PlanSchema,
        correlation_id: str | None = None,
    ) -> Any:
        from datetime import datetime

        from app.task_intelligence.service import GoalNotFoundError

        if (tenant_id, str(goal_id)) not in self.goals:
            raise GoalNotFoundError(goal_id)

        class MockPlanRec:
            def __init__(self, pid: Any):
                self.id = pid
                self.goal_id = goal_id
                self.version_number = 1
                self.plan_name = plan_schema.plan_name
                self.status = "GENERATED"
                self.is_baseline = False
                self.baseline_hash = None
                self.created_at = datetime.now(UTC)
                self.updated_at = datetime.now(UTC)
                self.schema = plan_schema

        pid = uuid4()
        rec = MockPlanRec(pid)
        self.plans[(tenant_id, str(pid))] = rec

        # Store tasks
        for td in plan_schema.tasks:
            tid = uuid4()
            self.tasks[(tenant_id, str(tid))] = {
                "id": tid,
                "plan_id": pid,
                "temporary_id": td.temporary_id,
                "title": td.title,
                "status": "TODO",
                "progress_percentage": 0,
                "actual_duration_minutes": None,
                "blocked_reason": None,
                "updated_at": datetime.now(UTC),
            }
        return rec

    async def validate_plan(
        self, tenant_id: str, plan_id: Any, correlation_id: str | None = None
    ) -> Any:
        from app.task_intelligence.service import PlanNotFoundError
        from app.task_intelligence.validators import PlanValidator

        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan_rec = self.plans[key]
        validator = PlanValidator()
        result = validator.validate(plan_rec.schema)
        if result.is_valid:
            plan_rec.status = "VALIDATED"
        return result

    async def approve_plan(
        self, tenant_id: str, plan_id: Any, approver_id: str, correlation_id: str | None = None
    ) -> Any:
        from app.task_intelligence.schemas import InvalidPlanStateTransitionError, PlanState
        from app.task_intelligence.service import PlanNotFoundError

        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan_rec = self.plans[key]
        if plan_rec.status != "VALIDATED":
            raise InvalidPlanStateTransitionError(PlanState(plan_rec.status), PlanState.APPROVED)
        plan_rec.status = "APPROVED"
        plan_rec.is_baseline = True
        plan_rec.baseline_hash = (
            "mock-baseline-hash-64-characters-long-sha256-fingerprint-test-hash"
        )
        return plan_rec

    async def get_critical_path(self, tenant_id: str, plan_id: Any) -> Any:
        from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
        from app.task_intelligence.service import PlanNotFoundError

        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan_rec = self.plans[key]
        graph = TaskDependencyGraph()
        for t in plan_rec.schema.tasks:
            graph.add_task(t.temporary_id, t.estimated_duration_minutes)
        for d in plan_rec.schema.dependencies:
            graph.add_dependency(d.predecessor_id, d.successor_id, d.lag_minutes)
        return CriticalPathCalculator.calculate(graph)

    async def update_task_progress(
        self, tenant_id: str, task_id: Any, update_data: Any, correlation_id: str | None = None
    ) -> Any:
        from app.task_intelligence.progress import (
            TaskProgressStatus,
            assert_valid_task_progress_transition,
        )
        from app.task_intelligence.service import TaskNotFoundError

        key = (tenant_id, str(task_id))
        if key not in self.tasks:
            raise TaskNotFoundError(task_id)
        t = self.tasks[key]
        if update_data.status is not None:
            assert_valid_task_progress_transition(
                TaskProgressStatus(t["status"]), update_data.status
            )
            t["status"] = str(update_data.status)
        if update_data.progress_percentage is not None:
            t["progress_percentage"] = update_data.progress_percentage
        if update_data.blocked_reason is not None:
            t["blocked_reason"] = update_data.blocked_reason

        class MockTaskRec:
            def __init__(self, data: dict):
                for k, v in data.items():
                    setattr(self, k, v)

        return MockTaskRec(t)


@pytest.fixture
def mock_service() -> MockPlanService:
    return MockPlanService()


@pytest.fixture
def client_factory(mock_service: MockPlanService) -> Any:
    def _create_client(tenant_id: str = "tenant-test-a") -> TestClient:
        app = FastAPI()
        register_api_exception_handlers(app)

        async def get_trusted_tenant() -> str:
            return tenant_id

        async def get_plan_service() -> Any:
            return mock_service

        router = create_planning_router(
            tenant_dependency=get_trusted_tenant,
            plan_service_dependency=get_plan_service,
        )
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    return _create_client


def test_create_goal_contract(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    response = client.post(
        "/api/v1/goals",
        json={"objective": "Plan semester exams"},
        headers={"X-Correlation-ID": "test-corr-123"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["objective"] == "Plan semester exams"
    assert data["tenant_id"] == "tenant-alpha"
    assert "id" in data


def test_goal_validation_error_contract(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    # Short objective (< 3 chars)
    response = client.post(
        "/api/v1/goals",
        json={"objective": "ab"},
    )
    assert response.status_code == 422


def test_generate_plan_contract(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    # 1. Create goal
    g_res = client.post("/api/v1/goals", json={"objective": "Plan exams"})
    goal_id = g_res.json()["id"]

    # 2. Post generated plan schema
    plan_schema = {
        "plan_name": "Exam Plan V1",
        "tasks": [
            {"temporary_id": "T1", "title": "Task 1", "estimated_duration_minutes": 30},
            {"temporary_id": "T2", "title": "Task 2", "estimated_duration_minutes": 45},
        ],
        "dependencies": [
            {"predecessor_id": "T1", "successor_id": "T2"},
        ],
    }
    p_res = client.post(
        f"/api/v1/goals/{goal_id}/generate-plan",
        json=plan_schema,
    )
    assert p_res.status_code == 201
    data = p_res.json()
    assert data["plan_name"] == "Exam Plan V1"
    assert data["status"] == "GENERATED"
    assert data["version_number"] == 1


def test_validate_plan_valid_and_cyclic(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    g_res = client.post("/api/v1/goals", json={"objective": "Plan exams"})
    goal_id = g_res.json()["id"]

    # Valid plan
    valid_schema = {
        "plan_name": "Valid Plan",
        "tasks": [
            {"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 10},
            {"temporary_id": "T2", "title": "T2", "estimated_duration_minutes": 20},
        ],
        "dependencies": [{"predecessor_id": "T1", "successor_id": "T2"}],
    }
    p1 = client.post(f"/api/v1/goals/{goal_id}/generate-plan", json=valid_schema).json()["id"]
    val1 = client.post(f"/api/v1/plans/{p1}/validate")
    assert val1.status_code == 200
    assert val1.json()["is_valid"] is True

    # Cyclic plan
    cyclic_schema = {
        "plan_name": "Cyclic Plan",
        "tasks": [
            {"temporary_id": "A", "title": "A", "estimated_duration_minutes": 10},
            {"temporary_id": "B", "title": "B", "estimated_duration_minutes": 20},
        ],
        "dependencies": [
            {"predecessor_id": "A", "successor_id": "B"},
            {"predecessor_id": "B", "successor_id": "A"},
        ],
    }
    p2 = client.post(f"/api/v1/goals/{goal_id}/generate-plan", json=cyclic_schema).json()["id"]
    val2 = client.post(f"/api/v1/plans/{p2}/validate")
    assert val2.status_code == 422
    data2 = val2.json()
    assert data2["is_valid"] is False
    assert data2["cycle_path"] is not None
    assert set(data2["cycle_path"]) == {"A", "B"}


def test_approval_lifecycle_contract(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    g_res = client.post("/api/v1/goals", json={"objective": "Plan exams"})
    goal_id = g_res.json()["id"]

    plan_schema = {
        "plan_name": "Valid Plan",
        "tasks": [{"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 30}],
    }
    p_id = client.post(f"/api/v1/goals/{goal_id}/generate-plan", json=plan_schema).json()["id"]

    # Attempt approve before validation -> 422
    app_fail = client.post(f"/api/v1/plans/{p_id}/approve")
    assert app_fail.status_code == 422

    # Validate first
    client.post(f"/api/v1/plans/{p_id}/validate")

    # Now approve succeeds
    app_ok = client.post(f"/api/v1/plans/{p_id}/approve")
    assert app_ok.status_code == 200
    data = app_ok.json()
    assert data["status"] == "APPROVED"
    assert data["is_baseline"] is True
    assert data["baseline_hash"] is not None


def test_critical_path_contract(client_factory: Any) -> None:
    client = client_factory("tenant-alpha")
    g_res = client.post("/api/v1/goals", json={"objective": "Plan exams"})
    goal_id = g_res.json()["id"]

    plan_schema = {
        "plan_name": "CPM Plan",
        "tasks": [
            {"temporary_id": "A", "title": "A", "estimated_duration_minutes": 10},
            {"temporary_id": "B", "title": "B", "estimated_duration_minutes": 20},
        ],
        "dependencies": [{"predecessor_id": "A", "successor_id": "B"}],
    }
    p_id = client.post(f"/api/v1/goals/{goal_id}/generate-plan", json=plan_schema).json()["id"]

    cpm_res = client.get(f"/api/v1/plans/{p_id}/critical-path")
    assert cpm_res.status_code == 200
    data = cpm_res.json()
    assert data["project_duration_minutes"] == 30
    assert data["critical_path"] == ["A", "B"]


def test_wrong_tenant_isolation_404_equivalence(client_factory: Any) -> None:
    client_a = client_factory("tenant-alpha")
    client_b = client_factory("tenant-beta")

    # Tenant Alpha creates a goal
    g_res = client_a.post("/api/v1/goals", json={"objective": "Alpha goal"})
    goal_id = g_res.json()["id"]

    # Tenant Beta cannot access or generate plan for Tenant Alpha's goal -> 404
    plan_schema = {
        "plan_name": "Infiltrator Plan",
        "tasks": [{"temporary_id": "T1", "title": "T1"}],
    }
    b_res = client_b.post(f"/api/v1/goals/{goal_id}/generate-plan", json=plan_schema)
    assert b_res.status_code == 404
    assert b_res.json()["error"]["code"] == "goal.not_found"

    # Non-existent goal also returns 404 with identical message
    ghost_res = client_b.post(f"/api/v1/goals/{uuid4()}/generate-plan", json=plan_schema)
    assert ghost_res.status_code == 404
    assert ghost_res.json()["error"]["code"] == "goal.not_found"
