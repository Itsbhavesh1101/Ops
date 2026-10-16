"""Unit tests for HTTP Idempotency-Key boundary and conflict presentation.

Task 1I.3 of Phase 1 Foundation:
- Canonical IDEMPOTENCY_KEY_HEADER constant ("Idempotency-Key") and no aliases.
- Header extraction and resolution (resolve_idempotency_key_digest):
  - Exactly one valid header -> IdempotencyKeyDigest.
  - Zero headers -> 400 idempotency.key_required.
  - Multiple headers -> 400 idempotency.key_ambiguous.
  - Invalid headers (blank, whitespace, control chars, oversized) -> 400 idempotency.key_invalid.
- Transient raw key and privacy: raw secret never retained in context, model dump, repr, or error.
- Identity construction (build_http_idempotency_identity):
  - Trusted tenant from caller (str or TenantContext), never derived from headers/query/path/body.
  - Reuses frozen OperationName, IdempotencyKeyDigest, IdempotencyIdentity.
  - Zero principal ID, zero raw key, zero transport artifacts.
- HttpIdempotencyContext value object:
  - Holds canonical identity and optional caller-supplied RequestFingerprint.
  - Never calculates fingerprints or calls request.body().
- Conflict presentation (idempotency_conflict_handler):
  - Maps IdempotencyConflictError -> HTTP 409 Conflict.
  - Reuses Task-1I.1 canonical error envelope, correlation ID, and privacy guarantees.
- Actual application exception handler registration (register_idempotency_exception_handler):
  - IdempotencyConflictError registered on app.main.app alongside Task-1I.1 handlers.
- Router composition preservation: /api/v1 prefix, health, readiness, root, OpenAPI unchanged.
- Architectural isolation: zero database or messaging imports in app/api/idempotency.py.
- Database schema stability: Alembic head 0006, exactly 4 canonical metadata tables.
"""

import ast
import inspect
import json
import os
from collections.abc import Generator

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

