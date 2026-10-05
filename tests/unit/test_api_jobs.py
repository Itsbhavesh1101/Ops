"""Unit tests for the HTTP asynchronous job read/polling API contract.

Part of Phase 1 Foundation Task 1I.4C:
- JobReadResponse & JobFailureResponse: immutable public schema, strict privacy minimization.
- job_snapshot_to_response: pure deterministic mapping from domain JobSnapshot.
- JobSnapshotReader: protocol specification and tenant-scoped retrieval.
- create_job_read_router: safe router factory requiring injected trusted tenant and reader.
- HTTP polling behavior:
  * All four statuses (PENDING, RUNNING, SUCCEEDED, FAILED) return HTTP 200.
  * Status is resource state (FAILED never maps to HTTP 500, 409, or 422).
  * Missing job and cross-tenant lookup return identical safe HTTP 404 ('job.not_found').
  * Path parameter strictly validates UUIDv4; invalid or non-v4 UUID returns HTTP 422.
  * Cache-Control: no-store present on 200; no invented Retry-After header.
  * Zero result, progress, retry, cancel, or tenant_id fields in public response.
- Security & Isolation:
  * Unmounted in production (verified against app.main.app.routes).
  * Zero SQLAlchemy or broker imports in app/api/v1/jobs.py.
  * No insecure client tenant derivations.
  * Local test app used exclusively for testing and OpenAPI generation.
"""

import ast
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.errors import register_api_exception_handlers
from app.api.v1.jobs import (
    JobFailureResponse,
    JobReadResponse,
    JobSnapshotReader,
    create_job_read_router,
    job_snapshot_to_response,
)
from app.core.auth.models import TenantContext
from app.core.jobs import (
    JobFailure,
    JobFailureCode,
    JobId,
    JobIdentity,
    JobSnapshot,
    JobStatus,
    JobType,
    generate_job_id,
)
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.main import app as production_app

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def _build_snapshot(
    *,
    tenant_id: str = "tenant-test-1",
    job_type: str = "schedule.solve",
    status: JobStatus = JobStatus.PENDING,
    failure: JobFailure | None = None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    job_id: JobId | None = None,
) -> JobSnapshot:
    """Construct a valid JobSnapshot for testing."""
    jid = job_id or generate_job_id()
    identity = JobIdentity(
        job_id=jid,
        tenant_id=tenant_id,
        job_type=JobType(job_type),
    )
    t0 = created_at or datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)

    if status == JobStatus.PENDING:
        return JobSnapshot(
            identity=identity,
            status=JobStatus.PENDING,
            failure=None,
            created_at=t0,
            started_at=None,
            completed_at=None,
        )
    elif status == JobStatus.RUNNING:
        t1 = started_at or datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        return JobSnapshot(
            identity=identity,
            status=JobStatus.RUNNING,
            failure=None,
            created_at=t0,
            started_at=t1,
            completed_at=None,
        )
    elif status == JobStatus.SUCCEEDED:
        t1 = started_at or datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        t2 = completed_at or datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        return JobSnapshot(
            identity=identity,
            status=JobStatus.SUCCEEDED,
            failure=None,
            created_at=t0,
            started_at=t1,
            completed_at=t2,
        )
    elif status == JobStatus.FAILED:
        t2 = completed_at or datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        fail = failure or JobFailure(
            code=JobFailureCode("infra.validation_error"),
            message="Input data validation failed",
        )
        return JobSnapshot(
            identity=identity,
            status=JobStatus.FAILED,
            failure=fail,
            created_at=t0,
            started_at=started_at,
            completed_at=t2,
        )
    raise ValueError(f"Unsupported status {status}")


