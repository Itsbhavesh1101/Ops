"""Comprehensive unit tests for Next-Best-Task Recommendation Engine (FR-TI-09).

Tests:
- Deterministic candidate ranking and scoring
- Task readiness filtering (initial tasks, completed predecessors)
- Blocked task and dependency handling
- Critical-path slack elevation
- Recommendation quality indicators and explanations
- Advisory non-authoritative contract invariants
- API endpoint authentication, authorization, and cross-tenant isolation
"""

from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.errors import register_api_exception_handlers
from app.api.v1.planning import create_planning_router
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
)
from app.task_intelligence.graph.models import CPMScheduleNode, CPMScheduleResult
from app.task_intelligence.recommender import (
    InvalidTaskIdentifierError,
    PlanRecommendationsResponse,
    RecommendationAction,
    RecommendationPolicyConfig,
    RecommendationQuality,
    TaskRecommendation,
    TaskRecommender,
)
from app.task_intelligence.service import PlanNotFoundError


class DummyTask:
    def __init__(
        self,
        temporary_id: str,
        title: str,
        status: str = "TODO",
        priority: str = "MEDIUM",
        progress_percentage: int = 0,
        blocked_reason: str | None = None,
        uid: UUID | None = None,
    ) -> None:
        self.id = uid or uuid4()
        self.temporary_id = temporary_id
        self.title = title
        self.status = status
        self.priority = priority
        self.progress_percentage = progress_percentage
        self.blocked_reason = blocked_reason
        self.estimated_duration_minutes = 60


class DummyDependency:
    def __init__(self, predecessor_id: Any, successor_id: Any) -> None:
        self.id = uuid4()
        self.predecessor_id = predecessor_id
        self.successor_id = successor_id
        self.dependency_type = "FINISH_TO_START"
        self.lag_minutes = 0


def test_initial_tasks_are_ready_and_ranked() -> None:
    """Tasks with zero incoming dependencies are immediately ready to start."""
    plan_id = uuid4()
    t1 = DummyTask("T1", "Initial Setup", priority="HIGH")
    t2 = DummyTask("T2", "Initial Documentation", priority="LOW")

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1, t2],
        dependencies=[],
    )

    assert res.total_tasks == 2
    assert res.ready_tasks_count == 2
    assert res.blocked_tasks_count == 0
    assert len(res.recommendations) == 2

    # Higher priority task ranks first
    assert res.recommendations[0].temporary_id == "T1"
    assert res.recommendations[0].rank == 1
    assert res.recommendations[0].action == RecommendationAction.START_TASK
    assert "Initial task with zero incoming dependencies" in res.recommendations[0].explanation


def test_predecessor_completion_unlocks_successor() -> None:
    """Successor task is not ready until its predecessor is completed."""
    plan_id = uuid4()
    t1 = DummyTask("T1", "Draft Syllabus", status="IN_PROGRESS", priority="HIGH")
    t2 = DummyTask("T2", "Print Papers", status="TODO", priority="CRITICAL")
    dep = DummyDependency(t1.id, t2.id)

    # When T1 is IN_PROGRESS, T2 is not ready (waiting on T1)
    res1 = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1, t2],
        dependencies=[dep],
        include_blocked=False,
    )
    assert res1.ready_tasks_count == 1
    assert len(res1.recommendations) == 1
    assert res1.recommendations[0].temporary_id == "T1"
    assert res1.recommendations[0].action == RecommendationAction.CONTINUE_EXECUTION

    # Now mark T1 COMPLETED: T2 becomes ready
    t1.status = "COMPLETED"
    t1.progress_percentage = 100

    res2 = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1, t2],
        dependencies=[dep],
    )
    assert res2.ready_tasks_count == 1
    assert res2.completed_tasks_count == 1
    assert len(res2.recommendations) == 1
    assert res2.recommendations[0].temporary_id == "T2"
    assert res2.recommendations[0].action == RecommendationAction.START_TASK
    assert "All 1 predecessor(s) completed (T1)" in res2.recommendations[0].explanation


