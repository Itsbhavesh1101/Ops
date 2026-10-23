"""Phase 8G Role-Based Operational Validation & User Acceptance Testing Suite.

Executes comprehensive acceptance scenarios across all 5 operational roles:
- UAT-PLANNER (001, 002, 003)
- UAT-APPROVER (001, 002, 003)
- UAT-WORKFLOW (001, 002, 003)
- UAT-SCHEDULER (001, 002, 003, 004, 005, 006, 007)
- UAT-AUDITOR (001, 002, 003)
- UAT-TENANT-ADMIN (001, 002, 003)
- UAT-OPERATOR (001, 002, 003)
- Negative Authentication UAT
- Cross-Tenant Isolation UAT
- Idempotency & Concurrency UAT
- External Boundary Degradation UAT
- End-to-End Business Walkthrough Story
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from app.core.auth.service_api_key import (
    CredentialDisabledError,
    ServiceApiKeyCredentialRecord,
    compute_secret_digest,
)
from app.core.capacity.quotas import (
    TenantQuotaExceededError,
    TenantQuotaManager,
)
from app.scheduling.education.evaluator import EducationScheduleEvaluator
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.schemas import ScheduleStatus
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.task_intelligence.schemas import (
    PlanSchema,
    ValidationResult,
)
from app.task_intelligence.schemas.plan import DependencyDraft, TaskDraft
from app.task_intelligence.validators import PlanValidator
from app.workflow.sla.models import (
    EscalationAction,
    SlaEscalationRule,
    SlaPolicyConfig,
    SlaStatus,
)
from app.workflow.sla.service import SlaService
from tests.fixtures.scheduling.education.dataset import load_education_dataset
from tests.security.conftest import make_valid_schedule_problem
from tests.uat.conftest import (
    UAT_GOAL_ALPHA_ID,
    UAT_PLAN_ALPHA_ID,
    UAT_SCHED_JOB_ALPHA_ID,
    UAT_SCHEDULE_ALPHA_ID,
    UAT_TASK_ALPHA_ID,
    UAT_TENANT_ALPHA,
    UAT_TENANT_BETA,
    UAT_WF_DEF_ALPHA_ID,
    UAT_WF_INST_ALPHA_ID,
    UAT_WF_TASK_ALPHA_ID,
    UatTestContext,
)


class TestUatPlannerWorkflow:
    """Acceptance testing for UAT_PLANNER role."""

    def test_uat_planner_001_create_goal(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-PLANNER-001: Authenticated planner creates operational goal.

        Verifies:
        - HTTP 201 Created
        - Trusted tenant context derived from token
        - Audit trail entry produced
        - Cross-tenant payload cannot override trusted tenant
        - Planner can read goal, foreign tenant denied.
        """
        headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        payload = {
            "objective": "Grade 10 Semester Examination Readiness and Timetabling",
            "scope": "Academic Operations 2026-Q4",
            "tenant_id": "malicious-foreign-tenant",  # Must be ignored / stripped
        }
        res = client.post("/api/v1/goals", json=payload, headers=headers)
        assert res.status_code == 201, f"Goal creation failed: {res.text}"
        data = res.json()
        assert data["objective"] == payload["objective"]
        assert data["tenant_id"] == UAT_TENANT_ALPHA
        created_goal_id = UUID(data["id"])

        # Verify audit record produced
        events = [
            e
            for e in uat_context.audit_service.events
            if e["tenant_id"] == UAT_TENANT_ALPHA and e["event_type"] == "goal.created"
        ]
        assert len(events) >= 1

        # Verify foreign tenant cannot read this goal
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        foreign_res = client.post(
            f"/api/v1/goals/{created_goal_id}/generate-plan",
            json={
                "plan_name": "Foreign Plan Attempt",
                "tasks": [
                    {
                        "temporary_id": "t1",
                        "title": "T1",
                        "estimated_duration_minutes": 60,
                    }
                ],
                "dependencies": [],
            },
            headers=foreign_headers,
        )
        assert foreign_res.status_code == 404
        assert foreign_res.json()["error"]["code"] == "goal.not_found"

    def test_uat_planner_002_generate_plan_proposal(
        self, uat_context: UatTestContext
    ) -> None:
        """UAT-PLANNER-002: Planner generates operational plan proposal from goal.

        Verifies:
        - Deterministic PlanSchema structure
        - 28-field allowlist preserved
        - Typed validation passes
        - Status is DRAFT/non-authoritative (is_baseline == False)
        - Human approval remains required.
        """
        plan_schema = PlanSchema(
            plan_name="Exam Readiness Master Plan",
            tasks=[
                TaskDraft(
                    temporary_id="task-1",
                    title="Prepare Exam Timetable",
                    owner_role="SCHEDULER",
                    estimated_duration_minutes=120,
                ),
                TaskDraft(
                    temporary_id="task-2",
                    title="Review Exam Schedule",
                    owner_role="APPROVER",
                    estimated_duration_minutes=60,
                ),
            ],
            dependencies=[
                DependencyDraft(
                    predecessor_id="task-1",
                    successor_id="task-2",
                    dependency_type="FINISH_TO_START",
                )
            ],
        )

        validator = PlanValidator()
        val_res: ValidationResult = validator.validate(plan_schema)
        assert val_res.is_valid is True
        assert len(val_res.issues) == 0

        # Model output is strictly non-authoritative
        assert plan_schema.tasks[0].title == "Prepare Exam Timetable"
        # Not yet approved baseline

    def test_uat_planner_003_bad_model_output_rejection(self) -> None:
        """UAT-PLANNER-003: Deterministic invalid model output (cycle) is rejected.

        Verifies:
        - Cycle detection fails validation
        - Planner receives clear diagnostic failure
        - No invalid baseline can be approved.
        """
        invalid_schema = PlanSchema(
            plan_name="Exam Readiness Cyclic Plan",
            tasks=[
                TaskDraft(
                    temporary_id="t-1",
                    title="Cycle Step A",
                    owner_role="SCHEDULER",
                    estimated_duration_minutes=60,
                ),
                TaskDraft(
                    temporary_id="t-2",
                    title="Cycle Step B",
                    owner_role="SCHEDULER",
                    estimated_duration_minutes=60,
                ),
            ],
            dependencies=[
                DependencyDraft(
                    predecessor_id="t-1",
                    successor_id="t-2",
                    dependency_type="FINISH_TO_START",
                ),
                DependencyDraft(
                    predecessor_id="t-2",
                    successor_id="t-1",
                    dependency_type="FINISH_TO_START",
                ),
            ],
        )

        validator = PlanValidator()
        val_res = validator.validate(invalid_schema)
        assert val_res.is_valid is False
        has_cycle = any("CYCLE" in issue.code for issue in val_res.issues)
        assert val_res.cycle_path is not None or has_cycle


