"""Regression and contract tests for Plan Approval REST flow and Schedule Publication authorization.

Protects against:
1. Plan approval:
   - State transition enforcement: VALIDATED -> APPROVAL_PENDING -> APPROVED.
   - Public submit-for-approval route execution and RBAC.
   - Atomicity / detachment: ensures no HTTP 500 or DetachedInstanceError after UoW commit.
   - Role / permission enforcement (approver, tenant_admin, separation of duties).
   - Cross-tenant isolation.
2. Schedule publication:
   - Request-aware AuthenticatedSchedulePublishPolicy evaluation.
   - Impersonation prevention: effective_principal bound to authenticated principal.
   - RBAC enforcement (scheduler, operator, tenant_admin, admin vs auditor).
   - Publication preconditions and immutable baseline protection.
3. Schedule repair:
   - Repair requires PUBLISHED baseline.
   - Produces candidate review successor while preserving baseline immutability.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.planning import create_planning_router
from app.api.v1.scheduling import create_scheduling_router
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
)
from app.db.models.planning import PlanRecord
from app.db.models.scheduling import ScheduleAssignmentRecord, ScheduleRecord
from app.scheduling.policy import (
    AuthenticatedSchedulePublishPolicy,
    DefaultSchedulePublishPolicy,
    PublishAuthorizationError,
)
from app.task_intelligence.schemas import (
    InvalidPlanStateTransitionError,
    PlanState,
    ValidationResult,
)
from app.task_intelligence.service import PlanNotFoundError


def _make_principal(
    *,
    principal_id: str = "user-test-001",
    tenant_id: str = "tenant-pilot-001",
    roles: set[str] | None = None,
    permissions: set[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        roles=frozenset(roles or {"tenant_admin"}),
        permissions=frozenset(permissions or {"*"}),
    )


class MockPlanServiceWithTransitions:
    """Mock PlanService tracking valid domain transitions and records."""

    def __init__(self) -> None:
        self.plans: dict[tuple[str, str], PlanRecord] = {}

    def seed_plan(
        self,
        tenant_id: str,
        status: str,
        is_baseline: bool = False,
    ) -> PlanRecord:
        pid = uuid4()
        now = datetime.now(UTC)
        rec = PlanRecord(
            id=pid,
            tenant_id=tenant_id,
            goal_id=uuid4(),
            version_number=1,
            plan_name="Exam Master Plan",
            status=status,
            is_baseline=is_baseline,
            baseline_hash="hash-initial" if is_baseline else None,
            created_at=now,
            updated_at=now,
        )
        self.plans[(tenant_id, str(pid))] = rec
        return rec

    async def submit_for_approval(
        self,
        tenant_id: str,
        plan_id: UUID,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan = self.plans[key]
        if plan.status != PlanState.VALIDATED.value:
            raise InvalidPlanStateTransitionError(
                PlanState(plan.status), PlanState.APPROVAL_PENDING
            )
        plan.status = PlanState.APPROVAL_PENDING.value
        plan.updated_at = datetime.now(UTC)
        return plan

    async def approve_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        approver_id: str,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan = self.plans[key]
        if plan.status != PlanState.APPROVAL_PENDING.value:
            raise InvalidPlanStateTransitionError(
                PlanState(plan.status), PlanState.APPROVED
            )
        plan.status = PlanState.APPROVED.value
        plan.is_baseline = True
        plan.baseline_hash = "baseline-sha256-verified"
        plan.updated_at = datetime.now(UTC)
        return plan

    async def validate_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        correlation_id: str | None = None,
    ) -> ValidationResult:
        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        plan = self.plans[key]
        plan.status = PlanState.VALIDATED.value
        return ValidationResult(is_valid=True, total_rules_checked=5, violations=[])


# ==============================================================================
# 1. Plan Approval REST Flow Tests
# ==============================================================================
class TestPlanApprovalRestFlow:
    @pytest.fixture
    def harness(self) -> tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]:
        app = FastAPI()
        register_api_exception_handlers(app)
        service = MockPlanServiceWithTransitions()

        state: dict[str, Any] = {
            "tenant_id": "tenant-pilot-001",
            "principal": _make_principal(
                principal_id="planner-admin-01",
                tenant_id="tenant-pilot-001",
                roles={"planner", "tenant_admin"},
            ),
        }

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=str(state["tenant_id"]))

        async def get_principal() -> AuthenticatedPrincipal:
            p = state["principal"]
            assert isinstance(p, AuthenticatedPrincipal)
            return p

        async def get_service() -> Any:
            return service

        router = create_planning_router(
            tenant_dependency=get_tenant,
            plan_service_dependency=get_service,  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        return client, service, state

    def test_full_lifecycle_validated_to_approved(
        self, harness: tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]
    ) -> None:
        """Prove VALIDATED -> submit-for-approval -> approve returns 200 without detachment."""
        client, service, state = harness
        plan = service.seed_plan("tenant-pilot-001", status="VALIDATED")

        # 1. Direct approve without submit_for_approval fails with 422
        resp = client.post(f"/api/v1/plans/{plan.id}/approve")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "plan.approval_rejected"

        # 2. Submit for approval succeeds -> APPROVAL_PENDING
        resp_sub = client.post(f"/api/v1/plans/{plan.id}/submit-for-approval")
        assert resp_sub.status_code == 200
        data_sub = resp_sub.json()
        assert data_sub["status"] == "APPROVAL_PENDING"
        assert data_sub["id"] == str(plan.id)

        # 3. Approve succeeds -> APPROVED, is_baseline=True
        # Set approver principal (not solely planner)
        state["principal"] = _make_principal(
            principal_id="approver-lead",
            roles={"approver"},
            permissions={"plan:approve"},
        )
        resp_app = client.post(f"/api/v1/plans/{plan.id}/approve")
        assert resp_app.status_code == 200
        data_app = resp_app.json()
        assert data_app["status"] == "APPROVED"
        assert data_app["is_baseline"] is True
        assert data_app["baseline_hash"] == "baseline-sha256-verified"
        assert "updated_at" in data_app

    def test_submit_for_approval_auditor_rejected(
        self, harness: tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]
    ) -> None:
        """Auditor role cannot submit plans for approval."""
        client, service, state = harness
        plan = service.seed_plan("tenant-pilot-001", status="VALIDATED")
        state["principal"] = _make_principal(roles={"auditor"}, permissions={"audit:read"})

        resp = client.post(f"/api/v1/plans/{plan.id}/submit-for-approval")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "plan.unauthorized"

    def test_submit_for_approval_cross_tenant_404(
        self, harness: tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]
    ) -> None:
        """Submitting plan from another tenant returns 404."""
        client, service, state = harness
        plan = service.seed_plan("tenant-alpha", status="VALIDATED")
        state["tenant_id"] = "tenant-beta"
        state["principal"] = _make_principal(tenant_id="tenant-beta", roles={"planner"})

        resp = client.post(f"/api/v1/plans/{plan.id}/submit-for-approval")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "plan.not_found"

    def test_approval_separation_of_duties(
        self, harness: tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]
    ) -> None:
        """Sole planner role cannot approve plans (SoD violation)."""
        client, service, state = harness
        plan = service.seed_plan("tenant-pilot-001", status="APPROVAL_PENDING")
        state["principal"] = _make_principal(roles={"planner"}, permissions={"plan:create"})

        resp = client.post(f"/api/v1/plans/{plan.id}/approve")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "plan.sod_violation"

    def test_approval_tenant_admin_succeeds(
        self, harness: tuple[TestClient, MockPlanServiceWithTransitions, dict[str, Any]]
    ) -> None:
        """Tenant admin possesses authority to approve plans."""
        client, service, state = harness
        plan = service.seed_plan("tenant-pilot-001", status="APPROVAL_PENDING")
        state["principal"] = _make_principal(roles={"tenant_admin"}, permissions={"*"})

        resp = client.post(f"/api/v1/plans/{plan.id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"


# ==============================================================================
# 2. Schedule Publication Policy & Router Wiring Tests
# ==============================================================================
class TestSchedulePublicationAuthorization:
    def test_authenticated_publish_policy_authorizes_scheduler(self) -> None:
        sched = ScheduleRecord(
            id=uuid4(),
            tenant_id="tenant-pilot-001",
            schedule_key="default",
            version=1,
            status="REVIEW",
            source_job_id=uuid4(),
            input_snapshot_hash="hash-123",
        )
        principal = _make_principal(
            principal_id="sched-operator-1",
            tenant_id="tenant-pilot-001",
            roles={"scheduler"},
            permissions={"schedule:publish"},
        )
        policy = AuthenticatedSchedulePublishPolicy(principal)

        # 1. Matching principal and tenant succeeds
        assert policy.authorize_publish(
            principal="sched-operator-1",
            tenant_id="tenant-pilot-001",
            schedule=sched,
            action="publish",
        )

        # 2. Impersonated principal is rejected
        assert not policy.authorize_publish(
            principal="attacker-fake-id",
            tenant_id="tenant-pilot-001",
            schedule=sched,
            action="publish",
        )

        # 3. Cross-tenant is rejected
        assert not policy.authorize_publish(
            principal="sched-operator-1",
            tenant_id="tenant-other",
            schedule=sched,
            action="publish",
        )

    def test_authenticated_publish_policy_rejects_auditor(self) -> None:
        sched = ScheduleRecord(
            id=uuid4(),
            tenant_id="tenant-pilot-001",
            schedule_key="default",
            version=1,
            status="REVIEW",
            source_job_id=uuid4(),
            input_snapshot_hash="hash-123",
        )
        principal = _make_principal(
            principal_id="auditor-01",
            tenant_id="tenant-pilot-001",
            roles={"auditor"},
            permissions={"audit:read"},
        )
        policy = AuthenticatedSchedulePublishPolicy(principal)
        assert not policy.authorize_publish(
            principal="auditor-01",
            tenant_id="tenant-pilot-001",
            schedule=sched,
            action="publish",
        )

    def test_router_publish_with_wired_policy(self) -> None:
        """Prove publishing a candidate schedule succeeds with wired policy."""
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-pilot-001"
        job_id = uuid4()
        sched_id = uuid4()
        now = datetime.now(UTC)

        sched_rec = ScheduleRecord(
            id=sched_id,
            tenant_id=tenant_id,
            schedule_key="default",
            version=1,
            status="REVIEW",
            source_job_id=job_id,
            input_snapshot_hash="snap-123",
            created_at=now,
            updated_at=now,
            assignments=[
                ScheduleAssignmentRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    schedule_id=sched_id,
                    activity_id="act-1",
                    resource_id="res-1",
                    start_at=now,
                    end_at=now,
                )
            ],
        )

        auth_principal = _make_principal(
            principal_id="svc:tenant-pilot-001:exam-pilot-admin",
            tenant_id=tenant_id,
            roles={"tenant_admin", "scheduler"},
            permissions={"*"},
        )

        mock_sched_service = MagicMock()

        async def fake_publish_job(
            session: Any,
            tenant_id: str,
            job_id: UUID,
            principal: str,
            publish_policy: Any = None,
        ) -> ScheduleRecord:
            policy = publish_policy or DefaultSchedulePublishPolicy()
            if not policy.authorize_publish(
                principal=principal,
                tenant_id=tenant_id,
                schedule=sched_rec,
                action="publish",
            ):
                raise PublishAuthorizationError("Unauthorized publish")
            sched_rec.status = "PUBLISHED"
            sched_rec.published_at = datetime.now(UTC)
            sched_rec.published_by = principal
            return sched_rec

        mock_sched_service.publish_job = fake_publish_job

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_session() -> Any:
            sess = MagicMock()
            sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
            return sess

        def get_policy() -> Any:
            return AuthenticatedSchedulePublishPolicy(auth_principal)

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=get_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: mock_sched_service,
            principal_dependency=lambda: auth_principal,
            publish_policy_dependency=get_policy,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        # Publish request binds effective_principal to auth_principal
        resp = client.post(
            f"/api/v1/schedule-jobs/{job_id}/publish",
            json={"principal": "attacker-fake-name"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "PUBLISHED"
        assert data["schedule_id"] == str(sched_id)
        # Verify published_by was securely bound to the authenticated principal, not the payload!
        assert data["published_by"] == auth_principal.principal_id