import app.db.models  # noqa: F401
from app.api.errors import (
    ApiError,
    register_api_exception_handlers,
)
from app.api.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    HttpIdempotencyContext,
    IdempotencyErrorCode,
    build_http_idempotency_identity,
    idempotency_conflict_handler,
    register_idempotency_exception_handler,
    resolve_idempotency_key_digest,
)
from app.core.auth.models import TenantContext
from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    IdempotencyKeyDigest,
    OperationName,
    RequestFingerprint,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.core.idempotency.hashing import digest_idempotency_key
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.db.base import Base
from app.main import app as production_app

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_request_with_headers(headers_list: list[tuple[bytes, bytes]]) -> Request:
    """Construct an ASGI Request directly with explicit raw headers."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/test-idempotency",
        "headers": headers_list,
    }
    return Request(scope)


def create_idempotency_test_app() -> FastAPI:
    """Create an isolated FastAPI test app registering both error and idempotency handlers."""
    test_app = FastAPI(title="IdempotencyTestApp")
    test_app.add_middleware(CorrelationAndLoggingMiddleware)
    register_api_exception_handlers(test_app)
    register_idempotency_exception_handler(test_app)

    @test_app.post("/test-endpoint")
    async def sample_endpoint(request: Request) -> dict[str, str]:
        digest = resolve_idempotency_key_digest(request)
        return {"digest": digest.value}

    @test_app.post("/test-conflict")
    async def sample_conflict(request: Request) -> dict[str, str]:
        digest = resolve_idempotency_key_digest(request)
        identity = IdempotencyIdentity(
            tenant_id="tenant-alpha",
            operation_name="tasks.create",
            key_digest=digest,
        )
        raise IdempotencyConflictError(
            identity=identity,
            request_fingerprint=RequestFingerprint("a" * 64),
            existing_fingerprint=RequestFingerprint("b" * 64),
        )

    return test_app


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Isolated TestClient for HTTP idempotency behavior verification."""
    client_app = create_idempotency_test_app()
    with TestClient(client_app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def prod_client() -> Generator[TestClient, None, None]:
    """TestClient against the production FastAPI app."""
    client = TestClient(production_app, raise_server_exceptions=False)
    yield client


# ===========================================================================
# 1. Header Name Constant & Alias Rejection (Sections 5, 32)
# ===========================================================================


class TestIdempotencyHeaderConstant:
    """Verify canonical header name constant and strict absence of aliases."""

    def test_canonical_header_constant(self) -> None:
        assert IDEMPOTENCY_KEY_HEADER == "Idempotency-Key"

    def test_no_alternate_aliases_defined(self) -> None:
        """Verify no X-Idempotency-Key, Request-Id, or X-Request-Id constants are defined."""
        import app.api.idempotency as idempotency_mod

        module_attrs = dir(idempotency_mod)
        for forbidden in [
            "X_IDEMPOTENCY_KEY_HEADER",
            "REQUEST_ID_HEADER",
            "X_REQUEST_ID_HEADER",
            "X_TENANT_ID_HEADER",
        ]:
            assert forbidden not in module_attrs, f"Forbidden header alias {forbidden} found"


# ===========================================================================
# 2. Header Extraction & Validation (Sections 6, 7, 8, 9, 10, 11, 33-36)
# ===========================================================================


class TestIdempotencyHeaderResolution:
    """Verify raw header extraction, validation, digesting, and ambiguity rejection."""

    def test_valid_header_resolution(self) -> None:
        raw_key = "client-mutation-key-12345"
        req = make_request_with_headers([(b"idempotency-key", raw_key.encode("utf-8"))])

        digest = resolve_idempotency_key_digest(req)
        assert isinstance(digest, IdempotencyKeyDigest)
        expected_digest = digest_idempotency_key(raw_key)
        assert digest == expected_digest
        assert digest.value == expected_digest.value

    def test_case_insensitive_header_matching(self, test_client: TestClient) -> None:
        raw_key = "case-test-key-999"
        resp = test_client.post(
            "/test-endpoint",
            headers={"IDEMPOTENCY-KEY": raw_key},
        )
        assert resp.status_code == 200
        assert resp.json()["digest"] == digest_idempotency_key(raw_key).value

    def test_missing_header_raises_key_required_error(self) -> None:
        req = make_request_with_headers([])

        with pytest.raises(ApiError) as exc_info:
            resolve_idempotency_key_digest(req)

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == IdempotencyErrorCode.KEY_REQUIRED
        assert err.code == "idempotency.key_required"
        assert err.message == "Idempotency-Key header is required."

    def test_duplicate_headers_raises_key_ambiguous_error(self) -> None:
        req = make_request_with_headers(
            [
                (b"idempotency-key", b"key-first"),
                (b"idempotency-key", b"key-second"),
            ]
        )

        with pytest.raises(ApiError) as exc_info:
            resolve_idempotency_key_digest(req)

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == IdempotencyErrorCode.KEY_AMBIGUOUS
        assert err.code == "idempotency.key_ambiguous"
        assert err.message == "Multiple Idempotency-Key headers are not allowed."

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "",  # empty
            "   ",  # whitespace only
            " leading-space",  # leading whitespace
            "trailing-space ",  # trailing whitespace
            "tab-space\t",  # trailing tab
            "newline\n",  # trailing newline
            "control-\x00-char",  # null byte
            "control-\x1f-char",  # unit separator control char
            "control-\x7f-delete",  # DEL control char
            "k" * 256,  # oversized (> 255 chars)
        ],
    )
    def test_invalid_header_raises_key_invalid_error(self, invalid_key: str) -> None:
        req = make_request_with_headers([(b"idempotency-key", invalid_key.encode("utf-8"))])

        with pytest.raises(ApiError) as exc_info:
            resolve_idempotency_key_digest(req)

        err = exc_info.value
        assert err.status_code == 400
        assert err.code == IdempotencyErrorCode.KEY_INVALID
        assert err.code == "idempotency.key_invalid"
        assert err.message == "Idempotency-Key header is invalid."
        # Privacy check: non-empty raw invalid key must not appear in exception string or message
        if invalid_key:
            assert invalid_key not in err.message
            assert invalid_key not in str(err)