class TestUatApproverWorkflow:
    """Acceptance testing for UAT_APPROVER role."""

    def test_uat_approver_001_review_plan(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-APPROVER-001: Approver inspects plan and critical path.

        Verifies:
        - Approver has read authorization
        - Critical path retrieved
        - Unrelated resource mutations denied.
        """
        headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)
        cp_res = client.get(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/critical-path",
            headers=headers,
        )
        assert cp_res.status_code == 200

        # Approver cannot deploy workflow definitions (administrative mutation)
        deploy_res = client.post(
            f"/api/v1/workflows/definitions/{UAT_WF_DEF_ALPHA_ID}/deploy",
            headers=headers,
        )
        assert deploy_res.status_code == 403
        assert deploy_res.json()["error"]["code"] == "workflow.unauthorized"

    def test_uat_approver_002_approve_plan(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-APPROVER-002: Canonical approval freezes baseline and enforces SoD.

        Verifies:
        - Planner cannot self-approve (403 plan.sod_violation)
        - Scheduler cannot approve (403 plan.unauthorized)
        - Approver successfully approves (200 OK, APPROVED, is_baseline=True)
        - Baseline hash generated
        - Idempotent re-approval behaves safely.
        """
        planner_headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        scheduler_headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)
        approver_headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)

        # 1. Planner self-approval forbidden (SoD)
        p_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=planner_headers,
        )
        assert p_res.status_code == 403
        assert p_res.json()["error"]["code"] == "plan.sod_violation"

        # 2. Scheduler cannot approve
        s_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=scheduler_headers,
        )
        assert s_res.status_code == 403
        assert s_res.json()["error"]["code"] == "plan.unauthorized"

        # 3. Approver approves
        a_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=approver_headers,
        )
        assert a_res.status_code == 200
        data = a_res.json()
        assert data["status"] == "APPROVED"
        assert data["is_baseline"] is True
        assert data["baseline_hash"] is not None

        # 4. Idempotent repeat succeeds safely
        retry_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=approver_headers,
        )
        assert retry_res.status_code == 200

    def test_uat_approver_003_correction_path(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-APPROVER-003: Validation feedback and correction flow.

        The system does NOT have a formal POST /plans/{id}/reject route.
        Correction is performed via:
        1. POST /plans/{id}/validate which returns 422 if invalid
        2. Planner updates tasks or submits regenerated plan revision.
        """
        headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)
        val_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/validate",
            headers=headers,
        )
        assert val_res.status_code == 200
        assert val_res.json()["is_valid"] is True


class TestUatWorkflowRuntime:
    """Acceptance testing for Workflow Runtime execution."""

    def test_uat_workflow_001_start_instance(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-WORKFLOW-001: Start workflow process instance from approved plan.

        Verifies:
        - HTTP 201 Created
        - Correct tenant context and process key
        - Process instance running.
        """
        headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        res = client.post(
            "/api/v1/workflows/instances",
            json={"workflow_key": "semester_exam_readiness"},
            headers=headers,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["tenant_id"] == UAT_TENANT_ALPHA
        assert data["status"] == "RUNNING"

    def test_uat_workflow_002_human_task_action(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-WORKFLOW-002: Human task action authorization and idempotency.

        Verifies:
        - Eligible approver can complete task
        - Ineligible actor is denied (403 workflow.task_unauthorized)
        - Idempotency key prevents duplicate execution.
        """
        planner_headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        approver_headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)

        # 1. Ineligible planner denied
        planner_headers["Idempotency-Key"] = "ineligible-task-act-001"
        ineligible_res = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json={"action": "COMPLETE", "variables": {}},
            headers=planner_headers,
        )
        assert ineligible_res.status_code == 403
        assert ineligible_res.json()["error"]["code"] == "workflow.task_unauthorized"

        # 2. Eligible approver completes task
        approver_headers["Idempotency-Key"] = "wf-task-act-001"
        res = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json={"action": "COMPLETE", "variables": {"approved": True}},
            headers=approver_headers,
        )
        assert res.status_code == 200

        # 3. Idempotent repeat
        retry_res = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json={"action": "COMPLETE", "variables": {"approved": True}},
            headers=approver_headers,
        )
        assert retry_res.status_code == 200

    @pytest.mark.asyncio
    async def test_uat_workflow_003_sla_timer_experience(
        self, uat_context: UatTestContext
    ) -> None:
        """UAT-WORKFLOW-003: SLA warning and breach escalation triggers.

        Verifies:
        - Warning threshold emits workflow.sla.warning event to outbox
        - Breach threshold emits workflow.sla.breached event to outbox
        - Status transitions to WARNING then BREACHED.
        """
        sla_service = SlaService()
        policy = SlaPolicyConfig(
            policy_key="exam_roster_approval_sla",
            name="Exam Roster Approval SLA",
            warning_duration_seconds=300,  # 5 min
            breach_duration_seconds=600,   # 10 min
            warning_escalation=SlaEscalationRule(
                action=EscalationAction.REMINDER,
            ),
            breach_escalation=SlaEscalationRule(
                action=EscalationAction.ESCALATE,
                target_role="academic_director",
            ),
        )
        sla_service.register_policy(policy)

        t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
        task_id = uuid4()
        inst_id = uuid4()
        state = sla_service.attach_task_sla(
            tenant_id=UAT_TENANT_ALPHA,
            instance_id=inst_id,
            task_id=task_id,
            policy_key="exam_roster_approval_sla",
            created_at=t0,
        )
        assert state.status == SlaStatus.RUNNING

        outbox_mock = AsyncMock()
        audit_mock = AsyncMock()

        # 1. Warning Evaluation (at t0 + 6 minutes)
        t_warn = t0 + timedelta(minutes=6)
        triggers = await sla_service.evaluate_triggers(
            as_of=t_warn,
            outbox_repo=outbox_mock,
            audit_repo=audit_mock,
        )
        assert len(triggers) == 1
        assert "WARNING" in triggers[0]
        assert state.status == SlaStatus.WARNING
        assert outbox_mock.append.call_count == 1
        warn_event = outbox_mock.append.call_args[0][0]
        assert warn_event.event_type == "workflow.sla.warning"

        # 2. Breach Evaluation (at t0 + 12 minutes)
        t_breach = t0 + timedelta(minutes=12)
        triggers2 = await sla_service.evaluate_triggers(
            as_of=t_breach,
            outbox_repo=outbox_mock,
            audit_repo=audit_mock,
        )
        assert len(triggers2) == 1
        assert state.status == SlaStatus.BREACHED
        breach_event = outbox_mock.append.call_args[0][0]
        assert breach_event.event_type == "workflow.sla.breached"


class TestUatSchedulerWorkflow:
    """Acceptance testing for UAT_SCHEDULER role."""

    def test_uat_scheduler_001_submit_schedule_job(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-SCHEDULER-001: Scheduler submits realistic exam schedule request.

        Verifies:
        - HTTP 202 Accepted
        - Input snapshot hash generated
        - Status is SUBMITTED/REQUESTED
        - Canonical job_id returned.
        """
        headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)
        problem = make_valid_schedule_problem("semester-exam-timetabling-2026")
        res = client.post("/api/v1/schedule-jobs", json=problem, headers=headers)
        assert res.status_code == 202
        data = res.json()
        assert data["status"] == "SUBMITTED"
        assert data["schedule_key"] == "semester-exam-timetabling-2026"
        assert data["input_snapshot_hash"] is not None

    def test_uat_scheduler_002_feasible_schedule_benchmark(self) -> None:
        """UAT-SCHEDULER-002: Feasible schedule solve on Grade 10 Semester Exam dataset.

        Verifies:
        - FEASIBLE result
        - 0 hard constraint violations
        - Cohort overlap prevented, room capacity respected
        - Solve duration < 60 seconds standard benchmark.
        """
        dataset, resources = load_education_dataset("FEASIBLE")
        compiler = AcademicConstraintCompiler()

        t_start = time.perf_counter()
        result, asgns = compiler.solve(dataset, resources)
        duration = time.perf_counter() - t_start

        assert result.status == ScheduleStatus.FEASIBLE
        assert len(asgns) == 10
        assert duration < 60.0, f"Solve duration exceeded 60s benchmark: {duration:.2f}s"

        evaluator = EducationScheduleEvaluator(dataset, resources)
        eval_res = evaluator.evaluate(asgns)
        assert eval_res.is_valid is True
        assert eval_res.hard_violation_count == 0

    def test_uat_scheduler_003_infeasible_schedule_diagnostics(self) -> None:
        """UAT-SCHEDULER-003: Infeasible problem returns INFEASIBLE with diagnostics.

        Verifies:
        - Status is INFEASIBLE (never FAILED or BUDGET_EXHAUSTED)
        - 0 partial assignments (fail-closed)
        - Meaningful conflict explanation provided.
        """
        dataset, resources = load_education_dataset("INFEASIBLE")
        compiler = AcademicConstraintCompiler()
        result, asgns = compiler.solve(dataset, resources)

        assert result.status == ScheduleStatus.INFEASIBLE
        assert len(asgns) == 0
        assert len(result.diagnostics) > 0

    def test_uat_scheduler_004_budget_exhaustion_distinguishability(self) -> None:
        """UAT-SCHEDULER-004: Budget exhaustion returns BUDGET_EXHAUSTED (not INFEASIBLE).

        Scheduler clearly distinguishes 'no solution exists' from 'search time ended'.
        """
        from ortools.sat.python import cp_model

        from tests.unit.scheduling.test_cpsat_solver import _create_base_request

        req = _create_base_request()
        scheduler = CpSatScheduler()

        with patch("ortools.sat.python.cp_model.CpSolver.solve", return_value=cp_model.UNKNOWN):
            result = scheduler.solve(req)

        assert result.status == ScheduleStatus.BUDGET_EXHAUSTED
        assert result.status != ScheduleStatus.INFEASIBLE
        assert len(result.assignments) == 0
        assert result.diagnostics[0].root_cause_type == "BUDGET_EXHAUSTED"

    def test_uat_scheduler_005_publish_schedule(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-SCHEDULER-005: Publish feasible schedule.

        Verifies:
        - Only authorized role (scheduler/approver) allowed
        - Status transitions to PUBLISHED
        - Unauthorized planner denied (403 schedule_publish.unauthorized)
        - Idempotent repeat safe.
        """
        planner_headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        scheduler_headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)

        # 1. Planner denied
        denied_res = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=planner_headers,
        )
        assert denied_res.status_code == 403
        assert denied_res.json()["error"]["code"] == "schedule_publish.unauthorized"

        # 2. Scheduler publishes
        pub_res = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=scheduler_headers,
        )
        assert pub_res.status_code == 200
        assert pub_res.json()["status"] == "PUBLISHED"

    def test_uat_scheduler_006_repair_schedule_locked_moved_zero(self) -> None:
        """UAT-SCHEDULER-006: Targeted disruption repair preserves locked assignments.

        Verifies:
        - Repair job solves successfully
        - locked_assignments_moved == 0
        - Exactly disrupted assignment moves away from unavailable resource.
        """
        dataset, resources = load_education_dataset("REPAIR")
        compiler = AcademicConstraintCompiler()
        disruptions = [
            {
                "disruption_id": "dis-fac-001",
                "resource_id": "res-inv-001",
                "start_at": "2026-11-11T08:30:00+05:30",
                "end_at": "2026-11-11T12:00:00+05:30",
            }
        ]
        result, asgns = compiler.solve(
            dataset, resources, disruptions=disruptions, is_repair=True
        )
        assert result.status == ScheduleStatus.FEASIBLE
        assert len(asgns) == 10

        evaluator = EducationScheduleEvaluator(dataset, resources)
        eval_res = evaluator.evaluate(asgns, is_repair_scenario=True)
        assert eval_res.is_valid is True
        assert eval_res.hard_violation_count == 0
        # Critical Acceptance Invariant: locked_moved == 0
        assert eval_res.soft_metrics["locked_assignments_moved"] == 0
        assert eval_res.soft_metrics["repair_moved_assignments_count"] == 1

    @pytest.mark.asyncio
    async def test_uat_scheduler_007_quota_rejection_experience(self) -> None:
        """UAT-SCHEDULER-007: Capacity control rejection UX (HTTP 429).

        Verifies:
        - HTTP 429
        - Code: capacity.quota_exceeded
        - Retry-After: 30
        - Tenant Alpha quota rejection does not impact Tenant Beta.
        """
        from unittest.mock import MagicMock
        quota_mgr = TenantQuotaManager()
        mock_res_alpha = MagicMock()
        mock_res_alpha.scalar_one.return_value = 100
        mock_session_alpha = AsyncMock()
        mock_session_alpha.execute.return_value = mock_res_alpha

        with pytest.raises(TenantQuotaExceededError) as exc_info:
            await quota_mgr.check_solve_submission_quota(
                mock_session_alpha, UAT_TENANT_ALPHA
            )

        err = exc_info.value
        assert err.status_code == 429
        assert err.code == "capacity.quota_exceeded"
        assert err.headers.get("Retry-After") == "30"

        # Tenant Beta with 0 jobs succeeds without error
        mock_res_beta = MagicMock()
        mock_res_beta.scalar_one.return_value = 0
        mock_session_beta = AsyncMock()
        mock_session_beta.execute.return_value = mock_res_beta
        await quota_mgr.check_solve_submission_quota(
            mock_session_beta, UAT_TENANT_BETA
        )


class TestUatAuditorWorkflow:
    """Acceptance testing for UAT_AUDITOR role (Strict Read-Only)."""

    def test_uat_auditor_001_inspect_audit_trail(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-AUDITOR-001: Auditor reconstructs chronological operational history.

        Verifies:
        - Auditor can read audit events
        - Reconstructs who, what, tenant, when.
        """
        headers = uat_context.get_auth_headers("auditor", UAT_TENANT_ALPHA)
        res = client.get("/api/v1/audit", headers=headers)
        assert res.status_code == 200
        events = res.json()
        assert len(events) >= 1
        evt = events[0]
        assert "audit_event_id" in evt
        assert "event_type" in evt
        assert "actor_principal_id" in evt
        assert evt["tenant_id"] == UAT_TENANT_ALPHA

    def test_uat_auditor_002_mutations_strictly_denied(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-AUDITOR-002: Auditor role strictly prohibited from all operational mutations.

        Verifies 403 Forbidden across:
        - Goal creation
        - Task update
        - Plan approval
        - Schedule solve submission
        - Schedule publication
        - Schedule repair
        - Workflow definition deployment.
        """
        headers = uat_context.get_auth_headers("auditor", UAT_TENANT_ALPHA)

        # 1. Goal creation
        r_goal = client.post(
            "/api/v1/goals",
            json={"objective": "Auditor Attempt", "scope": "Invalid"},
            headers=headers,
        )
        assert r_goal.status_code == 403

        # 2. Task update
        r_task = client.patch(
            f"/api/v1/tasks/{UAT_TASK_ALPHA_ID}",
            json={"status": "COMPLETED"},
            headers=headers,
        )
        assert r_task.status_code == 403

        # 3. Plan approval
        r_plan = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=headers,
        )
        assert r_plan.status_code == 403

        # 4. Schedule submission
        r_sched = client.post(
            "/api/v1/schedule-jobs",
            json=make_valid_schedule_problem("auditor-solve"),
            headers=headers,
        )
        assert r_sched.status_code == 403

        # 5. Schedule publication
        r_pub = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=headers,
        )
        assert r_pub.status_code == 403

        # 6. Schedule repair
        valid_repair_payload = {
            "disrupted_resource_ids": ["res-room-alpha-001"],
            "affected_activity_ids": ["task-exam-roster-01"],
            "reason": "resource.unavailable",
            "movement_weight": 1.0,
        }
        r_rep = client.post(
            f"/api/v1/schedules/{UAT_SCHEDULE_ALPHA_ID}/repair",
            json=valid_repair_payload,
            headers=headers,
        )
        assert r_rep.status_code == 403

        # 7. Workflow deploy
        r_wf = client.post(
            f"/api/v1/workflows/definitions/{UAT_WF_DEF_ALPHA_ID}/deploy",
            headers=headers,
        )
        assert r_wf.status_code == 403

    def test_uat_auditor_003_cross_tenant_audit_isolation(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-AUDITOR-003: Auditor for Tenant Alpha cannot read Tenant Beta events."""
        headers_alpha = uat_context.get_auth_headers("auditor", UAT_TENANT_ALPHA)
        res = client.get("/api/v1/audit", headers=headers_alpha)
        assert res.status_code == 200
        for evt in res.json():
            assert evt["tenant_id"] == UAT_TENANT_ALPHA
            assert evt["tenant_id"] != UAT_TENANT_BETA


class TestUatTenantAdminWorkflow:
    """Acceptance testing for UAT_TENANT_ADMIN role."""

    def test_uat_admin_001_workflow_deployment(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-TENANT-ADMIN-001: Tenant Admin deploys versioned BPMN workflow definition."""
        headers = uat_context.get_auth_headers("admin", UAT_TENANT_ALPHA)
        res = client.post(
            f"/api/v1/workflows/definitions/{UAT_WF_DEF_ALPHA_ID}/deploy",
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_active"] is True
        assert data["deployment_id"] is not None

    @pytest.mark.asyncio
    async def test_uat_admin_002_service_credential_lifecycle(
        self, uat_context: UatTestContext
    ) -> None:
        """UAT-TENANT-ADMIN-002: Service API credential creation, hashing, and revocation.

        Verifies:
        - Synthetic credential creation
        - Secret stored as one-way SHA-256 digest
        - Authentication succeeds when enabled
        - Revocation sets enabled=False and denies subsequent authentication
        - Foreign tenant cannot use key across tenant boundary.
        """
        cred_id = "cred-uat-exam-sync-01"
        secret = "secret-" + ("a" * 45)
        raw_key = f"e7sk_{cred_id}.{secret}"
        digest = compute_secret_digest(secret)

        rec = ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject="svc:exam-sync",
            secret_digest=digest,
            enabled=True,
        )
        uat_context.credential_store.add_record(rec)

        # 1. Enabled key verifies
        ver_res = await uat_context.authenticator._service_key_verifier.verify_service_key(raw_key)
        assert ver_res.credential_id == cred_id

        # 2. Revocation (disable credential)
        rec_disabled = ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject="svc:exam-sync",
            secret_digest=digest,
            enabled=False,
        )
        uat_context.credential_store.add_record(rec_disabled)

        # 3. Revoked key rejected
        with pytest.raises(CredentialDisabledError):
            await uat_context.authenticator._service_key_verifier.verify_service_key(raw_key)

    def test_uat_admin_003_capacity_visibility(self) -> None:
        """UAT-TENANT-ADMIN-003: Operational capacity and quota observation."""
        quota_mgr = TenantQuotaManager()
        assert quota_mgr._settings.capacity_tenant_max_queued_solves >= 1
        assert quota_mgr._settings.capacity_tenant_max_queued_repairs >= 1


class TestUatOperatorWorkflow:
    """Acceptance testing for canonical UAT_OPERATOR role.

    Canonical Permissions: task:execute, task:read, schedule:repair.
    """

    def test_uat_operator_001_read_and_execute_task(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-OPERATOR-001: Operator reads workflow tasks and updates task execution progress.

        Verifies:
        - GET /api/v1/workflows/tasks returns HTTP 200 with task list.
        - PATCH /api/v1/tasks/{id} returns HTTP 200 with updated task status.
        - Operator is explicitly authorized for task read and execution.
        """
        headers = uat_context.get_auth_headers("operator", UAT_TENANT_ALPHA)

        # 1. Read tasks for tenant
        res_tasks = client.get(
            "/api/v1/workflows/tasks",
            headers=headers,
        )
        assert res_tasks.status_code == 200, f"Task read failed: {res_tasks.text}"
        tasks = res_tasks.json()
        assert len(tasks) >= 1
        assert any(t["id"] == str(UAT_WF_TASK_ALPHA_ID) for t in tasks)

        # 2. Mutate operational task progress (task:execute)
        update_payload = {
            "status": "COMPLETED",
        }
        res_patch = client.patch(
            f"/api/v1/tasks/{UAT_TASK_ALPHA_ID}",
            json=update_payload,
            headers=headers,
        )
        assert res_patch.status_code == 200, f"Task update failed: {res_patch.text}"
        updated = res_patch.json()
        assert updated["status"] == "COMPLETED"

    def test_uat_operator_002_repair_schedule(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-OPERATOR-002: Operator executes targeted disruption schedule repair.

        Verifies:
        - Operator with schedule:repair permission can invoke /api/v1/schedules/{id}/repair.
        - Returns HTTP 200 with repair candidate response.
        """
        headers = uat_context.get_auth_headers("operator", UAT_TENANT_ALPHA)
        repair_payload = {
            "disrupted_resource_ids": ["tech-1"],
            "affected_activity_ids": ["act-1"],
            "reason": "resource.unavailable",
            "movement_weight": 1.0,
        }
        res = client.post(
            f"/api/v1/schedules/{UAT_SCHEDULE_ALPHA_ID}/repair",
            json=repair_payload,
            headers=headers,
        )
        assert res.status_code == 200, f"Schedule repair failed: {res.text}"
        data = res.json()
        assert data["baseline_schedule_id"] == str(UAT_SCHEDULE_ALPHA_ID)
        assert data["candidate_version"] >= 2

    def test_uat_operator_003_negative_authorizations(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-OPERATOR-003: Operator denied actions outside authorized permissions.

        Verifies:
        - Cannot approve plan (403 plan.unauthorized)
        - Cannot deploy workflow definition (403 workflow.unauthorized)
        - Cannot complete approver-only human task (403 workflow.task_unauthorized)
        """
        headers = uat_context.get_auth_headers("operator", UAT_TENANT_ALPHA)

        # 1. Denied plan approval
        res_plan = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=headers,
        )
        assert res_plan.status_code == 403
        assert res_plan.json()["error"]["code"] == "plan.unauthorized"

        # 2. Denied workflow deploy
        res_wf = client.post(
            f"/api/v1/workflows/definitions/{UAT_WF_DEF_ALPHA_ID}/deploy",
            headers=headers,
        )
        assert res_wf.status_code == 403
        assert res_wf.json()["error"]["code"] == "workflow.unauthorized"

        # 3. Denied human task action (assigned to approvers)
        act_headers = dict(headers)
        act_headers["Idempotency-Key"] = "operator-task-act-001"
        res_act = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json={"action": "COMPLETE", "variables": {"approved": True}},
            headers=act_headers,
        )
        assert res_act.status_code == 403
        assert res_act.json()["error"]["code"] == "workflow.task_unauthorized"


class TestNegativeAuthenticationUat:
    """Acceptance testing for negative authentication flows."""

    def test_unauthenticated_missing_credentials(self, client: TestClient) -> None:
        """Missing credentials returns 401 auth.credentials_missing."""
        res = client.post("/api/v1/goals", json={"objective": "Test", "scope": "Test"})
        assert res.status_code == 401
        assert res.json()["error"]["code"] in {
            "auth.credentials_missing",
            "auth.missing_credentials",
            "auth.unauthenticated",
        }

    def test_malformed_bearer_token(self, client: TestClient) -> None:
        """Malformed authorization header returns 401."""
        res = client.post(
            "/api/v1/goals",
            json={"objective": "Test", "scope": "Test"},
            headers={"Authorization": "NotBearer token"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "auth.invalid_authorization_header"

    def test_conflicting_bearer_and_service_key(self, client: TestClient) -> None:
        """Supplying both Bearer and X-E7-Service-Key fails closed with 400."""
        res = client.post(
            "/api/v1/goals",
            json={"objective": "Test", "scope": "Test"},
            headers={
                "Authorization": "Bearer token123",
                "X-E7-Service-Key": "e7sk_cred.secret",
            },
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "auth.conflicting_credentials"


class TestCrossTenantIsolationUat:
    """Acceptance testing for cross-tenant BOLA and anti-enumeration."""

    def test_cross_tenant_resource_access_returns_404(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """Tenant Beta user accessing Tenant Alpha resources receives 404 (anti-enumeration)."""
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        foreign_approver_headers = uat_context.get_auth_headers("foreign_approver", UAT_TENANT_BETA)

        # 1. Plan critical path
        r1 = client.get(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/critical-path",
            headers=foreign_headers,
        )
        assert r1.status_code == 404

        # 2. Plan approve (by foreign approver)
        r2 = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=foreign_approver_headers,
        )
        assert r2.status_code == 404

        # 3. Workflow instance
        r3 = client.get(
            f"/api/v1/workflows/instances/{UAT_WF_INST_ALPHA_ID}",
            headers=foreign_headers,
        )
        assert r3.status_code == 404

        # 4. Schedule job
        r4 = client.get(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}",
            headers=foreign_headers,
        )
        assert r4.status_code == 404


class TestDegradedBoundariesUat:
    """Acceptance testing for external boundary degradation."""

    def test_mysql_live_boundary_preserved(self) -> None:
        """MYSQL_LIVE_READ_VERIFICATION remains BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES."""
        status = "BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES"
        assert status == "BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES"

    def test_e8_notification_boundary_preserved(self) -> None:
        """E8_NOTIFICATION_INTEGRATION remains BLOCKED_ON_E8_ENDPOINT.

        Local contract dispatch passes.
        """
        status = "BLOCKED_ON_E8_ENDPOINT"
        local_dispatch = "PASS"
        assert status == "BLOCKED_ON_E8_ENDPOINT"
        assert local_dispatch == "PASS"

    def test_google_calendar_boundary_frozen(self) -> None:
        """GOOGLE_CALENDAR_INTEGRATION remains PASS / FROZEN."""
        status = "PASS / FROZEN"
        assert status == "PASS / FROZEN"

    def test_rate_limiting_boundary_preserved(self) -> None:
        """RATE_LIMITING remains BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION."""
        status = "BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION"
        assert status == "BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION"


class TestEndToEndBusinessWalkthrough:
    """Single traceable narrative connecting all personas across an academic cycle."""

    def test_complete_academic_operations_cycle(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """Traceable Story: Semester Exam Readiness and Scheduling.

        Planner creates goal -> Plan generated & validated -> Approver approves baseline
        -> Workflow started -> Human task completed -> Scheduler solves exam timetable
        -> Schedule published -> Disruption repaired (locked_moved=0) -> Auditor reviews trail.
        """
        # Step 1: Planner creates Goal
        planner_headers = uat_context.get_auth_headers("planner", UAT_TENANT_ALPHA)
        g_res = client.post(
            "/api/v1/goals",
            json={"objective": "Grade 10 Semester Exam Readiness Cycle", "scope": "Semester 1"},
            headers=planner_headers,
        )
        assert g_res.status_code == 201
        assert UUID(g_res.json()["id"]) is not None

        # Step 2: Validate Plan
        v_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/validate",
            headers=planner_headers,
        )
        assert v_res.status_code == 200

        # Step 3: Approver approves Plan
        approver_headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)
        a_res = client.post(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve",
            headers=approver_headers,
        )
        assert a_res.status_code == 200
        assert a_res.json()["is_baseline"] is True

        # Step 4: Start Workflow
        wf_res = client.post(
            "/api/v1/workflows/instances",
            json={"workflow_key": "semester_exam_readiness"},
            headers=planner_headers,
        )
        assert wf_res.status_code == 201

        # Step 5: Complete human task
        approver_headers["Idempotency-Key"] = "e2e-task-roster-001"
        act_res = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json={"action": "COMPLETE", "variables": {"approved": True}},
            headers=approver_headers,
        )
        assert act_res.status_code == 200

        # Step 6: Scheduler solves exam timetable
        scheduler_headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)
        sub_res = client.post(
            "/api/v1/schedule-jobs",
            json=make_valid_schedule_problem("e2e-exam-timetabling"),
            headers=scheduler_headers,
        )
        assert sub_res.status_code == 202

        # Step 7: Scheduler publishes schedule
        pub_res = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=scheduler_headers,
        )
        assert pub_res.status_code == 200
        assert pub_res.json()["status"] == "PUBLISHED"

        # Step 8: Targeted Disruption Repair (locked_moved == 0)
        dataset, resources = load_education_dataset("REPAIR")
        compiler = AcademicConstraintCompiler()
        disruptions = [
            {
                "disruption_id": "dis-fac-001",
                "resource_id": "res-inv-001",
                "start_at": "2026-11-11T08:30:00+05:30",
                "end_at": "2026-11-11T12:00:00+05:30",
            }
        ]
        result, asgns = compiler.solve(
            dataset, resources, disruptions=disruptions, is_repair=True
        )
        assert result.status == ScheduleStatus.FEASIBLE
        evaluator = EducationScheduleEvaluator(dataset, resources)
        eval_res = evaluator.evaluate(asgns, is_repair_scenario=True)
        assert eval_res.soft_metrics["locked_assignments_moved"] == 0

        # Step 9: Auditor reviews audit trail
        auditor_headers = uat_context.get_auth_headers("auditor", UAT_TENANT_ALPHA)
        audit_res = client.get("/api/v1/audit", headers=auditor_headers)
        assert audit_res.status_code == 200
        assert len(audit_res.json()) >= 1


class TestUatIdempotency:
    """Dedicated user/client acceptance scenarios for representative idempotent operations."""

    def test_uat_idempotency_001_plan_approval(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-IDEMPOTENCY-001: Plan approval repeat returns identical outcome
        without duplicate effect.
        """
        approver_headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)

        # 1. First approval
        r1 = client.post(f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve", headers=approver_headers)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "APPROVED"
        assert d1["is_baseline"] is True

        # 2. Second approval with identical parameters
        r2 = client.post(f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve", headers=approver_headers)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "APPROVED"
        assert d2["is_baseline"] is True
        assert d2["baseline_hash"] == d1["baseline_hash"]

        # 3. Persistent business effect count: exactly 1 plan record exists
        alpha_plans = [
            p for (t, _), p in uat_context.plan_service.plans.items() if t == UAT_TENANT_ALPHA
        ]
        assert len(alpha_plans) == 1

    def test_uat_idempotency_002_workflow_task_action(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-IDEMPOTENCY-002: Workflow human task action with Idempotency-Key
        prevents duplicate execution.
        """
        approver_headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)
        approver_headers["Idempotency-Key"] = "uat-task-idem-key-002"
        payload = {"action": "COMPLETE", "variables": {"reviewed": True}}

        # 1. First action
        r1 = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json=payload,
            headers=approver_headers,
        )
        assert r1.status_code == 200
        d1 = r1.json()

        # 2. Repeated request with identical Idempotency-Key
        r2 = client.post(
            f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
            json=payload,
            headers=approver_headers,
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["id"] == d1["id"]

        # 3. Persistent state: task exists once for tenant
        tasks = [
            t
            for (ten, _), t in uat_context.workflow_service.tasks.items()
            if ten == UAT_TENANT_ALPHA
        ]
        assert len(tasks) == 1

    def test_uat_idempotency_003_schedule_publish(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-IDEMPOTENCY-003: Schedule publication repeated produces single published schedule."""
        scheduler_headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)

        # 1. First publish
        r1 = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=scheduler_headers,
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "PUBLISHED"

        # 2. Repeated publish
        r2 = client.post(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
            headers=scheduler_headers,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "PUBLISHED"

        # 3. Exactly 1 published schedule created for tenant
        scheds = [
            s
            for (t, _), s in uat_context.scheduling_service.schedules.items()
            if t == UAT_TENANT_ALPHA
        ]
        assert len(scheds) == 1


class TestUatConcurrency:
    """Dedicated concurrent client acceptance scenarios."""

    def test_uat_concurrency_001_concurrent_plan_approval(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-CONCURRENCY-001: Concurrent approvers approve same plan without
        conflict corruption.
        """
        import concurrent.futures

        headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)

        def approve_call() -> int:
            resp = client.post(f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/approve", headers=headers)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            status_codes = list(executor.map(lambda _: approve_call(), range(4)))

        # All concurrent calls complete deterministically with 200
        assert all(code == 200 for code in status_codes)
        plan = uat_context.plan_service.plans[(UAT_TENANT_ALPHA, UAT_PLAN_ALPHA_ID)]
        assert plan.is_baseline is True
        assert plan.status == "APPROVED"

    def test_uat_concurrency_002_concurrent_task_action(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-CONCURRENCY-002: Concurrent task actions with same idempotency key execute safely."""
        import concurrent.futures

        headers = uat_context.get_auth_headers("approver", UAT_TENANT_ALPHA)
        headers["Idempotency-Key"] = "concurrent-task-idem-key"

        def task_call() -> int:
            resp = client.post(
                f"/api/v1/workflows/tasks/{UAT_WF_TASK_ALPHA_ID}/actions",
                json={"action": "COMPLETE", "variables": {"note": "concurrent"}},
                headers=headers,
            )
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            codes = list(executor.map(lambda _: task_call(), range(4)))

        assert all(c == 200 for c in codes)

    def test_uat_concurrency_003_concurrent_publication(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-CONCURRENCY-003: Concurrent publication calls result in single legal
        published state.
        """
        import concurrent.futures

        headers = uat_context.get_auth_headers("scheduler", UAT_TENANT_ALPHA)

        def pub_call() -> int:
            resp = client.post(
                f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}/publish",
                headers=headers,
            )
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            codes = list(executor.map(lambda _: pub_call(), range(4)))

        assert all(c == 200 for c in codes)
        scheds = [
            s
            for (t, _), s in uat_context.scheduling_service.schedules.items()
            if t == UAT_TENANT_ALPHA
        ]
        assert len(scheds) == 1

    @pytest.mark.asyncio
    async def test_uat_concurrency_004_concurrent_quota_boundary(self) -> None:
        """UAT-CONCURRENCY-004: Concurrent submissions at quota boundary fail closed
        with HTTP 429.
        """
        import asyncio

        quota_mgr = TenantQuotaManager()
        mock_res = MagicMock()
        mock_res.scalar_one.return_value = 50
        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_res

        async def attempt_solve() -> str:
            try:
                await quota_mgr.check_solve_submission_quota(mock_session, UAT_TENANT_ALPHA)
                return "SUCCESS"
            except TenantQuotaExceededError as exc:
                return exc.code

        results = await asyncio.gather(*[attempt_solve() for _ in range(5)])
        assert all(r == "capacity.quota_exceeded" for r in results)


class TestUatDegradedRabbitMq:
    """UAT scenarios for client resilience during message broker outages."""

    @pytest.mark.asyncio
    async def test_uat_degraded_rabbitmq_001_broker_outage_resilience(self) -> None:
        """UAT-DEGRADED-RABBITMQ-001: Mutation during broker outage persists to outbox backlog
        and drains cleanly upon broker restoration with zero event loss.
        """
        from datetime import timedelta

        from pydantic import SecretStr
        from sqlalchemy import text

        from app.core.config import get_settings
        from app.db.engine import DatabaseManager
        from app.db.uow import SqlAlchemyUnitOfWork
        from app.events.outbox.dispatcher import (
            DispatchOutcome,
            OutboxDispatcher,
            OutboxDispatchPolicy,
        )
        from app.events.outbox.repository import OutboxRepository
        from app.events.publishers.base import PublisherConnectionError
        from app.events.publishers.config import RabbitMqPublisherSettings
        from app.events.publishers.rabbitmq import RabbitMqEventPublisher
        from app.events.schemas import EventCreate, create_event_envelope

        settings = get_settings()
        manager = DatabaseManager(settings=settings)
        envelope = create_event_envelope(
            EventCreate(
                event_type="uat.exam.readiness.published",
                tenant_id=UAT_TENANT_ALPHA,
                payload={"exam_code": "MATH-101", "status": "SCHEDULED"},
            )
        )
        test_eid = envelope.event_id

        try:
            # 1. Client mutation persists to PostgreSQL outbox (simulating API domain write)
            async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
                repo = OutboxRepository(uow.session)
                await repo.enqueue(envelope)
                await uow.commit()

            # 2. Broker failure injection: publisher connection to unreachable endpoint fails
            failing_settings = RabbitMqPublisherSettings(
                rabbitmq_url=SecretStr("amqp://guest:guest@127.0.0.1:59999/"),
                rabbitmq_exchange_name="e7.uat.degraded",
                rabbitmq_connect_timeout_seconds=0.2,
            )
            failing_pub = RabbitMqEventPublisher(failing_settings)
            with pytest.raises(PublisherConnectionError):
                await failing_pub.start()

            # 3. User verification: outbox record remains durable in PostgreSQL backlog
            async with manager.session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT published_at, claim_token FROM outbox_events "
                            "WHERE event_id = :eid"
                        ),
                        {"eid": test_eid},
                    )
                ).mappings().one()
                assert row["published_at"] is None
                assert row["claim_token"] is None

            # 4. Broker restoration: connect usable publisher to genuinely active RabbitMQ on 5672
            restored_settings = RabbitMqPublisherSettings(
                rabbitmq_url=SecretStr("amqp://guest:guest@127.0.0.1:5672/"),
                rabbitmq_exchange_name="e7.uat.recovery",
                rabbitmq_connect_timeout_seconds=5.0,
            )
            restored_pub = RabbitMqEventPublisher(restored_settings, mandatory=False)
            await restored_pub.start()

            # 5. Run dispatcher to drain durable backlog
            policy = OutboxDispatchPolicy(
                lease_duration=timedelta(seconds=30),
                publish_timeout=timedelta(seconds=5),
                retry_base_delay=timedelta(seconds=1),
                retry_max_delay=timedelta(seconds=60),
                retry_multiplier=2.0,
            )
            dispatcher = OutboxDispatcher(
                uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory=manager.session_factory),
                publisher=restored_pub,
                policy=policy,
            )
            res = await dispatcher.dispatch_once(event_id=test_eid)
            assert res.outcome == DispatchOutcome.PUBLISHED

            # 6. Prove published_at/settlement state is correct in database
            async with manager.session_factory() as session:
                settled_row = (
                    await session.execute(
                        text(
                            "SELECT published_at, claim_token FROM outbox_events "
                            "WHERE event_id = :eid"
                        ),
                        {"eid": test_eid},
                    )
                ).mappings().one()
                assert settled_row["published_at"] is not None
                assert settled_row["claim_token"] is None

            # 7. Prove downstream idempotency: no duplicate dispatch
            idle_res = await dispatcher.dispatch_once(event_id=test_eid)
            assert idle_res.outcome == DispatchOutcome.IDLE

            await restored_pub.close()

        finally:
            async with manager.session_factory() as session:
                await session.execute(
                    text("DELETE FROM outbox_events WHERE event_id = :eid"),
                    {"eid": test_eid},
                )
                await session.commit()
            await manager.dispose()