def _create_test_app(
    *,
    tenant_dependency: Any,
    reader_dependency: Any,
) -> FastAPI:
    """Create an isolated test FastAPI application with the v1-composed job router."""
    test_app = FastAPI(title="Job Test App")
    register_api_exception_handlers(test_app)
    test_app.add_middleware(CorrelationAndLoggingMiddleware)

    v1_router = APIRouter(prefix="/api/v1")
    job_router = create_job_read_router(
        tenant_dependency=tenant_dependency,
        reader_dependency=reader_dependency,
    )
    v1_router.include_router(job_router)
    test_app.include_router(v1_router)
    return test_app


# ===========================================================================
# 1. JobReadResponse & JobFailureResponse Model Tests
# ===========================================================================


class TestJobResponseModels:
    """Verify JobReadResponse and JobFailureResponse schema contracts and invariants."""

    def test_job_read_response_fields_exact(self) -> None:
        """JobReadResponse must contain exactly the 7 canonical public fields."""
        expected_fields = {
            "job_id",
            "job_type",
            "status",
            "created_at",
            "started_at",
            "completed_at",
            "failure",
        }
        assert set(JobReadResponse.model_fields.keys()) == expected_fields

    def test_job_read_response_forbids_tenant_and_speculative_fields(self) -> None:
        """JobReadResponse must never include tenant identity or speculative fields."""
        forbidden_fields = {
            "tenant_id",
            "principal_id",
            "user_id",
            "email",
            "result",
            "result_json",
            "result_reference",
            "resource_url",
            "progress",
            "percentage",
            "retry_count",
            "priority",
            "worker_id",
            "cancel",
        }
        actual_fields = set(JobReadResponse.model_fields.keys())
        assert actual_fields.isdisjoint(forbidden_fields)

    def test_job_failure_response_fields_exact(self) -> None:
        """JobFailureResponse must contain exactly code and message."""
        expected_fields = {"code", "message"}
        assert set(JobFailureResponse.model_fields.keys()) == expected_fields

    def test_job_read_response_immutability(self) -> None:
        """JobReadResponse is frozen and extra fields are forbidden."""
        now = datetime.now(UTC)
        resp = JobReadResponse(
            job_id="11111111-1111-4111-8111-111111111111",
            job_type="tasks.sync",
            status=JobStatus.PENDING,
            created_at=now,
        )
        with pytest.raises(ValidationError):
            resp.status = JobStatus.RUNNING  # type: ignore[misc]

        with pytest.raises(ValidationError):
            JobReadResponse(
                job_id="11111111-1111-4111-8111-111111111111",
                job_type="tasks.sync",
                status=JobStatus.PENDING,
                created_at=now,
                extra_field="disallowed",  # type: ignore[call-arg]
            )

    def test_job_failure_response_immutability(self) -> None:
        """JobFailureResponse is frozen and extra fields are forbidden."""
        fail = JobFailureResponse(code="task.error", message="Failed")
        with pytest.raises(ValidationError):
            fail.message = "Changed"  # type: ignore[misc]

        with pytest.raises(ValidationError):
            JobFailureResponse(
                code="task.error",
                message="Failed",
                extra="disallowed",  # type: ignore[call-arg]
            )


# ===========================================================================
# 2. Pure Mapper Invariants (job_snapshot_to_response)
# ===========================================================================


