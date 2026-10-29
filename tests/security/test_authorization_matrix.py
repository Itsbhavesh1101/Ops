"""Executable authorization matrix tests for Phase 8A.

Covers all 7 canonical personas across actual implemented operations:
- Personas:
  1. Planner
  2. Approver
  3. Scheduler / Operator
  4. Auditor
  5. Tenant Admin
  6. Service Identity
  7. Unauthenticated Caller

- Operations:
  - goals: POST /api/v1/goals
  - plans: POST /api/v1/plans/{plan_id}/validate
  - plans: POST /api/v1/plans/{plan_id}/approve
  - plans: GET /api/v1/plans/{plan_id}/critical-path
  - tasks: PATCH /api/v1/tasks/{task_id}
  - resources: GET /api/v1/resources/availability
  - schedules: POST /api/v1/schedule-jobs
  - schedules: GET /api/v1/schedule-jobs/{job_id}
  - schedules: POST /api/v1/schedule-jobs/{job_id}/publish
  - schedules: POST /api/v1/schedules/{schedule_id}/repair
  - workflow definitions: POST /api/v1/workflows/definitions
  - workflow definitions: POST /api/v1/workflows/definitions/{id}/deploy
  - workflow definitions: GET /api/v1/workflows/definitions/{id}
  - workflow instances: POST /api/v1/workflows/instances
  - workflow instances: GET /api/v1/workflows/instances/{id}
  - workflow task actions: POST /api/v1/workflows/tasks/{task_id}/actions
  - audit search: GET /api/v1/audit
  - operations analytics: GET /api/v1/analytics/operations
"""

import pytest
from starlette.testclient import TestClient

from tests.security.conftest import (
    PLAN_A_ID,
    SCHED_JOB_A_ID,
    SCHEDULE_A_ID,
    TASK_A_ID,
    TENANT_A,
    WF_DEF_A_ID,
    WF_INST_A_ID,
    WF_TASK_A_ID,
    SecurityTestContext,
    make_valid_schedule_problem,
)

VALID_REPAIR_PAYLOAD = {
    "disrupted_resource_ids": ["tech-1"],
    "affected_activity_ids": ["act-1"],
    "reason": "resource.unavailable",
    "movement_weight": 1.0,
}


class TestAuthorizationMatrix:
    """Verifies ALLOW, DENY (403), or 401 across all personas and operations."""

    # -------------------------------------------------------------------------
    # 1. Unauthenticated Caller: ALL protected routes must return 401
    # -------------------------------------------------------------------------
    @pytest.mark.parametrize(
        ("method", "path", "payload", "extra_headers"),
        [
            ("post", "/api/v1/goals", {"objective": "Test", "scope": "Test"}, None),
            ("post", f"/api/v1/plans/{PLAN_A_ID}/validate", {}, None),
            ("post", f"/api/v1/plans/{PLAN_A_ID}/approve", {}, None),
            ("get", f"/api/v1/plans/{PLAN_A_ID}/critical-path", None, None),
            ("get", f"/api/v1/plans/{PLAN_A_ID}/recommendations", None, None),
            ("patch", f"/api/v1/tasks/{TASK_A_ID}", {"status": "COMPLETED"}, None),
            ("get", "/api/v1/resources/availability", None, None),
            ("post", "/api/v1/schedule-jobs", make_valid_schedule_problem(), None),
            ("get", f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}", None, None),
            ("post", f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish", {}, None),
            (
                "post",
                f"/api/v1/schedules/{SCHEDULE_A_ID}/repair",
                VALID_REPAIR_PAYLOAD,
                None,
            ),
            (
                "post",
                "/api/v1/workflows/definitions",
                {
                    "workflow_key": "wf-new",
                    "name": "New WF",
                    "bpmn_xml": "<definitions>BPMN</definitions>",
                },
                None,
            ),
            ("post", f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy", {}, None),
            ("get", f"/api/v1/workflows/definitions/{WF_DEF_A_ID}", None, None),
            ("post", "/api/v1/workflows/instances", {"workflow_key": "order-workflow-alpha"}, None),
            ("get", f"/api/v1/workflows/instances/{WF_INST_A_ID}", None, None),
            (
                "post",
                f"/api/v1/workflows/tasks/{WF_TASK_A_ID}/actions",
                {"action": "COMPLETE", "variables": {}},
                {"Idempotency-Key": "unauth-key-1"},
            ),
            ("get", "/api/v1/audit", None, None),
            ("get", "/api/v1/analytics/operations", None, None),
        ],
    )
    def test_unauthenticated_rejected_401(
        self,
        client: TestClient,
        method: str,
        path: str,
        payload: dict | None,
        extra_headers: dict | None,
    ) -> None:
        headers = extra_headers or {}
        if method == "get":
            response = client.get(path, headers=headers)
        elif method == "post":
            response = client.post(path, json=payload, headers=headers)
        elif method == "patch":
            response = client.patch(path, json=payload, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        assert response.status_code == 401
        assert response.json()["error"]["code"] in {
            "auth.credentials_missing",
            "auth.missing_credentials",
            "auth.unauthenticated",
        }

    # -------------------------------------------------------------------------
    # 2. Planner Persona
    # -------------------------------------------------------------------------
    def test_planner_can_create_goal(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            "/api/v1/goals",
            json={"objective": "Q4 Logistics Expansion", "scope": "EMEA"},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["objective"] == "Q4 Logistics Expansion"

    def test_planner_can_validate_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/validate",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True

    def test_planner_can_view_recommendations(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.get(
            f"/api/v1/plans/{PLAN_A_ID}/recommendations",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["plan_id"] == str(PLAN_A_ID)

    def test_planner_cannot_approve_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "plan.sod_violation"

    def test_planner_cannot_deploy_workflow(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "workflow.unauthorized"

    def test_planner_cannot_publish_schedule(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        response = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "schedule_publish.unauthorized"

    # -------------------------------------------------------------------------
    # 3. Approver Persona
    # -------------------------------------------------------------------------
    def test_approver_can_approve_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"
        assert response.json()["is_baseline"] is True

    def test_approver_can_publish_schedule(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        response = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_A_ID}/publish",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PUBLISHED"

    def test_approver_can_complete_assigned_workflow_task(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        headers["Idempotency-Key"] = "approver-action-1"
        response = client.post(
            f"/api/v1/workflows/tasks/{WF_TASK_A_ID}/actions",
            json={"action": "COMPLETE", "variables": {"approved": True}},
            headers=headers,
        )
        assert response.status_code == 200

    # -------------------------------------------------------------------------
    # 4. Scheduler / Operator Persona
    # -------------------------------------------------------------------------
    def test_scheduler_can_submit_and_repair_schedule(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        # Solve submit
        solve_res = client.post(
            "/api/v1/schedule-jobs",
            json=make_valid_schedule_problem("q4-ops"),
            headers=headers,
        )
        assert solve_res.status_code == 202
        assert solve_res.json()["status"] == "SUBMITTED"

        # Repair
        repair_res = client.post(
            f"/api/v1/schedules/{SCHEDULE_A_ID}/repair",
            json=VALID_REPAIR_PAYLOAD,
            headers=headers,
        )
        assert repair_res.status_code == 200
        assert repair_res.json()["status"] == "REPAIRED"

    def test_scheduler_cannot_approve_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "plan.unauthorized"

    # -------------------------------------------------------------------------
    # 5. Auditor Persona (Strict Read-Only)
    # -------------------------------------------------------------------------
    def test_auditor_can_read_audit_and_analytics(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        # Audit
        audit_res = client.get("/api/v1/audit", headers=headers)
        assert audit_res.status_code == 200
        assert len(audit_res.json()) >= 1

        # Analytics
        analytics_res = client.get("/api/v1/analytics/operations", headers=headers)
        assert analytics_res.status_code == 200
        assert analytics_res.json()["tenant_id"] == TENANT_A

    def test_auditor_cannot_mutate_goals(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        response = client.post(
            "/api/v1/goals",
            json={"objective": "Auditor Attempt", "scope": "Forbidden"},
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "goal.unauthorized"

    def test_auditor_cannot_update_task_progress(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        response = client.patch(
            f"/api/v1/tasks/{TASK_A_ID}",
            json={"status": "COMPLETED"},
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "task.unauthorized"

    def test_auditor_cannot_submit_or_repair_schedule(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        # Submit
        sub_res = client.post(
            "/api/v1/schedule-jobs",
            json=make_valid_schedule_problem("auditor-job"),
            headers=headers,
        )
        assert sub_res.status_code == 403
        assert sub_res.json()["error"]["code"] == "schedule.unauthorized"

        # Repair
        rep_res = client.post(
            f"/api/v1/schedules/{SCHEDULE_A_ID}/repair",
            json=VALID_REPAIR_PAYLOAD,
            headers=headers,
        )
        assert rep_res.status_code == 403
        assert rep_res.json()["error"]["code"] == "schedule.unauthorized"

    # -------------------------------------------------------------------------
    # 6. Tenant Admin Persona
    # -------------------------------------------------------------------------
    def test_tenant_admin_can_deploy_workflow(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("admin", TENANT_A)
        response = client.post(
            f"/api/v1/workflows/definitions/{WF_DEF_A_ID}/deploy",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["deployment_id"] is not None
        assert response.json()["is_active"] is True

    def test_tenant_admin_can_approve_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("admin", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

    # -------------------------------------------------------------------------
    # 7. Service Identity Persona
    # -------------------------------------------------------------------------
    def test_service_identity_can_read_resources(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("service", TENANT_A)
        response = client.get(
            "/api/v1/resources/availability",
            headers=headers,
        )
        assert response.status_code == 200

    def test_service_identity_cannot_approve_plan(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("service", TENANT_A)
        response = client.post(
            f"/api/v1/plans/{PLAN_A_ID}/approve",
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "plan.unauthorized"