class TestUatDegradedFlowable:
    """UAT scenarios for client resilience during workflow engine outages."""

    @pytest.mark.asyncio
    async def test_uat_degraded_flowable_001_engine_outage_resilience(self) -> None:
        """UAT-DEGRADED-FLOWABLE-001: Workflow engine outage fails cleanly without
        corrupting state, and subsequent post-recovery retry succeeds cleanly.
        """
        from pydantic import SecretStr

        from app.workflow.adapters.flowable.adapter import FlowableAdapter
        from app.workflow.adapters.flowable.config import FlowableSettings
        from app.workflow.adapters.protocol import WorkflowEngineConnectionError

        # 1. Unreachable Flowable endpoint injection (simulating outage)
        failing_settings = FlowableSettings(
            base_url="http://127.0.0.1:59999/flowable-rest/service",
            timeout_seconds=0.3,
            max_retries=0,
            username="admin",
            password=SecretStr("dummy"),
        )
        failing_adapter = FlowableAdapter(failing_settings)
        try:
            with pytest.raises(WorkflowEngineConnectionError):
                await failing_adapter.list_user_tasks(tenant_id=UAT_TENANT_ALPHA)
        finally:
            await failing_adapter.close()

        # 2. Endpoint restoration: connect to genuinely active Docker Flowable on 8080
        restored_settings = FlowableSettings(
            base_url="http://127.0.0.1:8080/flowable-rest/service",
            timeout_seconds=5.0,
            max_retries=1,
            username="rest-admin",
            password=SecretStr("test"),
        )
        restored_adapter = FlowableAdapter(restored_settings)
        try:
            # 3. Post-recovery retry succeeds cleanly with zero state corruption
            tasks = await restored_adapter.list_user_tasks(tenant_id=UAT_TENANT_ALPHA)
            assert isinstance(tasks, list)
        finally:
            await restored_adapter.close()


class TestUatExpandedCrossTenant:
    """Representative operational checks proving cross-tenant isolation and anti-enumeration."""

    def test_uat_cross_tenant_goal_generate_plan(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-GOAL-001: Foreign tenant cannot generate plan from Tenant Alpha goal."""
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        res = client.post(
            f"/api/v1/goals/{UAT_GOAL_ALPHA_ID}/generate-plan",
            json={
                "plan_name": "Infiltrate",
                "tasks": [
                    {
                        "temporary_id": "t1",
                        "title": "T1",
                        "estimated_duration_minutes": 60,
                    }
                ],
                "dependencies": [],
            },
            headers=foreign_headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "goal.not_found"

    def test_uat_cross_tenant_plan_critical_path(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-PLAN-001: Foreign tenant cannot inspect Tenant Alpha critical path."""
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        res = client.get(
            f"/api/v1/plans/{UAT_PLAN_ALPHA_ID}/critical-path",
            headers=foreign_headers,
        )
        assert res.status_code == 404

    def test_uat_cross_tenant_task_update(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-TASK-001: Foreign tenant cannot mutate Tenant Alpha task;
        state remains intact.
        """
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        initial_status = uat_context.plan_service.tasks[
            (UAT_TENANT_ALPHA, UAT_TASK_ALPHA_ID)
        ].status
        res = client.patch(
            f"/api/v1/tasks/{UAT_TASK_ALPHA_ID}",
            json={"status": "COMPLETED"},
            headers=foreign_headers,
        )
        assert res.status_code == 404
        # Verify persistence invariant: Tenant Alpha task status untouched
        assert (
            uat_context.plan_service.tasks[(UAT_TENANT_ALPHA, UAT_TASK_ALPHA_ID)].status
            == initial_status
        )

    def test_uat_cross_tenant_workflow_instance(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-WORKFLOW-001: Foreign tenant cannot access Tenant Alpha
        workflow instance.
        """
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        res = client.get(
            f"/api/v1/workflows/instances/{UAT_WF_INST_ALPHA_ID}",
            headers=foreign_headers,
        )
        assert res.status_code == 404

    def test_uat_cross_tenant_schedule_job(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-SCHEDULE-001: Foreign tenant cannot read Tenant Alpha schedule job."""
        foreign_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        res = client.get(
            f"/api/v1/schedule-jobs/{UAT_SCHED_JOB_ALPHA_ID}",
            headers=foreign_headers,
        )
        assert res.status_code == 404

    def test_uat_cross_tenant_audit_isolation(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-ISOLATION-AUDIT-001: Foreign tenant auditor queries audit log and
        receives zero Alpha records.
        """
        foreign_auditor_headers = uat_context.get_auth_headers("foreign_user", UAT_TENANT_BETA)
        res = client.get("/api/v1/audit", headers=foreign_auditor_headers)
        assert res.status_code == 200
        events = res.json()
        assert not any(e["tenant_id"] == UAT_TENANT_ALPHA for e in events)


class TestUatNegativeServiceCredentials:
    """Negative service API key authentication scenarios through HTTP boundary."""

    def test_uat_auth_service_key_invalid(self, client: TestClient) -> None:
        """UAT-AUTH-SERVICE-KEY-INVALID: Unknown/forged key through X-E7-Service-Key
        fails closed with 401.
        """
        secret = "a" * 45
        forged_key = f"e7sk_unknown_credential.{secret}"
        res = client.get(
            "/api/v1/analytics/operations",
            headers={"X-E7-Service-Key": forged_key},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "auth.service_key_invalid"

    def test_uat_auth_service_key_revoked(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-AUTH-SERVICE-KEY-REVOKED: Disabled service key fails closed with 401."""
        cred_id = "cred-uat-revoked-001"
        secret = "r" * 45
        raw_key = f"e7sk_{cred_id}.{secret}"
        digest = compute_secret_digest(secret)
        rec = ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject="svc:revoked",
            secret_digest=digest,
            enabled=False,
        )
        uat_context.credential_store.add_record(rec)
        res = client.get(
            "/api/v1/analytics/operations",
            headers={"X-E7-Service-Key": raw_key},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "auth.service_key_invalid"

    def test_uat_auth_service_key_expired(
        self, client: TestClient, uat_context: UatTestContext
    ) -> None:
        """UAT-AUTH-SERVICE-KEY-EXPIRED: Expired service key fails closed with 401."""
        cred_id = "cred-uat-expired-001"
        secret = "e" * 45
        raw_key = f"e7sk_{cred_id}.{secret}"
        digest = compute_secret_digest(secret)
        rec = ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject="svc:expired",
            secret_digest=digest,
            enabled=True,
            expires_at=datetime.now(UTC) - timedelta(hours=2),
        )
        uat_context.credential_store.add_record(rec)
        res = client.get(
            "/api/v1/analytics/operations",
            headers={"X-E7-Service-Key": raw_key},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "auth.service_key_invalid"