class TestJobSnapshotToResponseMapper:
    """Verify job_snapshot_to_response performs pure, correct translation."""

    def test_maps_pending_snapshot(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.PENDING)
        resp = job_snapshot_to_response(snapshot)

        assert resp.job_id == str(snapshot.identity.job_id)
        assert resp.job_type == str(snapshot.identity.job_type)
        assert resp.status == JobStatus.PENDING
        assert resp.created_at == snapshot.created_at
        assert resp.started_at is None
        assert resp.completed_at is None
        assert resp.failure is None

    def test_maps_running_snapshot(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.RUNNING)
        resp = job_snapshot_to_response(snapshot)

        assert resp.status == JobStatus.RUNNING
        assert resp.started_at == snapshot.started_at
        assert resp.completed_at is None
        assert resp.failure is None

    def test_maps_succeeded_snapshot(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.SUCCEEDED)
        resp = job_snapshot_to_response(snapshot)

        assert resp.status == JobStatus.SUCCEEDED
        assert resp.started_at == snapshot.started_at
        assert resp.completed_at == snapshot.completed_at
        assert resp.failure is None

    def test_maps_failed_snapshot_with_failure(self) -> None:
        fail = JobFailure(code=JobFailureCode("infra.timeout"), message="Timed out")
        snapshot = _build_snapshot(status=JobStatus.FAILED, failure=fail)
        resp = job_snapshot_to_response(snapshot)

        assert resp.status == JobStatus.FAILED
        assert resp.failure is not None
        assert resp.failure.code == "infra.timeout"
        assert resp.failure.message == "Timed out"

    def test_mapper_never_exposes_tenant_id(self) -> None:
        snapshot = _build_snapshot(tenant_id="secret-tenant-999")
        resp = job_snapshot_to_response(snapshot)
        serialized = resp.model_dump()
        assert "tenant_id" not in serialized
        assert "secret-tenant-999" not in str(serialized)


# ===========================================================================
# 3. HTTP Polling Route & Status Semantics (GET /api/v1/jobs/{job_id})
# ===========================================================================


class TestJobReadHttpEndpoint:
    """Verify HTTP polling contract, status representations, and privacy boundaries."""

    def test_get_pending_job_returns_200(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.PENDING)
        jid = str(snapshot.identity.job_id)

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = snapshot

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{jid}")
        assert response.status_code == 200

        data = response.json()
        assert data["job_id"] == jid
        assert data["job_type"] == "schedule.solve"
        assert data["status"] == "pending"
        assert data["started_at"] is None
        assert data["completed_at"] is None
        assert data["failure"] is None
        assert "tenant_id" not in data

        # Protocol cache policy
        assert response.headers.get("cache-control") == "no-store"
        assert "retry-after" not in response.headers

    def test_get_running_job_returns_200(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.RUNNING)
        jid = str(snapshot.identity.job_id)

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = snapshot

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{jid}")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "running"
        assert data["started_at"] is not None
        assert data["completed_at"] is None
        assert data["failure"] is None

    def test_get_succeeded_job_returns_200_without_result_payload(self) -> None:
        snapshot = _build_snapshot(status=JobStatus.SUCCEEDED)
        jid = str(snapshot.identity.job_id)

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = snapshot

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{jid}")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "succeeded"
        assert data["started_at"] is not None
        assert data["completed_at"] is not None
        assert data["failure"] is None
        assert "result" not in data
        assert "result_json" not in data

    def test_get_failed_job_returns_200_with_sanitized_failure(self) -> None:
        """FAILED status is resource state; returns HTTP 200, NOT 500, 409, or 422."""
        fail = JobFailure(
            code=JobFailureCode("solver.infeasible"),
            message="No feasible schedule solution exists",
        )
        snapshot = _build_snapshot(status=JobStatus.FAILED, failure=fail)
        jid = str(snapshot.identity.job_id)

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = snapshot

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{jid}")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "failed"
        assert data["failure"] == {
            "code": "solver.infeasible",
            "message": "No feasible schedule solution exists",
        }
        # No leak of raw tracebacks, SQL, or internal exceptions
        assert "traceback" not in data["failure"]
        assert "stack" not in data["failure"]

    def test_get_failed_pre_execution_job_returns_200(self) -> None:
        """Pre-execution failure (started_at=None) serializes safely with HTTP 200."""
        fail = JobFailure(
            code=JobFailureCode("infra.validation_error"),
            message="Pre-execution constraint check failed",
        )
        snapshot = _build_snapshot(
            status=JobStatus.FAILED,
            failure=fail,
            started_at=None,
        )
        jid = str(snapshot.identity.job_id)

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = snapshot

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{jid}")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "failed"
        assert data["started_at"] is None
        assert data["completed_at"] is not None
        assert data["failure"]["code"] == "infra.validation_error"