def test_blocked_task_and_downstream_mitigation() -> None:
    """Directly blocked tasks and downstream dependencies are identified with reasons."""
    plan_id = uuid4()
    t1 = DummyTask("T1", "Obtain Sign-off", status="BLOCKED", blocked_reason="Approver on leave")
    t2 = DummyTask("T2", "Publish Roster", status="TODO")
    dep = DummyDependency(t1.id, t2.id)

    # With include_blocked=True, blocked tasks are surfaced with RESOLVE_BLOCKER
    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1, t2],
        dependencies=[dep],
        include_blocked=True,
    )
    assert res.blocked_tasks_count == 2
    assert res.ready_tasks_count == 0

    t1_rec = next(r for r in res.recommendations if r.temporary_id == "T1")
    assert t1_rec.action == RecommendationAction.RESOLVE_BLOCKER
    assert "Approver on leave" in t1_rec.blocked_reasons[0]

    t2_rec = next(r for r in res.recommendations if r.temporary_id == "T2")
    assert t2_rec.action == RecommendationAction.RESOLVE_BLOCKER
    assert any("Waiting on blocked predecessor" in r for r in t2_rec.blocked_reasons)


def test_critical_path_slack_elevation() -> None:
    """Tasks on the critical path (zero slack) receive elevated priority scores."""
    plan_id = uuid4()
    t_crit = DummyTask("T_CRIT", "Critical Exam Prep", priority="HIGH")
    t_slack = DummyTask("T_SLACK", "Elective Activity Prep", priority="HIGH")

    cpm = CPMScheduleResult(
        critical_path=["T_CRIT"],
        project_duration_minutes=120,
        schedule={
            "T_CRIT": CPMScheduleNode(
                task_id="T_CRIT",
                duration_minutes=120,
                early_start=0,
                early_finish=120,
                late_start=0,
                late_finish=120,
                total_slack=0,
                free_slack=0,
                is_critical=True,
            ),
            "T_SLACK": CPMScheduleNode(
                task_id="T_SLACK",
                duration_minutes=30,
                early_start=0,
                early_finish=30,
                late_start=90,
                late_finish=120,
                total_slack=90,
                free_slack=90,
                is_critical=False,
            ),
        },
    )

    policy = RecommendationPolicyConfig(
        critical_path_weight=0.40,
        priority_weight=0.30,
        progress_momentum_weight=0.15,
        readiness_weight=0.15,
    )

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t_crit, t_slack],
        dependencies=[],
        cpm_result=cpm,
        policy=policy,
    )

    assert len(res.recommendations) == 2
    top = res.recommendations[0]
    assert top.temporary_id == "T_CRIT"
    assert top.is_critical_path is True
    assert top.total_slack_minutes == 0
    assert top.action == RecommendationAction.EXPEDITE_CRITICAL
    assert top.score > res.recommendations[1].score
    assert top.quality == RecommendationQuality.HIGH
    assert res.policy_applied is True

    # Without policy, structural fallback still prioritizes critical-path task
    res_no_policy = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t_crit, t_slack],
        dependencies=[],
        cpm_result=cpm,
        policy=None,
    )
    assert res_no_policy.policy_applied is False
    assert res_no_policy.recommendations[0].temporary_id == "T_CRIT"
    assert res_no_policy.recommendations[0].score == 0.0


def test_deterministic_repeatability() -> None:
    """Equivalent inputs always produce byte-for-byte identical output and ordering."""
    plan_id = uuid4()
    tasks = [
        DummyTask("T_B", "Task B", priority="MEDIUM"),
        DummyTask("T_A", "Task A", priority="MEDIUM"),
        DummyTask("T_C", "Task C", priority="HIGH"),
    ]

    res1 = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=tasks,
        dependencies=[],
    )
    res2 = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=tasks,
        dependencies=[],
    )

    tids1 = [r.temporary_id for r in res1.recommendations]
    tids2 = [r.temporary_id for r in res2.recommendations]
    assert tids1 == tids2
    assert [r.score for r in res1.recommendations] == [r.score for r in res2.recommendations]
    assert res1.is_authoritative is False


def test_empty_and_all_completed_plans() -> None:
    """Empty plans and plans with all tasks completed return valid empty recommendations."""
    plan_id = uuid4()

    # Empty plan
    res_empty = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[],
        dependencies=[],
    )
    assert res_empty.total_tasks == 0
    assert len(res_empty.recommendations) == 0

    # All completed
    t1 = DummyTask("T1", "Done 1", status="COMPLETED", progress_percentage=100)
    t2 = DummyTask("T2", "Done 2", status="COMPLETED", progress_percentage=100)
    res_done = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1, t2],
        dependencies=[],
    )
    assert res_done.total_tasks == 2
    assert res_done.completed_tasks_count == 2
    assert res_done.ready_tasks_count == 0
    assert len(res_done.recommendations) == 0


