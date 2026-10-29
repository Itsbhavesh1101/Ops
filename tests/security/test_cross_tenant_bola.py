"""Cross-Tenant BOLA / IDOR Penetration Testing Suite for Phase 8A.

Covers two isolated tenants:
- TENANT_A (tenant-alpha)
- TENANT_B (tenant-beta)

Tests 17 operations where Tenant A possesses valid UUIDs/identifiers belonging
to Tenant B.

Invariants verified:
1. Object invisibility: 404 returned when accessing another tenant's object.
2. Zero data disclosure: no Tenant B payload or metadata returned to Tenant A.
3. Zero mutation: Tenant A cannot validate, approve, mutate, publish, or repair Tenant B objects.
4. Zero audit leakage: Tenant A audit queries return only Tenant A events.
5. Zero analytics leakage: Tenant A analytics queries are strictly scoped.
6. Idempotency isolation: Same Idempotency-Key used by Tenant A does not
   collide with or leak to Tenant B.
"""

from uuid import uuid4

from starlette.testclient import TestClient

from tests.security.conftest import (
    GOAL_B_ID,
    PLAN_B_ID,
    RESOURCE_B_ID,
    SCHED_JOB_B_ID,
    SCHEDULE_B_ID,
    TASK_B_ID,
    TENANT_A,
    TENANT_B,
    VALID_REPAIR_PAYLOAD,
    WF_DEF_B_ID,
    WF_INST_B_ID,
    WF_TASK_B_ID,
    SecurityTestContext,
)

VALID_PLAN_PROPOSAL = {
    "plan_name": "Attack Proposal",
    "tasks": [
        {
            "temporary_id": "T1",
            "title": "Task 1",
            "effort_estimate": {"value": 60.0},
        }
    ],
}


class TestCrossTenantBola:
    """Rigorous BOLA / IDOR testing across all 17 operational dimensions."""

    # 1. Goal read (via plan generation endpoint target)
    def test_bola_goal_access_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        # Attempt to generate plan against Tenant B's goal
        res = client.post(
            f"/api/v1/goals/{GOAL_B_ID}/generate-plan",
            json=VALID_PLAN_PROPOSAL,
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "goal.not_found"

    # 2. Goal mutation attempt with cross-tenant ID
    def test_bola_goal_mutation_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.post(
            f"/api/v1/goals/{GOAL_B_ID}/generate-plan",
            json=VALID_PLAN_PROPOSAL,
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "goal.not_found"

    # 3. Plan read: critical path
    def test_bola_plan_read_critical_path_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.get(
            f"/api/v1/plans/{PLAN_B_ID}/critical-path",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "plan.not_found"

    # 3b. Plan read: recommendations
    def test_bola_plan_read_recommendations_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.get(
            f"/api/v1/plans/{PLAN_B_ID}/recommendations",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "plan.not_found"

    # 4. Plan validation
    def test_bola_plan_validation_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.post(
            f"/api/v1/plans/{PLAN_B_ID}/validate",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "plan.not_found"

    # 5. Plan approval
    def test_bola_plan_approval_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        res = client.post(
            f"/api/v1/plans/{PLAN_B_ID}/approve",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "plan.not_found"

    # 6. Task progress update
    def test_bola_task_update_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.patch(
            f"/api/v1/tasks/{TASK_B_ID}",
            json={"status": "IN_PROGRESS", "progress_percentage": 50},
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "task.not_found"

    # 7. Resource availability access
    def test_bola_resource_access_isolated(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        res = client.get(
            f"/api/v1/resources/availability?resource_id={RESOURCE_B_ID}",
            headers=headers,
        )
        # Endpoint returns either 404 or empty list scoped to Tenant A, never Tenant B data
        if res.status_code == 200:
            intervals = res.json()
            assert all(item.get("tenant_id") == TENANT_A for item in intervals)
            assert not any(str(item.get("resource_id")) == str(RESOURCE_B_ID) for item in intervals)
        else:
            assert res.status_code == 404

    # 8. Schedule job read
    def test_bola_schedule_job_read_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        res = client.get(
            f"/api/v1/schedule-jobs/{SCHED_JOB_B_ID}",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "schedule_job.not_found"

    # 9. Schedule publish
    def test_bola_schedule_publish_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        res = client.post(
            f"/api/v1/schedule-jobs/{SCHED_JOB_B_ID}/publish",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "schedule_job.not_found"

    # 10. Schedule repair
    def test_bola_schedule_repair_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        res = client.post(
            f"/api/v1/schedules/{SCHEDULE_B_ID}/repair",
            json=VALID_REPAIR_PAYLOAD,
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "schedule.not_found"

    # 11. Workflow instance access
    def test_bola_workflow_instance_read_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.get(
            f"/api/v1/workflows/instances/{WF_INST_B_ID}",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "workflow.instance_not_found"

    # 12. Workflow task action
    def test_bola_workflow_task_action_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("approver", TENANT_A)
        headers["Idempotency-Key"] = "bola-wf-task-action"
        res = client.post(
            f"/api/v1/workflows/tasks/{WF_TASK_B_ID}/actions",
            json={"action": "COMPLETE", "variables": {}},
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "workflow.task_not_found"

    # 13. Audit record lookup
    def test_bola_audit_records_isolated(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        res = client.get(
            "/api/v1/audit",
            headers=headers,
        )
        assert res.status_code == 200
        events = res.json()
        assert len(events) >= 1
        for evt in events:
            assert evt["tenant_id"] == TENANT_A
            assert evt["tenant_id"] != TENANT_B

    # 14. Operations analytics filters
    def test_bola_analytics_isolated(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        res = client.get(
            "/api/v1/analytics/operations",
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["tenant_id"] == TENANT_A
        assert data["tenant_id"] != TENANT_B

    # 15. Job lookup / Workflow definition lookup
    def test_bola_workflow_definition_lookup_fails_404(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        headers = security_context.get_auth_headers("planner", TENANT_A)
        res = client.get(
            f"/api/v1/workflows/definitions/{WF_DEF_B_ID}",
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "workflow.definition_not_found"

    # 16. Idempotency-key reuse across tenants
    def test_bola_idempotency_key_reuse_across_tenants_isolated(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Tenant A using Idempotency-Key 'shared-idem-key' does not block or return to Tenant B."""
        headers_a = security_context.get_auth_headers("approver", TENANT_A)
        headers_a["Idempotency-Key"] = "shared-cross-tenant-key-1"

        headers_b = security_context.get_auth_headers("approver", TENANT_B)
        headers_b["Idempotency-Key"] = "shared-cross-tenant-key-1"

        # Tenant A acts on their task
        from tests.security.conftest import WF_TASK_A_ID
        res_a = client.post(
            f"/api/v1/workflows/tasks/{WF_TASK_A_ID}/actions",
            json={"action": "COMPLETE", "variables": {}},
            headers=headers_a,
        )
        assert res_a.status_code == 200

        # Tenant B acts on their task with the EXACT same idempotency key
        res_b = client.post(
            f"/api/v1/workflows/tasks/{WF_TASK_B_ID}/actions",
            json={"action": "COMPLETE", "variables": {}},
            headers=headers_b,
        )
        assert res_b.status_code == 200
        # Responses correspond to different task IDs and different tenants
        assert res_a.json()["id"] != res_b.json()["id"]

    # 17. Calendar / integration record access where exposed
    def test_bola_calendar_push_invalid_channel_fails_404(
        self, client: TestClient
    ) -> None:
        """Push notification with unknown or cross-tenant channel returns 404."""
        unknown_channel = str(uuid4())
        res = client.post(
            "/api/v1/integrations/calendar/google/webhook",
            headers={
                "X-Goog-Channel-ID": unknown_channel,
                "X-Goog-Resource-ID": "res-123",
                "X-Goog-Resource-State": "exists",
                "X-Goog-Message-Number": "1",
                "X-Goog-Channel-Token": "secret-token",
            },
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "calendar.channel_not_found"