# ===========================================================================
# 4. Not-Found & Cross-Tenant Isolation
# ===========================================================================


class TestJobReadNotFoundAndIsolation:
    """Verify safe 404 behavior and complete cross-tenant isolation."""

    def test_missing_job_returns_safe_404(self) -> None:
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = None

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-test-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        valid_uuid = str(uuid.uuid4())
        response = client.get(
            f"/api/v1/jobs/{valid_uuid}",
            headers={"X-Correlation-ID": "test-corr-id-1234"},
        )
        assert response.status_code == 404

        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "job.not_found"
        assert body["error"]["message"] == "Job not found."
        assert body["error"]["correlation_id"] == "test-corr-id-1234"

    def test_cross_tenant_lookup_indistinguishable_from_absent_job(self) -> None:
        """Tenant A querying Tenant B's job returns identical 404 with no leakage."""
        job_b_id = generate_job_id()

        async def fake_get(*, tenant_id: str, job_id: JobId) -> JobSnapshot | None:
            # Simulate database scoped lookup: job exists for 'tenant-b', not 'tenant-a'
            if tenant_id == "tenant-b":
                return _build_snapshot(tenant_id="tenant-b", job_id=job_b_id)
            return None

        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.side_effect = fake_get

        # Request originates under authenticated Tenant A
        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-a"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{job_b_id}")
        assert response.status_code == 404

        body = response.json()
        assert body["error"]["code"] == "job.not_found"
        assert body["error"]["message"] == "Job not found."
        # Verify Tenant B's identity is nowhere in the response
        assert "tenant-b" not in response.text

    def test_reader_receives_trusted_tenant_and_canonical_job_id(self) -> None:
        """Reader receives exact trusted tenant and canonical JobId; no client derivation."""
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = None

        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="trusted-org-42"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        jid = str(uuid.uuid4())
        client.get(f"/api/v1/jobs/{jid}")

        mock_reader.get.assert_awaited_once_with(
            tenant_id="trusted-org-42",
            job_id=JobId(jid),
        )

    def test_trusted_tenant_dependency_accepts_plain_string(self) -> None:
        """Router factory supports trusted tenant dependency returning str."""
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        mock_reader.get.return_value = None

        app = _create_test_app(
            tenant_dependency=lambda: "trusted-org-99",
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        jid = str(uuid.uuid4())
        client.get(f"/api/v1/jobs/{jid}")

        mock_reader.get.assert_awaited_once_with(
            tenant_id="trusted-org-99",
            job_id=JobId(jid),
        )


# ===========================================================================
# 5. Path Parameter Validation (UUIDv4)
# ===========================================================================


class TestJobIdPathParameterValidation:
    """Verify strict UUIDv4 path validation via canonical 422 envelope."""

    def test_rejects_non_uuid_string(self) -> None:
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        response = client.get("/api/v1/jobs/not-a-valid-uuid")
        assert response.status_code == 422

        body = response.json()
        assert body["error"]["code"] == "request.validation_failed"
        assert body["error"]["message"] == "Request validation failed."
        # Reader must never be invoked for malformed path input
        mock_reader.get.assert_not_called()

    def test_rejects_nil_uuid(self) -> None:
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        nil_uuid = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/api/v1/jobs/{nil_uuid}")
        assert response.status_code == 422

        body = response.json()
        assert body["error"]["code"] == "request.validation_failed"
        mock_reader.get.assert_not_called()

    def test_rejects_uuid_version_1(self) -> None:
        mock_reader = AsyncMock(spec=JobSnapshotReader)
        app = _create_test_app(
            tenant_dependency=lambda: TenantContext(tenant_id="tenant-1"),
            reader_dependency=lambda: mock_reader,
        )
        client = TestClient(app)

        u1 = str(uuid.uuid1())
        response = client.get(f"/api/v1/jobs/{u1}")
        assert response.status_code == 422

        body = response.json()
        assert body["error"]["code"] == "request.validation_failed"
        mock_reader.get.assert_not_called()


# ===========================================================================
# 6. Router Composition & OpenAPI Contract
# ===========================================================================


class TestRouterCompositionAndOpenApi:
    """Verify route prefixes, composition, and OpenAPI schema generation."""

    def test_router_prefix_is_relative_jobs(self) -> None:
        router = create_job_read_router(
            tenant_dependency=lambda: "t",
            reader_dependency=lambda: AsyncMock(),
        )
        assert router.prefix == "/jobs"

    def test_composed_v1_route_path(self) -> None:
        app = _create_test_app(
            tenant_dependency=lambda: "t",
            reader_dependency=lambda: AsyncMock(),
        )
        route_paths = [r.path for r in app.routes]
        assert "/api/v1/jobs/{job_id}" in route_paths
        # Verify no double prefix
        assert "/api/v1/api/v1/jobs/{job_id}" not in route_paths

    def test_openapi_schema_generation(self) -> None:
        app = _create_test_app(
            tenant_dependency=lambda: "t",
            reader_dependency=lambda: AsyncMock(),
        )
        schema = app.openapi()

        assert "/api/v1/jobs/{job_id}" in schema["paths"]
        get_op = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]

        # Responses include 200, 404, and 422
        assert "200" in get_op["responses"]
        assert "404" in get_op["responses"]
        assert "422" in get_op["responses"]

        # Component schemas include JobReadResponse and JobFailureResponse
        components = schema["components"]["schemas"]
        assert "JobReadResponse" in components
        assert "JobFailureResponse" in components

        # Verify JobReadResponse schema properties omit tenant_id
        props = components["JobReadResponse"]["properties"]
        assert "job_id" in props
        assert "status" in props
        assert "failure" in props
        assert "tenant_id" not in props


