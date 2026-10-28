"""Unit tests for versioned /api/v1 router composition and OpenAPI foundation.

Task 1I.2 of Phase 1 Foundation:
- Canonical API_V1_PREFIX ("/api/v1") and API_V1_NAME ("v1") constants.
- Canonical v1 aggregation router with exact prefix.
- Main FastAPI application composition with frozen Task-1I.1 exception handlers.
- Preservation of operational health/readiness endpoints and root endpoint.
- OpenAPI metadata (title, version, description) and documentation URL preservation.
- Architectural isolation and database schema stability (Alembic head 0006, 4 tables).
"""

import importlib
import json
import os

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.db.models  # noqa: F401
from app.api import (
    API_V1_NAME,
    API_V1_PREFIX,
    OPENAPI_DESCRIPTION,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
)
from app.api.errors import (
    ApiError,
    FoundationErrorCode,
    api_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.api.v1.router import api_router, router
from app.api.v1.schemas import HealthResponse, ReadinessResponse
from app.db.base import Base
from app.main import app

router_mod = importlib.import_module("app.api.v1.router")


# ---------------------------------------------------------------------------
# 1. API Prefix and Version Constants (Section 28, 4, 5)
# ---------------------------------------------------------------------------


class TestApiV1PrefixAndVersion:
    """Verify canonical API prefix and version identifier constants."""

    def test_canonical_prefix_constant(self) -> None:
        assert API_V1_PREFIX == "/api/v1"
        assert router_mod.API_V1_PREFIX == "/api/v1"

    def test_canonical_name_constant(self) -> None:
        assert API_V1_NAME == "v1"
        assert router_mod.API_V1_NAME == "v1"

    def test_no_duplicate_alternate_prefixes_in_module(self) -> None:
        # Check module attributes for invalid alternate prefixes
        for attr_name in dir(router_mod):
            if attr_name.startswith("API_") and attr_name.endswith("_PREFIX"):
                val = getattr(router_mod, attr_name)
                assert val == "/api/v1", f"Unexpected alternate prefix found: {attr_name}={val}"


# ---------------------------------------------------------------------------
# 2. Canonical V1 Router Composition (Section 29, 6)
# ---------------------------------------------------------------------------


class TestV1RouterComposition:
    """Verify canonical v1 APIRouter prefix, alias identity, and route paths."""

    def test_router_prefix(self) -> None:
        assert router.prefix == API_V1_PREFIX
        assert router.prefix == "/api/v1"

    def test_api_router_alias_identity(self) -> None:
        # Backwards-compatible alias must be the exact same router instance
        assert api_router is router

    def test_no_double_prefix_on_routes(self) -> None:
        for route in router.routes:
            assert route.path.startswith("/api/v1"), f"Route path lacks v1 prefix: {route.path}"
            assert not route.path.startswith("/api/v1/api/v1"), (
                f"Double prefix detected: {route.path}"
            )


# ---------------------------------------------------------------------------
# 3. Existing Route Preservation & Health Endpoints (Section 30, 31, 32, 1, 21)
# ---------------------------------------------------------------------------


class TestExistingRoutePreservation:
    """Verify operational health/readiness probes and root routes remain preserved."""

    def test_pre_task_route_inventory_preserved(self) -> None:
        expected_paths = {
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/integrations/calendar/google/webhook",
            "/",
        }
        actual_paths = {r.path for r in app.routes}
        assert actual_paths == expected_paths, (
            f"Expected routes {expected_paths}, got {actual_paths}"
        )

    def test_health_liveness_endpoint_functional(self) -> None:
        client = TestClient(app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        model = HealthResponse(**data)
        assert model.status == "ok"
        assert model.service == "operations-engine"
        assert model.environment in {"local", "development", "test", "staging", "production"}

    def test_readiness_endpoint_functional(self) -> None:
        client = TestClient(app)
        response = client.get("/api/v1/ready")
        assert response.status_code == 200
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "ready"
        assert model.service == "operations-engine"
        assert model.environment in {"local", "development", "test", "staging", "production"}

    def test_root_endpoint_functional(self) -> None:
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert data["docs"] == "/docs"
        assert "engine" in data

    def test_health_probes_not_relocated_or_duplicated(self) -> None:
        client = TestClient(app)
        # Ensure root-level health/ready paths do NOT exist
        assert client.get("/health").status_code == 404
        assert client.get("/ready").status_code == 404

        # Ensure no speculative version endpoints were invented
        assert client.get("/api/v1").status_code == 404
        assert client.get("/api/v1/version").status_code == 404
        assert client.get("/api/v1/info").status_code == 404

    def test_calendar_webhook_mounted_exactly_once(self) -> None:
        """Verify POST /api/v1/integrations/calendar/google/webhook is mounted on production app."""
        webhook_routes = [
            r
            for r in app.routes
            if getattr(r, "path", None) == "/api/v1/integrations/calendar/google/webhook"
        ]
        assert len(webhook_routes) == 1, (
            f"Expected exactly 1 webhook route, got {len(webhook_routes)}"
        )
        route = webhook_routes[0]
        assert "POST" in getattr(route, "methods", set())

        # Verify present in OpenAPI specification exactly once
        openapi_schema = app.openapi()
        paths = openapi_schema.get("paths", {})
        assert "/api/v1/integrations/calendar/google/webhook" in paths
        assert "post" in paths["/api/v1/integrations/calendar/google/webhook"]


# ---------------------------------------------------------------------------
# 4. Error Handler Wiring on Production App (Section 33, 34, 35, 10)
# ---------------------------------------------------------------------------


class TestAppExceptionHandlerRegistration:
    """Verify Task-1I.1 exception handlers are registered on app.main.app."""

    def test_handlers_registered_structurally(self) -> None:
        handlers = app.exception_handlers

        assert ApiError in handlers
        assert handlers[ApiError] is api_error_handler

        assert RequestValidationError in handlers
        assert handlers[RequestValidationError] is validation_error_handler

        assert StarletteHTTPException in handlers
        assert handlers[StarletteHTTPException] is http_exception_handler

        assert FastAPIHTTPException in handlers
        assert handlers[FastAPIHTTPException] is http_exception_handler

        assert Exception in handlers
        assert handlers[Exception] is unhandled_exception_handler

    def test_actual_app_404_uses_canonical_error_envelope(self) -> None:
        client = TestClient(app)
        custom_cid = "test-prod-app-404-cid"
        res = client.get(
            "/api/v1/nonexistent-route-for-testing",
            headers={"X-Correlation-ID": custom_cid},
        )
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/json")
        assert res.headers.get("X-Correlation-ID") == custom_cid

        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_NOT_FOUND
        assert data["error"]["message"] == "Resource not found."
        assert data["error"]["correlation_id"] == custom_cid
        assert "details" not in data["error"]


# ---------------------------------------------------------------------------
# 5. OpenAPI Metadata & Documentation URLs (Section 36, 37, 38, 39, 12, 13, 14)
# ---------------------------------------------------------------------------


class TestOpenApiMetadataAndDocs:
    """Verify OpenAPI metadata, version, title, description, and docs URLs."""

    def test_openapi_info_metadata(self) -> None:
        schema = app.openapi()
        assert "info" in schema
        info = schema["info"]

        assert info["title"] == OPENAPI_TITLE
        assert info["title"] == "E-7 Operations Engine API"

        assert info["version"] == OPENAPI_VERSION
        assert info["version"] == "1.0.0"

        assert info["description"] == OPENAPI_DESCRIPTION
        assert info["description"] == "E-7 Operations Engine HTTP API"

    def test_openapi_servers_unset(self) -> None:
        schema = app.openapi()
        # Servers must remain unset / empty to avoid hardcoding environment URLs
        assert schema.get("servers") is None or schema.get("servers") == []

    def test_docs_urls_preserved(self) -> None:
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

        client = TestClient(app)
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_no_secret_or_infrastructure_leakage_in_openapi(self) -> None:
        schema_text = json.dumps(app.openapi())
        forbidden_substrings = [
            "password",
            "postgres://",
            "amqp://",
            "secret",
            "token",
            "localhost:5432",
            "localhost:5672",
        ]
        for term in forbidden_substrings:
            assert term not in schema_text.lower(), f"Forbidden string found in OpenAPI: {term}"

    def test_openapi_versioned_paths(self) -> None:
        schema = app.openapi()
        paths = schema.get("paths", {})
        # All versioned routes in OpenAPI must start with /api/v1
        versioned_paths = [p for p in paths if p != "/"]
        assert len(versioned_paths) >= 2
        for p in versioned_paths:
            assert p.startswith(API_V1_PREFIX), f"Path does not start with {API_V1_PREFIX}: {p}"


# ---------------------------------------------------------------------------
# 6. Scope Boundaries & Architectural Isolation (Section 40, 41, 42)
# ---------------------------------------------------------------------------


class TestScopeAndIsolation:
    """Verify absence of premature business routes, idempotency HTTP, and ORM imports."""

    def test_no_business_resource_endpoints(self) -> None:
        actual_paths = [r.path for r in app.routes]
        forbidden_segments = ["/tasks", "/workflows", "/schedules", "/jobs"]
        for path in actual_paths:
            for seg in forbidden_segments:
                assert seg not in path, f"Forbidden business endpoint found: {path}"

    def test_no_idempotency_http_handling_in_router(self) -> None:
        router_source = open(router_mod.__file__, encoding="utf-8").read()
        assert "Idempotency-Key" not in router_source
        assert "IdempotencyConflictError" not in router_source
        assert "IdempotencyRepository" not in router_source
        assert "IdempotentMutationExecutor" not in router_source

    def test_router_architectural_isolation(self) -> None:
        forbidden_imports = [
            "sqlalchemy",
            "AsyncSession",
            "OutboxRepository",
            "InboxRepository",
            "IdempotencyRepository",
            "aio_pika",
            "rabbitmq",
        ]
        router_source = open(router_mod.__file__, encoding="utf-8").read()
        for term in forbidden_imports:
            assert f"import {term}" not in router_source
            assert f"from {term}" not in router_source


# ---------------------------------------------------------------------------
# 7. Database and Migration Stability (Section 43)
# ---------------------------------------------------------------------------


class TestDatabaseSchemaStability:
    """Verify database schema contains exactly 5 canonical tables, head at 0007."""

    def test_metadata_tables_are_exact_five(self) -> None:
        phase1_tables = {
            "audit_events",
            "outbox_events",
            "inbox_messages",
            "idempotency_records",
            "jobs",
        }
        canonical_phase4_tables = phase1_tables | {
            "goals",
            "plans",
            "milestones",
            "tasks",
            "task_dependencies",
            "task_risks",
            "workflow_definitions",
            "workflow_instances",
            "workflow_task_mappings",
            "sla_policies",
            "workflow_artifacts",
            # Phase 4A Scheduling (7)
            "resources",
            "resource_availabilities",
            "constraint_definitions",
            "solver_profiles",
            "schedule_jobs",
            "schedules",
            "schedule_assignments",
            # Phase 4B Calendar (1)
            "calendar_links",
            # Phase 7 Calendar Watch (1)
            "calendar_watch_channels",
            # Phase 8D Service Credentials Persistence (1)
            "service_api_credentials",
        }
        actual_tables = set(Base.metadata.tables.keys())
        missing = phase1_tables - actual_tables
        assert phase1_tables <= actual_tables, f"Missing Phase-1 tables: {missing}"
        assert actual_tables == canonical_phase4_tables, (
            f"Expected canonical Phase-4 tables {canonical_phase4_tables}, got {actual_tables}"
        )
        assert len(actual_tables) == 26

    def test_alembic_head_remains_0007(self) -> None:
        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert heads == ["0016"], f"Alembic heads expected ['0016'], got {heads}"

        rev_0007 = script.get_revision("0007")
        assert rev_0007 is not None
        assert rev_0007.down_revision == "0006"

        rev_0008 = script.get_revision("0008")
        assert rev_0008 is not None
        assert rev_0008.down_revision == "0007"

        rev_0009 = script.get_revision("0009")
        assert rev_0009 is not None
        assert rev_0009.down_revision == "0008"

        rev_0010 = script.get_revision("0010")
        assert rev_0010 is not None
        assert rev_0010.down_revision == "0009"

        rev_0011 = script.get_revision("0011")
        assert rev_0011 is not None
        assert rev_0011.down_revision == "0010"

        rev_0012 = script.get_revision("0012")
        assert rev_0012 is not None
        assert rev_0012.down_revision == "0011"

        rev_0013 = script.get_revision("0013")
        assert rev_0013 is not None
        assert rev_0013.down_revision == "0012"

        rev_0014 = script.get_revision("0014")
        assert rev_0014 is not None
        assert rev_0014.down_revision == "0013"

        rev_0015 = script.get_revision("0015")
        assert rev_0015 is not None
        assert rev_0015.down_revision == "0014"

        rev_0016 = script.get_revision("0016")
        assert rev_0016 is not None
        assert rev_0016.down_revision == "0015"

        versions_dir = os.path.join("app", "db", "migrations", "versions")
        files = os.listdir(versions_dir)
        migration_0017 = [f for f in files if "0017" in f]
        assert len(migration_0017) == 0, f"Found unexpected migration 0017: {migration_0017}"
