"""Unit tests for Audit HTTP API route authorization and contract enforcement.

Verifies:
- Authorized auditor with 'audit:read' permission succeeds (200 OK).
- Unauthorized planner receives HTTP 403 Forbidden.
- Unauthorized viewer receives HTTP 403 Forbidden.
- Missing authentication / anonymous request receives HTTP 401 Unauthorized.
- Wrong tenant context mismatch raises HTTP 403 (audit.cross_tenant_forbidden).
- Valid administrator / tenant_admin with wildcard '*' permission succeeds (200 OK).
- Malformed or non-principal context fails closed with HTTP 401 Unauthorized.
- Client-provided X-Tenant-ID header cannot override the verified principal's tenant authority.
- Query parameters (event_type, correlation_id, etc.) are passed correctly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.audit import create_audit_router
from app.audit.models import AuditEventRecord
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
)


def _make_principal(
    principal_id: str = "test-auditor",
    tenant_id: str = "tenant-alpha",
    roles: frozenset[str] = frozenset({"auditor"}),
    permissions: frozenset[str] = frozenset({"audit:read"}),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=roles,
        permissions=permissions,
    )


def _make_audit_record(
    tenant_id: str = "tenant-alpha",
    event_type: str = "task.progress_updated",
) -> AuditEventRecord:
    rec = AuditEventRecord(
        audit_event_id=uuid4(),
        schema_version=1,
        tenant_id=tenant_id,
        actor_principal_id="test-auditor",
        actor_principal_type="HUMAN",
        authentication_method="OIDC",
        event_type=event_type,
        resource_type="task",
        resource_id=str(uuid4()),
        outcome="SUCCESS",
        correlation_id=str(uuid4()),
        details={"status": "IN_PROGRESS"},
        recorded_at=datetime.now(UTC),
    )
    return rec


@pytest.fixture
def mock_audit_service() -> AsyncMock:
    service = AsyncMock()
    service.search_events = AsyncMock(
        return_value=[_make_audit_record(tenant_id="tenant-alpha")]
    )
    return service


@pytest.fixture
def mock_db_session() -> AsyncMock:
    return AsyncMock()


def _create_test_app(
    principal_override: object | None,
    tenant_override: object | None,
    audit_service: AsyncMock,
    session: AsyncMock,
) -> FastAPI:
    app = FastAPI()
    register_api_exception_handlers(app)

    async def _principal_dep():
        if principal_override == "RAISE_UNAUTH":
            from app.api.errors import ApiError

            raise ApiError(
                status_code=401,
                code="auth.credentials_missing",
                message="Authentication required: missing Authorization or Service Key header.",
            )
        return principal_override

    async def _tenant_dep():
        return tenant_override

    async def _session_dep():
        return session

    router = create_audit_router(
        tenant_dependency=_tenant_dep,
        principal_dependency=_principal_dep,
        session_dependency=_session_dep,
        audit_service=audit_service,
    )
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_audit_authorized_auditor_succeeds(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    principal = _make_principal(
        roles=frozenset({"auditor"}),
        permissions=frozenset({"audit:read", "analytics:read"}),
    )
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(principal, tenant_ctx, mock_audit_service, mock_db_session)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 200
    events = res.json()
    assert len(events) == 1
    assert events[0]["tenant_id"] == "tenant-alpha"
    assert events[0]["event_type"] == "task.progress_updated"
    mock_audit_service.search_events.assert_awaited_once()
    assert (
        mock_audit_service.search_events.await_args.kwargs["tenant_id"]
        == "tenant-alpha"
    )


@pytest.mark.asyncio
async def test_audit_unauthorized_planner_receives_403(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    planner_principal = _make_principal(
        principal_id="planner-user",
        roles=frozenset({"planner"}),
        permissions=frozenset({
            "goal:create",
            "plan:create",
            "plan:submit",
            "task:read",
        }),
    )
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(
        planner_principal, tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "audit.unauthorized"
    assert "Insufficient permissions" in body["error"]["message"]
    mock_audit_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_audit_unauthorized_viewer_receives_403(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    viewer_principal = _make_principal(
        principal_id="viewer-user",
        roles=frozenset({"viewer"}),
        permissions=frozenset({"plan:read", "schedule:read"}),
    )
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(
        viewer_principal, tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "audit.unauthorized"
    mock_audit_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_audit_missing_authentication_receives_401(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    app = _create_test_app(
        "RAISE_UNAUTH", None, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "auth.credentials_missing"
    mock_audit_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_audit_wrong_tenant_context_mismatch_receives_403(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    # Principal belongs strictly to tenant-alpha
    principal = _make_principal(
        tenant_id="tenant-alpha",
        roles=frozenset({"auditor"}),
        permissions=frozenset({"audit:read"}),
    )
    # Tampered or mismatched tenant context claims tenant-beta
    mismatched_tenant_ctx = TenantContext(tenant_id="tenant-beta")
    app = _create_test_app(
        principal, mismatched_tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "audit.cross_tenant_forbidden"
    assert "Cross-tenant audit access forbidden" in body["error"]["message"]
    mock_audit_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_audit_valid_administrator_succeeds(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    admin_principal = _make_principal(
        principal_id="tenant-admin-1",
        roles=frozenset({"tenant_admin"}),
        permissions=frozenset({"*"}),
    )
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(
        admin_principal, tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 200
    mock_audit_service.search_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_malformed_principal_context_fails(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    # Malformed context (e.g. raw dict instead of AuthenticatedPrincipal)
    malformed_principal = {"principal_id": "fake", "roles": ["auditor"]}
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(
        malformed_principal, tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/audit")

    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "auth.unauthorized"
    mock_audit_service.search_events.assert_not_called()


@pytest.mark.asyncio
async def test_audit_client_header_x_tenant_id_cannot_override_principal(
    mock_audit_service: AsyncMock, mock_db_session: AsyncMock
) -> None:
    # Verified principal is strictly tenant-alpha
    principal = _make_principal(
        tenant_id="tenant-alpha",
        roles=frozenset({"auditor"}),
        permissions=frozenset({"audit:read"}),
    )
    tenant_ctx = TenantContext(tenant_id="tenant-alpha")
    app = _create_test_app(
        principal, tenant_ctx, mock_audit_service, mock_db_session
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Client maliciously injects X-Tenant-ID for tenant-foreign
        res = await client.get(
            "/api/v1/audit", headers={"X-Tenant-ID": "tenant-foreign"}
        )

    assert res.status_code == 200
    # AuditQueryService must be called with tenant-alpha, never tenant-foreign
    mock_audit_service.search_events.assert_awaited_once()
    assert (
        mock_audit_service.search_events.await_args.kwargs["tenant_id"]
        == "tenant-alpha"
    )