# ===========================================================================
# 7. Production Security & Architectural Isolation
# ===========================================================================


class TestProductionSecurityAndIsolation:
    """Verify production application is NOT mounted and module is architecturally isolated."""

    def test_production_app_does_not_mount_job_route(self) -> None:
        """CRITICAL: Production application routes must NOT expose the job polling router yet."""
        production_paths = [r.path for r in production_app.routes]
        for path in production_paths:
            assert "jobs" not in path, f"Production route leaked job path: {path}"

    def test_production_routes_unmodified(self) -> None:
        """Existing production routes remain exactly as frozen in Task 1I.2."""
        production_paths = set(r.path for r in production_app.routes)
        expected_paths = {
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/health",
            "/api/v1/ready",
        }
        assert expected_paths.issubset(production_paths)

    def test_jobs_module_architectural_isolation(self) -> None:
        """app/api/v1/jobs.py must import ZERO database or messaging dependencies."""
        with open("app/api/v1/jobs.py", encoding="utf-8") as f:
            source = f.read()

        parsed = ast.parse(source)
        imported_modules: set[str] = set()

        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module.split(".")[0])

        forbidden_imports = {
            "sqlalchemy",
            "aio_pika",
            "pika",
            "redis",
            "celery",
        }
        assert imported_modules.isdisjoint(forbidden_imports)

    def test_no_insecure_client_tenant_header_or_param(self) -> None:
        """app/api/v1/jobs.py must contain no unverified client tenant derivation."""
        with open("app/api/v1/jobs.py", encoding="utf-8") as f:
            source = f.read()

        # Check for banned unverified client input patterns
        assert "X-Tenant-ID" not in source
        assert "x_tenant_id" not in source.lower()
        assert "request.headers" not in source

    def test_no_job_mutation_endpoints(self) -> None:
        """The job router must expose strictly GET /{job_id}. No mutation routes."""
        router = create_job_read_router(
            tenant_dependency=lambda: "t",
            reader_dependency=lambda: AsyncMock(),
        )
        for route in router.routes:
            assert hasattr(route, "methods")
            methods = route.methods
            assert methods == {"GET"} or methods == {"GET", "HEAD"}