# --- HTTP API & Security / Isolation Tests ---


class MockPlanService:
    def __init__(self) -> None:
        self.plan_id = uuid4()
        self.tenant_id = "tenant-alpha"

    async def get_plan_recommendations(
        self,
        tenant_id: str,
        plan_id: UUID,
        limit: int = 10,
        include_blocked: bool = False,
        policy: RecommendationPolicyConfig | None = None,
    ) -> PlanRecommendationsResponse:
        if tenant_id != self.tenant_id or plan_id != self.plan_id:
            raise PlanNotFoundError(plan_id)

        task_id = uuid4()
        return PlanRecommendationsResponse(
            plan_id=plan_id,
            tenant_id=tenant_id,
            policy=policy,
            policy_applied=policy is not None,
            total_tasks=1,
            ready_tasks_count=1,
            blocked_tasks_count=0,
            completed_tasks_count=0,
            recommendations=[
                TaskRecommendation(
                    task_id=task_id,
                    temporary_id="T1",
                    title="Setup Exam Hall",
                    rank=1,
                    score=0.85,
                    action=RecommendationAction.START_TASK,
                    priority="HIGH",
                    status="TODO",
                    is_critical_path=True,
                    total_slack_minutes=0,
                    explanation="Ready to start: all prerequisites complete.",
                    quality=RecommendationQuality.HIGH,
                    quality_basis="deterministic_cpm_and_dependency_evaluation",
                    blocked_reasons=[],
                )
            ],
        )


def build_test_client(
    service: MockPlanService,
    principal: Any = None,
    tenant_id: str = "tenant-alpha",
) -> TestClient:
    app = FastAPI()
    register_api_exception_handlers(app)

    def get_tenant() -> TenantContext:
        return TenantContext(tenant_id=tenant_id)

    def get_principal() -> Any:
        return principal or AuthenticatedPrincipal(
            principal_id="user-planner",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=tenant_id,
            roles=frozenset({"planner"}),
            permissions=frozenset({"plan:read"}),
        )

    def get_service() -> MockPlanService:
        return service

    router = create_planning_router(
        tenant_dependency=get_tenant,
        plan_service_dependency=get_service,
        principal_dependency=get_principal,
    )
    app.include_router(router)
    return TestClient(app)


def test_api_recommendations_success() -> None:
    """GET /plans/{plan_id}/recommendations returns 200 with ranked recommendations."""
    service = MockPlanService()
    client = build_test_client(service)

    resp = client.get(f"/plans/{service.plan_id}/recommendations")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["plan_id"] == str(service.plan_id)
    assert data["tenant_id"] == service.tenant_id
    assert data["is_authoritative"] is False
    assert len(data["recommendations"]) == 1
    assert data["recommendations"][0]["temporary_id"] == "T1"
    assert data["recommendations"][0]["action"] == "START_TASK"


def test_api_recommendations_cross_tenant_isolation() -> None:
    """Accessing plan from a different tenant returns 404 Not Found."""
    service = MockPlanService()
    # Client is in tenant-beta, plan is in tenant-alpha
    client = build_test_client(service, tenant_id="tenant-beta")

    resp = client.get(f"/plans/{service.plan_id}/recommendations")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["error"]["code"] == "plan.not_found"


def test_api_recommendations_unknown_plan() -> None:
    """Accessing non-existent plan returns 404 Not Found."""
    service = MockPlanService()
    client = build_test_client(service)

    unknown_id = uuid4()
    resp = client.get(f"/plans/{unknown_id}/recommendations")
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_api_recommendations_auditor_authorized() -> None:
    """Auditors can read recommendations without creating/mutating plans."""
    service = MockPlanService()
    auditor = AuthenticatedPrincipal(
        principal_id="user-auditor",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"auditor"}),
        permissions=frozenset(),
    )
    client = build_test_client(service, principal=auditor)

    resp = client.get(f"/plans/{service.plan_id}/recommendations")
    assert resp.status_code == status.HTTP_200_OK