# ===========================================================================
# 3. Raw Key Non-Retention & Privacy (Sections 6, 26, 37)
# ===========================================================================


class TestRawKeyNonRetentionAndPrivacy:
    """Verify raw client keys are strictly transient and never leak."""

    SECRET_KEY = "SYNTHETIC_IDEMPOTENCY_SECRET_987654321"

    def test_raw_key_not_retained_in_context_or_models(self) -> None:
        req = make_request_with_headers([(b"idempotency-key", self.SECRET_KEY.encode("utf-8"))])
        digest = resolve_idempotency_key_digest(req)

        identity = build_http_idempotency_identity(
            tenant_id="tenant-test",
            operation_name=OperationName("tasks.create"),
            key_digest=digest,
        )
        context = HttpIdempotencyContext(identity=identity)

        # 1. Not in context repr
        assert self.SECRET_KEY not in repr(context)
        # 2. Not in context str
        assert self.SECRET_KEY not in str(context)
        # 3. Not in model dump
        dump_data = context.model_dump(mode="json")
        dump_str = str(dump_data)
        assert self.SECRET_KEY not in dump_str
        # 4. Only SHA-256 digest is present
        expected_digest = digest_idempotency_key(self.SECRET_KEY).value
        assert dump_data["identity"]["key_digest"] == expected_digest
        assert expected_digest in repr(context)

    def test_raw_key_not_retained_on_request_state(self) -> None:
        req = make_request_with_headers([(b"idempotency-key", self.SECRET_KEY.encode("utf-8"))])
        _ = resolve_idempotency_key_digest(req)

        # Verify nothing was attached to request.state
        state_dict = getattr(req.state, "_state", {})
        assert self.SECRET_KEY not in str(state_dict)

    def test_client_http_roundtrip_never_leaks_raw_key(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/test-endpoint",
            headers={"Idempotency-Key": self.SECRET_KEY},
        )
        assert resp.status_code == 200
        assert self.SECRET_KEY not in resp.text
        # SHA-256 digest is present in response
        expected_digest = digest_idempotency_key(self.SECRET_KEY).value
        assert resp.json()["digest"] == expected_digest


# ===========================================================================
# 4. Trusted Tenant & Identity Construction (Sections 13, 14, 38, 39)
# ===========================================================================


class TestTrustedTenantAndIdentityBuilder:
    """Verify build_http_idempotency_identity invariants and strict tenant boundary."""

    def test_build_identity_with_string_tenant(self) -> None:
        digest = digest_idempotency_key("sample-key-1")
        identity = build_http_idempotency_identity(
            tenant_id="tenant-123",
            operation_name="tasks.create",
            key_digest=digest,
        )

        assert isinstance(identity, IdempotencyIdentity)
        assert identity.tenant_id == "tenant-123"
        assert identity.operation_name == "tasks.create"
        assert identity.key_digest == digest

    def test_build_identity_with_tenant_context(self) -> None:
        digest = digest_idempotency_key("sample-key-2")
        tenant_ctx = TenantContext(tenant_id="tenant-456", name="Alpha Org")
        identity = build_http_idempotency_identity(
            tenant_id=tenant_ctx,
            operation_name=OperationName("orders.process"),
            key_digest=digest,
        )

        assert identity.tenant_id == "tenant-456"
        assert identity.operation_name == "orders.process"
        assert identity.key_digest == digest

    def test_build_identity_rejects_invalid_tenant_types(self) -> None:
        digest = digest_idempotency_key("sample-key-3")
        with pytest.raises(TypeError, match="tenant_id must be a string or TenantContext"):
            build_http_idempotency_identity(
                tenant_id=12345,  # type: ignore[arg-type]
                operation_name="tasks.create",
                key_digest=digest,
            )

    def test_build_identity_rejects_invalid_digest_types(self) -> None:
        with pytest.raises(TypeError, match="key_digest must be an IdempotencyKeyDigest"):
            build_http_idempotency_identity(
                tenant_id="tenant-123",
                operation_name="tasks.create",
                key_digest="not-a-digest",  # type: ignore[arg-type]
            )

    def test_source_inspects_no_request_for_tenant(self) -> None:
        """AST check: identity builder does not inspect request headers, body, or params."""
        import app.api.idempotency as idempotency_mod

        src = inspect.getsource(idempotency_mod.build_http_idempotency_identity)
        tree = ast.parse(src)

        # Check function parameters: strictly keyword-only, no 'request' parameter
        fn_def = tree.body[0]
        assert isinstance(fn_def, ast.FunctionDef)
        param_names = [arg.arg for arg in fn_def.args.kwonlyargs]
        assert "request" not in param_names
        assert "tenant_id" in param_names
        assert "operation_name" in param_names
        assert "key_digest" in param_names

        # Check AST nodes for any attribute access on headers, query_params, or path_params
        for node in ast.walk(fn_def):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "headers",
                    "query_params",
                    "path_params",
                    "cookies",
                }

        # Check module has no X_TENANT_ID constant
        assert "X_TENANT_ID" not in dir(idempotency_mod)


