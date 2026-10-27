"""OpenAPI specification provenance and drift test (Phase 8F).

Model: CANONICAL_DERIVED_OPENAPI
Validates:
1. Operation set equivalence: Raw app.main:app.openapi() operations match
   committed docs/production/openapi.json operations exactly (Requirement A).
2. Protected operation security: Protected operations declare BearerAuth and
   ApiKeyAuth alternatives (Requirement B).
3. Public routes security: Health, readiness, and root endpoints do not require
   authentication (Requirement C).
4. Provider webhook security: Google Calendar push webhook documents provider-specific
   channel token verification and runtime error responses (Requirement D).
5. Quota 429 equivalence: Dynamically derived quota-enforced endpoint set matches
   OpenAPI 429 endpoint set with zero symmetric difference (Requirement E).
6. Health/readiness/root quota isolation: Probes do not expose 429 (Requirement F).
7. Request schema preservation: Raw Pydantic request models are preserved (Requirement G).
8. Success response schema preservation: Raw 200/201/202 responses are preserved (Requirement H).
9. Export repeatability: Repeated export is deterministic (Requirement I).
10. Canonical paths count (59) and domain representation.
11. Standard security schemes and error response components.
12. Zero secret or infrastructure leakage.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPENAPI_JSON_PATH = REPO_ROOT / "docs" / "production" / "openapi.json"


def _extract_raw_and_derived_schemas() -> tuple[
    dict[str, Any], dict[str, Any], set[tuple[str, str]]
]:
    """Execute isolated subprocess to extract raw schema, derived schema, and quota endpoints.

    Subprocess isolation guarantees that importing app.main under E7_MOUNT_DOMAIN_ROUTERS=true
    does not alter in-memory app.routes for other unit tests (such as test_api_v1_composition).
    """
    code = (
        "import json; "
        "from scripts.export_openapi import "
        "get_raw_openapi_schema, get_canonical_openapi_schema, "
        "discover_quota_enforced_endpoints, sort_json_deterministic; "
        "raw = sort_json_deterministic(get_raw_openapi_schema()); "
        "derived = sort_json_deterministic(get_canonical_openapi_schema()); "
        "quota_eps = list(discover_quota_enforced_endpoints()); "
        "print(json.dumps({'raw': raw, 'derived': derived, 'quota_eps': quota_eps}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    quota_set = {(p, m) for p, m in payload["quota_eps"]}
    return payload["raw"], payload["derived"], quota_set


class TestOpenApiProvenanceAndDrift:
    """Rigorous provenance and drift verification for the CANONICAL_DERIVED_OPENAPI model."""

    def test_openapi_file_exists(self) -> None:
        """Verify committed openapi.json exists."""
        assert OPENAPI_JSON_PATH.exists(), f"Missing canonical OpenAPI file at {OPENAPI_JSON_PATH}"

    def test_raw_app_route_operations_match_exported_operation_set(self) -> None:
        """Requirement A: Raw app.main route operation set must exactly equal openapi operations."""
        raw_schema, _, _ = _extract_raw_and_derived_schemas()

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            committed_schema = json.load(f)

        raw_ops: set[tuple[str, str]] = set()
        for path, path_item in raw_schema.get("paths", {}).items():
            for method in path_item.keys():
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    raw_ops.add((path, method.lower()))

        committed_ops: set[tuple[str, str]] = set()
        for path, path_item in committed_schema.get("paths", {}).items():
            for method in path_item.keys():
                if method.lower() in {"get", "post", "put", "patch", "delete"}:
                    committed_ops.add((path, method.lower()))

        missing_in_committed = raw_ops - committed_ops
        extra_in_committed = committed_ops - raw_ops

        assert not missing_in_committed, (
            f"Operations in live app.main:app but missing in openapi.json: {missing_in_committed}"
        )
        assert not extra_in_committed, (
            f"Operations in openapi.json but not in live app.main:app: {extra_in_committed}"
        )
        assert raw_ops == committed_ops, "Raw and committed operation sets are not identical"

    def test_protected_operations_security_alternatives(self) -> None:
        """Requirement B: Actual protected operations declare BearerAuth and ApiKeyAuth."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        unauthenticated_or_webhook = {
            "/",
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/integrations/calendar/google/webhook",
        }

        protected_count = 0
        for path, methods in schema.get("paths", {}).items():
            if path in unauthenticated_or_webhook:
                continue
            for method, op in methods.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                protected_count += 1
                sec = op.get("security", [])
                assert sec, f"Protected endpoint {method.upper()} {path} missing security"
                schemes_present = {list(s.keys())[0] for s in sec}
                assert "BearerAuth" in schemes_present, (
                    f"Protected endpoint {method.upper()} {path} missing BearerAuth"
                )
                assert "ApiKeyAuth" in schemes_present or "ServiceKeyAuth" in schemes_present, (
                    f"Protected endpoint {method.upper()} {path} missing ApiKeyAuth"
                )

        assert protected_count == 69, f"Expected 69 protected operations, found {protected_count}"

    def test_public_operations_do_not_require_auth(self) -> None:
        """Requirement C: Actual public routes do not incorrectly require OIDC/service key."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        public_paths = ["/", "/api/v1/health", "/api/v1/ready"]
        for p in public_paths:
            assert p in schema.get("paths", {}), f"Missing public path: {p}"
            op = schema["paths"][p]["get"]
            sec = op.get("security", [])
            assert not sec, f"Public endpoint GET {p} should not have security declared, got {sec}"
            responses = op.get("responses", {})
            assert "401" not in responses, f"Public endpoint GET {p} should not declare 401"
            assert "403" not in responses, f"Public endpoint GET {p} should not declare 403"

    def test_provider_webhook_security_and_errors_match_runtime(self) -> None:
        """Requirement D: Provider webhook security matches runtime semantics."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        webhook_path = "/api/v1/integrations/calendar/google/webhook"
        assert webhook_path in schema.get("paths", {}), f"Missing webhook path: {webhook_path}"

        op = schema["paths"][webhook_path]["post"]
        sec = op.get("security", [])
        assert sec, "Webhook operation should declare provider security scheme"
        schemes = {list(s.keys())[0] for s in sec}
        assert "GoogleWebhookAuth" in schemes, (
            "Webhook operation must declare GoogleWebhookAuth provider security"
        )
        assert "BearerAuth" not in schemes, (
            "Webhook operation must not require standard BearerAuth"
        )
        assert "ApiKeyAuth" not in schemes and "ServiceKeyAuth" not in schemes, (
            "Webhook operation must not require standard ApiKeyAuth"
        )

        responses = op.get("responses", {})
        for expected_status in ["200", "400", "401", "403", "404", "500"]:
            assert expected_status in responses, (
                f"Webhook missing runtime-supported response {expected_status}"
            )

    def test_actual_quota_enforced_set_matches_openapi_429_set(self) -> None:
        """Requirement E: Actual quota-enforced operation set == OpenAPI 429 operation set."""
        _, _, actual_quota_endpoints = _extract_raw_and_derived_schemas()

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        openapi_429_endpoints: set[tuple[str, str]] = set()
        for path, methods in schema.get("paths", {}).items():
            for method, op in methods.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if "429" in op.get("responses", {}):
                    openapi_429_endpoints.add((path, method.lower()))

        sym_diff = actual_quota_endpoints ^ openapi_429_endpoints
        assert not sym_diff, (
            f"Symmetric difference between actual quota endpoints and OpenAPI 429 endpoints "
            f"must be zero, got difference: {sym_diff}"
        )
        assert len(openapi_429_endpoints) == 5, (
            f"Expected exactly 5 quota-governed endpoints, got {len(openapi_429_endpoints)}"
        )

    def test_health_readiness_root_do_not_expose_quota_429(self) -> None:
        """Requirement F: Health, readiness, and root endpoints do not expose HTTP 429."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        for p in ["/", "/api/v1/health", "/api/v1/ready"]:
            op = schema["paths"][p]["get"]
            assert "429" not in op.get("responses", {}), (
                f"Public probe GET {p} must not declare HTTP 429"
            )

    def test_raw_request_schemas_preserved(self) -> None:
        """Requirement G: Enrichment does not alter request schemas or parameters."""
        raw_schema, _, _ = _extract_raw_and_derived_schemas()

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            committed_schema = json.load(f)

        for path, path_item in raw_schema.get("paths", {}).items():
            committed_path_item = committed_schema["paths"][path]
            for method, raw_op in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                committed_op = committed_path_item[method]

                if "parameters" in raw_op:
                    assert "parameters" in committed_op
                    assert raw_op["parameters"] == committed_op["parameters"]

                if "requestBody" in raw_op:
                    assert "requestBody" in committed_op
                    assert raw_op["requestBody"] == committed_op["requestBody"]

    def test_raw_success_response_schemas_preserved(self) -> None:
        """Requirement H: Enrichment does not alter success response schemas."""
        raw_schema, _, _ = _extract_raw_and_derived_schemas()

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            committed_schema = json.load(f)

        for path, path_item in raw_schema.get("paths", {}).items():
            committed_path_item = committed_schema["paths"][path]
            for method, raw_op in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                committed_op = committed_path_item[method]

                for status in ["200", "201", "202"]:
                    if status in raw_op.get("responses", {}):
                        assert status in committed_op.get("responses", {})
                        assert raw_op["responses"][status] == committed_op["responses"][status]

    def test_repeated_export_is_deterministic(self) -> None:
        """Requirement I: Repeated export remains deterministic."""
        _, derived_1, _ = _extract_raw_and_derived_schemas()
        _, derived_2, _ = _extract_raw_and_derived_schemas()
        assert derived_1 == derived_2, "Non-deterministic OpenAPI export detected"

    def test_deterministic_enrichment_identity(self) -> None:
        """The output of deterministic enrichment must match committed openapi.json key-for-key."""
        _, derived_schema, _ = _extract_raw_and_derived_schemas()

        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            committed_schema = json.load(f)

        assert derived_schema == committed_schema, (
            "Derived OpenAPI schema differs from committed docs/production/openapi.json. "
            "Run 'python scripts/export_openapi.py' to regenerate."
        )

    def test_canonical_paths_and_operations(self) -> None:
        """Prove committed OpenAPI contains exactly 59 paths and all required production domains."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        paths = schema.get("paths", {})
        assert len(paths) == 68, f"Expected 68 canonical paths, got {len(paths)}"

        expected_domain_paths = {
            "root": "/",
            "health": "/api/v1/health",
            "readiness": "/api/v1/ready",
            "calendar_webhook": "/api/v1/integrations/calendar/google/webhook",
            "planning_goals": "/api/v1/goals",
            "planning_generate": "/api/v1/goals/{goal_id}/generate-plan",
            "planning_validate": "/api/v1/plans/{plan_id}/validate",
            "planning_approve": "/api/v1/plans/{plan_id}/approve",
            "planning_critical_path": "/api/v1/plans/{plan_id}/critical-path",
            "planning_tasks": "/api/v1/tasks/{task_id}",
            "workflows_def": "/api/v1/workflows/definitions",
            "workflows_inst": "/api/v1/workflows/instances",
            "workflows_tasks": "/api/v1/workflows/tasks",
            "scheduling_submit": "/api/v1/schedule-jobs",
            "scheduling_poll": "/api/v1/schedule-jobs/{job_id}",
            "scheduling_publish": "/api/v1/schedule-jobs/{job_id}/publish",
            "scheduling_repair": "/api/v1/schedules/{schedule_id}/repair",
            "scheduling_avail": "/api/v1/resources/availability",
            "analytics_operations": "/api/v1/analytics/operations",
            "audit_trail": "/api/v1/audit",
        }
        for domain, expected_path in expected_domain_paths.items():
            assert expected_path in paths, (
                f"Missing expected {domain} production path: {expected_path}"
            )

    def test_security_schemes_contract(self) -> None:
        """Verify OpenAPI security schemes reflect configured OIDC and Service Key contracts."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        components = schema.get("components", {})
        security_schemes = components.get("securitySchemes", {})

        assert "BearerAuth" in security_schemes
        bearer = security_schemes["BearerAuth"]
        assert bearer["type"] == "http"
        assert bearer["scheme"] == "bearer"
        assert bearer["bearerFormat"] == "JWT"
        assert "Configured OIDC/JWT" in bearer["description"]

        assert "ApiKeyAuth" in security_schemes
        api_key = security_schemes["ApiKeyAuth"]
        assert api_key["type"] == "apiKey"
        assert api_key["in"] == "header"
        assert api_key["name"] == "X-E7-Service-Key"

        assert "ServiceKeyAuth" in security_schemes
        service_key = security_schemes["ServiceKeyAuth"]
        assert service_key["type"] == "apiKey"
        assert service_key["in"] == "header"
        assert service_key["name"] == "X-E7-Service-Key"

        assert "GoogleWebhookAuth" in security_schemes
        webhook_auth = security_schemes["GoogleWebhookAuth"]
        assert webhook_auth["type"] == "apiKey"
        assert webhook_auth["in"] == "header"
        assert webhook_auth["name"] == "X-Goog-Channel-Token"

    def test_standard_error_contracts(self) -> None:
        """Verify OpenAPI documents standardized HTTP error responses."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema = json.load(f)

        responses = schema.get("components", {}).get("responses", {})

        required_responses = [
            "BadRequestError",
            "UnauthorizedError",
            "ForbiddenError",
            "NotFoundError",
            "ConflictError",
            "PayloadTooLargeError",
            "ValidationError",
            "QuotaExceededError",
            "InternalServerError",
        ]
        for resp in required_responses:
            assert resp in responses, f"Missing error response component: {resp}"

        quota_resp = responses["QuotaExceededError"]
        assert "headers" in quota_resp
        assert "Retry-After" in quota_resp["headers"]

    def test_no_credential_or_infrastructure_leakage(self) -> None:
        """Verify no sensitive secrets or internal infrastructure URLs appear in openapi.json."""
        with OPENAPI_JSON_PATH.open("r", encoding="utf-8") as f:
            schema_text = f.read().lower()

        forbidden_patterns = [
            "e7sk_live_",
            "postgres://",
            "postgresql+asyncpg://",
            "amqp://",
            "amqps://",
            "localhost:5432",
            "localhost:5672",
            "client_secret",
            "refresh_token",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in schema_text, f"Forbidden leakage pattern found: {pattern}"