def test_api_recommendations_unauthorized_role() -> None:
    """Principals without reading permissions or required roles are rejected with 403."""
    service = MockPlanService()
    unauthorized = AuthenticatedPrincipal(
        principal_id="user-guest",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"guest"}),
        permissions=frozenset(),
    )
    client = build_test_client(service, principal=unauthorized)

    resp = client.get(f"/plans/{service.plan_id}/recommendations")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["error"]["code"] == "plan.unauthorized"


def test_recommendation_policy_validation() -> None:
    """Policy weights must be finite, non-negative, and sum exactly to 1.0."""
    # Valid custom policy
    custom_policy = RecommendationPolicyConfig(
        critical_path_weight=0.50,
        priority_weight=0.30,
        progress_momentum_weight=0.10,
        readiness_weight=0.10,
    )
    assert custom_policy.critical_path_weight == 0.50

    # Negative weight rejected
    import pytest
    with pytest.raises(ValidationError):
        RecommendationPolicyConfig(
            critical_path_weight=-0.1,
            priority_weight=0.5,
            progress_momentum_weight=0.3,
            readiness_weight=0.3,
        )

    # Sum != 1.0 rejected
    with pytest.raises(ValidationError):
        RecommendationPolicyConfig(
            critical_path_weight=0.2,
            priority_weight=0.2,
            progress_momentum_weight=0.2,
            readiness_weight=0.2,
        )


def test_custom_policy_changes_recommendation_scoring() -> None:
    """Applying custom policy shifts weights deterministically."""
    plan_id = uuid4()
    t_crit = DummyTask("T_CRIT", "Critical Task", priority="LOW")
    t_prio = DummyTask("T_PRIO", "High Priority Task", priority="CRITICAL")

    cpm = CPMScheduleResult(
        critical_path=["T_CRIT"],
        project_duration_minutes=60,
        schedule={
            "T_CRIT": CPMScheduleNode(
                task_id="T_CRIT",
                duration_minutes=60,
                early_start=0,
                early_finish=60,
                late_start=0,
                late_finish=60,
                total_slack=0,
                free_slack=0,
                is_critical=True,
            ),
            "T_PRIO": CPMScheduleNode(
                task_id="T_PRIO",
                duration_minutes=30,
                early_start=0,
                early_finish=30,
                late_start=30,
                late_finish=60,
                total_slack=30,
                free_slack=30,
                is_critical=False,
            ),
        },
    )

    # 1. Under Critical-Path Heavy policy (critical_path=0.70, priority=0.10)
    crit_policy = RecommendationPolicyConfig(
        critical_path_weight=0.70,
        priority_weight=0.10,
        progress_momentum_weight=0.10,
        readiness_weight=0.10,
    )
    res_crit = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t_crit, t_prio],
        dependencies=[],
        cpm_result=cpm,
        policy=crit_policy,
    )
    assert res_crit.recommendations[0].temporary_id == "T_CRIT"

    # 2. Under Priority Heavy policy (critical_path=0.10, priority=0.70)
    prio_policy = RecommendationPolicyConfig(
        critical_path_weight=0.10,
        priority_weight=0.70,
        progress_momentum_weight=0.10,
        readiness_weight=0.10,
    )
    res_prio = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t_crit, t_prio],
        dependencies=[],
        cpm_result=cpm,
        policy=prio_policy,
    )
    assert res_prio.recommendations[0].temporary_id == "T_PRIO"


def test_invalid_task_identifier_raises_typed_error() -> None:
    """Missing or unparseable UUID identifiers fail with InvalidTaskIdentifierError."""
    import pytest
    plan_id = uuid4()

    # Missing ID
    bad_task_none = DummyTask("T_BAD", "Bad Task")
    bad_task_none.id = None  # type: ignore[assignment]

    with pytest.raises(InvalidTaskIdentifierError, match="has no persisted UUID identifier"):
        TaskRecommender.recommend(
            plan_id=plan_id,
            tenant_id="tenant-alpha",
            tasks=[bad_task_none],
            dependencies=[],
        )

    # Unparseable string ID
    bad_task_str = DummyTask("T_BAD2", "Bad Task 2")
    bad_task_str.id = "not-a-valid-uuid"  # type: ignore[assignment]

    with pytest.raises(InvalidTaskIdentifierError, match="has invalid UUID identifier"):
        TaskRecommender.recommend(
            plan_id=plan_id,
            tenant_id="tenant-alpha",
            tasks=[bad_task_str],
            dependencies=[],
        )