# ===========================================================================
# 5. Request Fingerprint Input & Payload Independence (Sections 12, 16, 40)
# ===========================================================================


class TestRequestFingerprintBoundary:
    """Verify Task-1I.3 never reads request.body() or calculates fingerprints."""

    def test_http_idempotency_context_accepts_precomputed_fingerprint(self) -> None:
        digest = digest_idempotency_key("fingerprint-test-key")
        identity = build_http_idempotency_identity(
            tenant_id="tenant-123",
            operation_name="tasks.create",
            key_digest=digest,
        )
        fp = RequestFingerprint("e" * 64)

        ctx = HttpIdempotencyContext(identity=identity, request_fingerprint=fp)
        assert ctx.identity == identity
        assert ctx.request_fingerprint == fp

    def test_http_idempotency_context_allows_none_fingerprint(self) -> None:
        digest = digest_idempotency_key("fingerprint-test-key-2")
        identity = build_http_idempotency_identity(
            tenant_id="tenant-123",
            operation_name="tasks.create",
            key_digest=digest,
        )
        ctx = HttpIdempotencyContext(identity=identity)
        assert ctx.request_fingerprint is None

    def test_source_contains_no_body_reading_or_fingerprinting(self) -> None:
        """AST check: app/api/idempotency.py does NOT call fingerprint_bytes or request.body()."""
        import app.api.idempotency as idempotency_mod

        src = inspect.getsource(idempotency_mod)
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # No calls to fingerprint_bytes
                if isinstance(node.func, ast.Name):
                    assert node.func.id != "fingerprint_bytes"
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "fingerprint_bytes"
                    # No call to request.body() or req.body()
                    assert node.func.attr != "body"


# ===========================================================================
# 6. Conflict Mapping & Presentation (Sections 17, 18, 41, 42)
# ===========================================================================


class TestIdempotencyConflictPresentation:
    """Verify IdempotencyConflictError maps to canonical HTTP 409 with safe envelope."""

    def test_direct_conflict_handler_delegation(self) -> None:
        import asyncio

        digest = digest_idempotency_key("direct-conflict-key")
        identity = IdempotencyIdentity(
            tenant_id="tenant-abc",
            operation_name="tasks.create",
            key_digest=digest,
        )
        conflict_exc = IdempotencyConflictError(
            identity=identity,
            request_fingerprint=RequestFingerprint("1" * 64),
            existing_fingerprint=RequestFingerprint("2" * 64),
        )

        req = make_request_with_headers([(b"idempotency-key", b"direct-conflict-key")])
        req.state.correlation_id = "cid-direct-test-123"

        response = asyncio.run(idempotency_conflict_handler(req, conflict_exc))
        assert response.status_code == 409

        data = json.loads(response.body.decode("utf-8"))
        assert "error" in data
        assert data["error"]["code"] == "idempotency.conflict"
        assert (
            data["error"]["message"] == "Idempotency key was already used for a different request."
        )
        assert data["error"]["correlation_id"] == "cid-direct-test-123"
        assert response.headers["X-Correlation-ID"] == "cid-direct-test-123"

    def test_http_endpoint_conflict_mapping(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/test-conflict",
            headers={
                "Idempotency-Key": "conflict-test-key",
                "X-Correlation-ID": "cid-conflict-test-456",
            },
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "idempotency.conflict"
        assert (
            data["error"]["message"] == "Idempotency key was already used for a different request."
        )
        assert data["error"]["correlation_id"] == "cid-conflict-test-456"
        assert resp.headers["X-Correlation-ID"] == "cid-conflict-test-456"

    def test_conflict_response_privacy(self, test_client: TestClient) -> None:
        """Verify conflict response contains no raw key, bodies, traces, or internal data."""
        raw_key = "TOP_SECRET_CONFLICT_KEY_777"
        resp = test_client.post(
            "/test-conflict",
            headers={"Idempotency-Key": raw_key},
        )
        assert resp.status_code == 409
        body_text = resp.text
        assert raw_key not in body_text
        assert "Traceback" not in body_text
        assert "IdempotencyConflictError" not in body_text
        assert "1" * 64 not in body_text  # Fingerprints are not leaked in public details
        assert "2" * 64 not in body_text


# ===========================================================================
# 7. TestClient End-to-End Route Validation (Sections 34, 35, 36)
# ===========================================================================


class TestIdempotencyRouteIntegration:
    """Verify HTTP behavior through TestClient on isolated test endpoints."""

    def test_missing_header_returns_400(self, test_client: TestClient) -> None:
        resp = test_client.post("/test-endpoint")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "idempotency.key_required"
        assert data["error"]["message"] == "Idempotency-Key header is required."

    def test_duplicate_headers_returns_400(self, test_client: TestClient) -> None:
        # httpx / TestClient accepts list of tuples for duplicate headers
        resp = test_client.post(
            "/test-endpoint",
            headers=[
                ("Idempotency-Key", "key-first-occurrence"),
                ("Idempotency-Key", "key-second-occurrence"),
            ],
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "idempotency.key_ambiguous"
        assert data["error"]["message"] == "Multiple Idempotency-Key headers are not allowed."

    def test_invalid_header_returns_400(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/test-endpoint",
            headers={"Idempotency-Key": " whitespace-key "},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "idempotency.key_invalid"
        assert data["error"]["message"] == "Idempotency-Key header is invalid."


# ===========================================================================
# 8. Actual Production App Exception Handler Registration (Sections 20, 21, 43, 44)
# ===========================================================================


class TestProductionAppRegistration:
    """Verify production FastAPI application wiring in app/main.py."""

    def test_conflict_handler_registered_in_production_app(self) -> None:
        handlers = production_app.exception_handlers
        assert IdempotencyConflictError in handlers
        assert handlers[IdempotencyConflictError] is idempotency_conflict_handler

    def test_generic_task_1i1_handlers_preserved(self) -> None:
        handlers = production_app.exception_handlers
        assert ApiError in handlers
        assert RequestValidationError in handlers
        assert FastAPIHTTPException in handlers
        assert StarletteHTTPException in handlers
        assert Exception in handlers

    def test_registration_helper_is_idempotent(self) -> None:
        """Calling register_idempotency_exception_handler multiple times is safe."""
        register_idempotency_exception_handler(production_app)
        register_idempotency_exception_handler(production_app)
        assert IdempotencyConflictError in production_app.exception_handlers


# ===========================================================================
# 9. V1 Route Composition & Foundation Preservation (Sections 20, 45)
# ===========================================================================


class TestV1CompositionPreservation:
    """Verify existing Task-1I.1 and Task-1I.2 routes remain intact."""

    def test_health_endpoint_preserved(self, prod_client: TestClient) -> None:
        resp = prod_client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness_endpoint_preserved(self, prod_client: TestClient) -> None:
        resp = prod_client.get("/api/v1/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_root_endpoint_preserved(self, prod_client: TestClient) -> None:
        resp = prod_client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert data["docs"] == "/docs"

    def test_docs_and_openapi_preserved(self, prod_client: TestClient) -> None:
        docs_resp = prod_client.get("/docs")
        assert docs_resp.status_code == 200
        openapi_resp = prod_client.get("/openapi.json")
        assert openapi_resp.status_code == 200


# ===========================================================================
# 10. Success Policy & No Generic Response Replay (Sections 2, 22, 23, 24, 46)
# ===========================================================================


class TestSuccessPolicyAndNoGenericReplay:
    """Verify absence of generic status mappings (EXECUTED/ALREADY_PROCESSED) and replay."""

    def test_no_generic_status_mapping_functions_exist(self) -> None:
        import app.api.idempotency as idempotency_mod

        src = inspect.getsource(idempotency_mod)
        tree = ast.parse(src)

        # Check that no function in app/api/idempotency.py returns 200/201/204 or maps outcome
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert "map_outcome" not in node.name
                assert "outcome_to_status" not in node.name
                assert "status_for_outcome" not in node.name

        # Verify module attributes do not define response replay or status maps
        attrs = dir(idempotency_mod)
        assert "OUTCOME_STATUS_MAP" not in attrs
        assert "EXECUTED_STATUS" not in attrs
        assert "ALREADY_PROCESSED_STATUS" not in attrs

    def test_no_response_replay_structures_exist(self) -> None:
        import app.api.idempotency as idempotency_mod

        attrs = dir(idempotency_mod)
        assert "response_body" not in attrs
        assert "response_headers" not in attrs
        assert "cached_response" not in attrs
        assert "result_reference" not in attrs


# ===========================================================================
# 11. Architectural Isolation (Sections 30, 31, 47)
# ===========================================================================


class TestArchitecturalIsolation:
    """Verify app/api/idempotency.py does not import database, ORM, or messaging."""

    def test_forbidden_imports_not_present_in_source(self) -> None:
        import app.api.idempotency as idempotency_mod

        src = inspect.getsource(idempotency_mod)
        tree = ast.parse(src)

        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.append(node.module)

        forbidden_prefixes = [
            "sqlalchemy",
            "app.db",
            "app.events",
            "app.audit",
            "aio_pika",
        ]
        for mod in imported_modules:
            for forbidden in forbidden_prefixes:
                assert not mod.startswith(forbidden), (
                    f"Forbidden import '{mod}' found in app/api/idempotency.py"
                )

    def test_no_executor_invocation_in_adapter(self) -> None:
        import app.api.idempotency as idempotency_mod

        src = inspect.getsource(idempotency_mod)
        assert "IdempotentMutationExecutor" not in src
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id != "execute"
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "execute"


# ===========================================================================
# 12. Database Schema Stability (Sections 48, 56, 57, 58)
# ===========================================================================


class TestDatabaseSchemaStability:
    """Verify database schema, Alembic head, and metadata tables are untouched."""

    def test_metadata_tables_exactly_canonical_five(self) -> None:
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
        # Historical Phase-1 tables remain intact
        assert phase1_tables <= set(Base.metadata.tables.keys())
        # Canonical Phase-4 table set equals expected 26 tables
        assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
        assert len(Base.metadata.tables) == 26

    def test_alembic_head_remains_0007(self) -> None:
        alembic_cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "alembic.ini",
        )
        if not os.path.exists(alembic_cfg_path):
            alembic_cfg_path = "alembic.ini"

        config = Config(alembic_cfg_path)
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()

        assert len(heads) == 1
        assert heads[0] == "0014"
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

    def test_no_migration_0008_exists(self) -> None:
        versions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "app",
            "db",
            "migrations",
            "versions",
        )
        if not os.path.exists(versions_dir):
            versions_dir = os.path.join("app", "db", "migrations", "versions")

        version_files = os.listdir(versions_dir)
        has_0014 = any(vf.startswith("0014") for vf in version_files)
        assert has_0014, "Migration 0014 must exist for Phase 8D"
        for vf in version_files:
            assert not vf.startswith("0015"), f"Unexpected migration file found beyond 0014: {vf}"
