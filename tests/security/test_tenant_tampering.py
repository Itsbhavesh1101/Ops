"""Payload & Query Tenant-Tampering Security Regression Suite for Phase 8A.

Validates that:
1. Authenticated principal / trusted tenant context is strictly authoritative.
2. Client-supplied tenant_id in payloads is either rejected (extra='forbid') or ignored.
3. Query parameter tampering (?tenant_id=...) does not alter execution scope.
4. Header tampering (e.g. X-Tenant-ID) cannot override trusted auth context.
5. Nested resources or relationship IDs belonging to another tenant fail safely (404).
"""


from starlette.testclient import TestClient

from tests.security.conftest import (
    GOAL_B_ID,
    RESOURCE_B_ID,
    TENANT_A,
    TENANT_B,
    SecurityTestContext,
    make_valid_schedule_problem,
)


class TestTenantTampering:
    """Verifies that untrusted client tenant inputs never override trusted context."""

    def test_payload_tenant_id_tampering_rejected_or_ignored(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Client attempting to specify tenant-beta while authenticated as tenant-alpha."""
        headers = security_context.get_auth_headers("planner", TENANT_A)

        # 1. Goal creation: GoalCreate forbids client tenant_id or enforces trusted tenant
        res_goal = client.post(
            "/api/v1/goals",
            json={"objective": "Tamper Goal", "scope": "Global", "tenant_id": TENANT_B},
            headers=headers,
        )
        if res_goal.status_code == 201:
            # If accepted, MUST be bound to TENANT_A, never TENANT_B
            assert res_goal.json()["tenant_id"] == TENANT_A
        else:
            # Or rejected via schema validation (extra='forbid')
            assert res_goal.status_code == 422

        # 2. Workflow instance creation: WorkflowInstanceStart forbids tenant_id
        res_wf = client.post(
            "/api/v1/workflows/instances",
            json={"workflow_key": "order-workflow-alpha", "tenant_id": TENANT_B},
            headers=headers,
        )
        if res_wf.status_code == 200:
            assert res_wf.json()["tenant_id"] == TENANT_A
        else:
            assert res_wf.status_code == 422

        # 3. Schedule job solve submission: ScheduleJobSubmitRequest forbids tenant_id
        tampered_solve = make_valid_schedule_problem("tamper-solve")
        tampered_solve["tenant_id"] = TENANT_B
        res_solve = client.post(
            "/api/v1/schedule-jobs",
            json=tampered_solve,
            headers=headers,
        )
        assert res_solve.status_code == 422

    def test_query_param_tenant_id_tampering_ignored(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Query parameter ?tenant_id=tenant-beta cannot expand or switch tenant context."""
        auditor_headers = security_context.get_auth_headers("auditor", TENANT_A)

        # 1. Audit search
        res_audit = client.get(
            f"/api/v1/audit?tenant_id={TENANT_B}",
            headers=auditor_headers,
        )
        assert res_audit.status_code == 200
        events = res_audit.json()
        assert len(events) >= 1
        for evt in events:
            assert evt["tenant_id"] == TENANT_A
            assert evt["tenant_id"] != TENANT_B

        # 2. Analytics
        res_analytics = client.get(
            f"/api/v1/analytics/operations?tenant_id={TENANT_B}",
            headers=auditor_headers,
        )
        assert res_analytics.status_code == 200
        data = res_analytics.json()
        assert data["tenant_id"] == TENANT_A
        assert data["tenant_id"] != TENANT_B

    def test_header_tenant_override_attempt_ignored(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Injecting X-Tenant-ID or Tenant-ID header cannot override verified JWT/Key context."""
        headers = security_context.get_auth_headers("auditor", TENANT_A)
        headers["X-Tenant-ID"] = TENANT_B
        headers["Tenant-ID"] = TENANT_B

        res = client.get(
            "/api/v1/analytics/operations",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["tenant_id"] == TENANT_A
        assert res.json()["tenant_id"] != TENANT_B

    def test_mixed_tenant_relationship_ids_rejected(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Tenant A cannot create a plan referencing a goal belonging to Tenant B."""
        headers = security_context.get_auth_headers("planner", TENANT_A)
        valid_plan_proposal = {
            "plan_name": "Tamper Relationship Plan",
            "tasks": [
                {
                    "temporary_id": "T1",
                    "title": "Task 1",
                    "effort_estimate": {"value": 60.0},
                }
            ],
        }
        res = client.post(
            f"/api/v1/goals/{GOAL_B_ID}/generate-plan",
            json=valid_plan_proposal,
            headers=headers,
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "goal.not_found"

    def test_nested_foreign_resource_identifiers_isolated(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Tenant A querying Tenant B resource cannot access Tenant B resource."""
        headers = security_context.get_auth_headers("scheduler", TENANT_A)
        res = client.get(
            f"/api/v1/resources/availability?resource_id={RESOURCE_B_ID}",
            headers=headers,
        )
        # Scoped strictly to Tenant A - no Tenant B resource returned
        if res.status_code == 200:
            intervals = res.json()
            assert not any(str(i.get("resource_id")) == str(RESOURCE_B_ID) for i in intervals)
        else:
            assert res.status_code == 404