def test_cpm_diagnostic_surfaced_as_degraded_quality() -> None:
    """CPM failure/cycle diagnostics surface as DEGRADED quality without silent suppression."""
    plan_id = uuid4()
    t1 = DummyTask("T1", "Initial Task")

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1],
        dependencies=[],
        cpm_result=None,
        cpm_diagnostic="Dependency cycle detected in graph: ['T1', 'T2', 'T1']",
    )
    assert res.cpm_diagnostic == "Dependency cycle detected in graph: ['T1', 'T2', 'T1']"
    assert len(res.recommendations) == 1
    rec = res.recommendations[0]
    assert rec.quality == RecommendationQuality.DEGRADED
    assert "Dependency cycle detected in graph" in rec.quality_basis


def test_critical_path_low_priority_suggests_reprioritization() -> None:
    """Critical-path tasks marked LOW priority produce REPRIORITIZE action."""
    plan_id = uuid4()
    t_crit = DummyTask("T_CRIT", "Critical Low Priority Task", priority="LOW")

    cpm = CPMScheduleResult(
        critical_path=["T_CRIT"],
        project_duration_minutes=60,
        schedule={
            "T_CRIT": CPMScheduleNode(
                task_id="T_CRIT",
                duration_minutes=60,
                early_start=0,
                early_finish=60,
                late_start=0,
                late_finish=60,
                total_slack=0,
                free_slack=0,
                is_critical=True,
            )
        },
    )

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t_crit],
        dependencies=[],
        cpm_result=cpm,
    )
    rec = res.recommendations[0]
    assert rec.action == RecommendationAction.REPRIORITIZE
    assert "reprioritization recommended" in rec.explanation


def test_recommendation_includes_milestone_reference() -> None:
    """Tasks associated with a milestone have that milestone cited in their explanation."""
    plan_id = uuid4()
    t1 = DummyTask("T1", "Task with milestone")
    t1.milestone_reference = "M1_READINESS"

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1],
        dependencies=[],
    )
    rec = res.recommendations[0]
    assert "Associated with milestone 'M1_READINESS'" in rec.explanation


def test_recommendation_includes_milestone_proximity() -> None:
    """When milestones have target dates, proximity is incorporated into explanations."""
    from datetime import UTC, datetime, timedelta

    from app.db.models.planning import MilestoneRecord

    plan_id = uuid4()
    t1 = DummyTask("T1", "Exam Prep Task")
    t1.milestone_reference = "M1_FINAL"

    now = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    target = now + timedelta(days=5)

    milestone = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M1_FINAL",
        name="Final Preparation",
        target_date=target,
    )

    res = TaskRecommender.recommend(
        plan_id=plan_id,
        tenant_id="tenant-alpha",
        tasks=[t1],
        dependencies=[],
        milestones=[milestone],
    )
    rec = res.recommendations[0]
    assert "Associated with milestone 'Final Preparation'" in rec.explanation
    assert "target in" in rec.explanation


def test_api_recommendations_with_custom_policy_query_params() -> None:
    """GET /plans/{plan_id}/recommendations accepts valid 4-factor policy weights."""
    service = MockPlanService()
    client = build_test_client(service)

    resp = client.get(
        f"/plans/{service.plan_id}/recommendations",
        params={
            "critical_path_weight": 0.40,
            "priority_weight": 0.30,
            "progress_momentum_weight": 0.15,
            "readiness_weight": 0.15,
        },
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["policy_applied"] is True
    assert data["policy"]["critical_path_weight"] == 0.40


def test_api_recommendations_partial_policy_params_rejected() -> None:
    """Partial policy weights are rejected with 422 Unprocessable Entity."""
    service = MockPlanService()
    client = build_test_client(service)

    # Only 2 of 4 weights supplied
    resp = client.get(
        f"/plans/{service.plan_id}/recommendations",
        params={
            "critical_path_weight": 0.50,
            "priority_weight": 0.50,
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY



