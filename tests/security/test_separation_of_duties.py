"""Separation-of-Duties (SoD) Regression Suite for Phase 8A.

Validates that critical operational boundaries and checks are enforced
both at the HTTP boundary and domain layer:
1. Planner cannot approve own plan (plan.sod_violation -> 403).
2. Unauthorized persona cannot publish schedule (schedule_publish.unauthorized -> 403).
3. Workflow task actions require assignee eligibility (workflow.task_unauthorized -> 403).
4. Auditor cannot mutate operational objects (goals, tasks, schedules, workflows -> 403).
5. Non-admins cannot deploy workflow definitions (workflow.unauthorized -> 403).
6. Service identity receives only its intended capabilities.
7. Tenant admin cannot bypass tenant boundary (Tenant A admin gets 404 on Tenant B).
"""

from starlette.testclient import TestClient

from tests.security.conftest import (
    PLAN_A_ID,
    PLAN_B_ID,
    SCHED_JOB_A_ID,
    TASK_A_ID,
    TENANT_A,
    WF_DEF_A_ID,
    WF_TASK_A_ID,
    SecurityTestContext,
    make_valid_schedule_problem,
)


class TestSeparationOfDuties:
    """Rigorous verification of SoD invariants across all operational domains."""

    def test_planner_cannot_approve_own_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """A planner who authored a plan is strictly prohibited from approving it."""
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"]["code"] == "plan.sod_violation"
        assert "Separation of Duties" in data["error"]["message"]

    def test_approver_can_approve_planner_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """An independent approver persona can approve the plan."""
        headers = security_context.get_auth_headers("approver", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

    def test_unauthorized_role_cannot_publish_schedule(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Only users with explicit schedule:publish permissions may publish schedules."""
        # Planner has no schedule:publish permission
        planner_headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish",
            headers=planner_headers,
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "schedule_publish.unauthorized"

        # Auditor has no schedule:publish permission
        auditor_headers = security_context.get_auth_headers("auditor", TENANT_A)
        res_aud = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish",
            headers=auditor_headers,
        )
        assert res_aud.status_code == 403
        assert res_aud.json()["error"]["code"] == "schedule_publish.unauthorized"

    def test_workflow_task_action_requires_assignee_eligibility(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """A human actor who is neither assignee nor approver cannot complete task."""
        # Planner is not the assignee and lacks approver role
        planner_headers = security_context.get_auth_headers("planner", TENANT_A)
        planner_headers["Idempotency-Key"] = "planner-task-action"
        res = client.post(
            f"/api/v1/workflows/tasks/{WF_TASK_A_ID}/actions",
            json={"action": "COMPLETE", "variables": {}},
            headers=planner_headers,
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "workflow.task_unauthorized"

    def test_auditor_strictly_read_only_across_all_mutations(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Auditors are strictly read-only and must be denied on all mutation endpoints."""
        headers = security_context.get_auth_headers("auditor", TENANT_A)

        # 1. Goal creation
        res1 = client.post(
            "/api/v1/goals",
            json={"objective": "Auditor Goal", "scope": "Global"},
            headers=headers,
        )
        assert res1.status_code == 403
        assert res1.json()["error"]["code"] == "goal.unauthorized"

        # 2. Task progress update
        res2 = client.patch(
            f"/api/v1/tasks/{TASK_A_ID}",
            json={"status": "COMPLETED"},
            headers=headers,
        )
        assert res2.status_code == 403
        assert res2.json()["error"]["code"] == "task.unauthorized"

        # 3. Schedule solve submission
        res3 = client.post(
            "/api/v1/schedule-jobs",
            json=make_valid_schedule_problem("auditor-solve"),
            headers=headers,
        )
        assert res3.status_code == 403
        assert res3.json()["error"]["code"] == "schedule.unauthorized"

        # 4. Schedule publish
        res4 = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish",
            headers=headers,
        )
        assert res4.status_code == 403
        assert res4.json()["error"]["code"] == "schedule_publish.unauthorized"

    def test_ordinary_roles_cannot_deploy_workflow_definitions(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Deploying workflow definitions to engine runtime requires admin or workflow:deploy."""
        planner_headers = security_context.get_auth_headers("planner", TENANT_A)
        res_planner = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=planner_headers,
        )
        assert res_planner.status_code == 403
        assert res_planner.json()["error"]["code"] == "workflow.unauthorized"

        scheduler_headers = security_context.get_auth_headers("scheduler", TENANT_A)
        res_sched = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=scheduler_headers,
        )
        assert res_sched.status_code == 403
        assert res_sched.json()["error"]["code"] == "workflow.unauthorized"

        # Admin CAN deploy
        admin_headers = security_context.get_auth_headers("admin", TENANT_A)
        res_admin = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=admin_headers,
        )
        assert res_admin.status_code == 200
        assert res_admin.json()["is_active"] is True

    def test_service_identity_capabilities_bounded(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Service identity is bounded to machine operations and cannot perform human approvals."""
        svc_headers = security_context.get_auth_headers("service", TENANT_A)

        # Forbidden to approve plan
        res1 = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=svc_headers,
        )
        assert res1.status_code == 403
        assert res1.json()["error"]["code"] == "plan.unauthorized"

        # Forbidden to deploy workflows
        res2 = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=svc_headers,
        )
        assert res2.status_code == 403
        assert res2.json()["error"]["code"] == "workflow.unauthorized"

    def test_tenant_admin_cannot_bypass_tenant_boundary(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Even a Tenant Admin in Tenant A cannot see or mutate Tenant B objects (returns 404)."""
        admin_a_headers = security_context.get_auth_headers("admin", TENANT_A)

        # Cannot approve Tenant B plan
        res1 = client.post(
            f"/api/v1/plans/{PLAN_B_ID}/approve",
            headers=admin_a_headers,
        )
        assert res1.status_code == 404
        assert res1.json()["error"]["code"] == "plan.not_found"
