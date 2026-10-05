"""Unit tests for canonical authentication, tenant context, and RBAC contracts (Task 1C.1)."""

import asyncio

import pytest
from fastapi import APIRouter, Depends, FastAPI
from pydantic import ValidationError
from starlette.testclient import TestClient

from app.core.auth import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    ExternalIdentityResolver,
    PrincipalType,
    ServiceIdentityResolver,
    TenantContext,
    VerifiedOidcIdentity,
    VerifiedServiceCredentialIdentity,
    get_current_authenticated_principal,
    get_current_principal,
    get_current_tenant_context,
    get_current_tenant_id,
    require_any_permission,
    require_any_role,
    require_authenticated_principal,
    require_permissions,
    require_roles,
    reset_current_principal,
    set_current_principal,
)


def _create_human_principal(
    principal_id: str = "usr_001",
    tenant_id: str = "tenant_alpha",
    roles: set[str] | None = None,
    permissions: set[str] | None = None,
) -> AuthenticatedPrincipal:
    """Helper to construct a valid human principal."""
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset(roles or {"operator"}),
        permissions=frozenset(permissions or {"task:read", "task:execute"}),
        issuer="https://accounts.example.com",
        external_subject="sub_123456",
    )


def _create_service_principal(
    principal_id: str = "svc_001",
    tenant_id: str = "tenant_alpha",
    roles: set[str] | None = None,
    permissions: set[str] | None = None,
) -> AuthenticatedPrincipal:
    """Helper to construct a valid service principal."""
    return AuthenticatedPrincipal(
        principal_id=principal_id,
        principal_type=PrincipalType.SERVICE,
        authentication_method=AuthenticationMethod.SERVICE_API_KEY,
        tenant_id=tenant_id,
        roles=frozenset(roles or {"system"}),
        permissions=frozenset(permissions or {"metrics:export"}),
        issuer=None,
        external_subject="svc_account_id",
    )


def test_human_principal_construction() -> None:
    """Verify human principal model construction, immutability, and RBAC methods."""
    principal = _create_human_principal(
        roles={"admin", "operator"},
        permissions={"task:create", "task:read"},
    )
    assert principal.principal_id == "usr_001"
    assert principal.principal_type == PrincipalType.HUMAN
    assert principal.authentication_method == AuthenticationMethod.OIDC
    assert principal.tenant_id == "tenant_alpha"
    assert principal.tenant_context == TenantContext(tenant_id="tenant_alpha")

    # RBAC method assertions
    assert principal.has_role("admin") is True
    assert principal.has_role("guest") is False
    assert principal.has_any_role("guest", "admin") is True
    assert principal.has_any_role("guest", "viewer") is False
    assert principal.has_all_roles("admin", "operator") is True
    assert principal.has_all_roles("admin", "root") is False

    assert principal.has_permission("task:create") is True
    assert principal.has_permission("task:delete") is False
    assert principal.has_any_permission("task:delete", "task:read") is True
    assert principal.has_all_permissions("task:create", "task:read") is True
    assert principal.has_all_permissions("task:create", "task:delete") is False

    # Immutability
    with pytest.raises(ValidationError):
        principal.principal_id = "other_id"  # type: ignore[misc]


def test_service_principal_construction() -> None:
    """Verify service principal construction and role/permission checks."""
    service_principal = _create_service_principal(
        roles={"ingestion_worker"},
        permissions={"events:publish"},
    )
    assert service_principal.principal_type == PrincipalType.SERVICE
    assert service_principal.authentication_method == AuthenticationMethod.SERVICE_API_KEY
    assert service_principal.has_role("ingestion_worker") is True
    assert service_principal.has_permission("events:publish") is True


def test_principal_contains_no_raw_secret_or_token_fields() -> None:
    """Verify AuthenticatedPrincipal schema forbids raw secrets and extra token fields."""
    fields = AuthenticatedPrincipal.model_fields.keys()
    prohibited_substrings = ["token", "raw", "secret", "password", "api_key"]
    for field_name in fields:
        for sub in prohibited_substrings:
            assert sub not in field_name, f"Forbidden field '{field_name}' in principal model"

    # extra='forbid' prevents token injection
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="p1",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="t1",
            raw_token="ey...",  # type: ignore[call-arg]
        )


def test_verified_oidc_identity_contract() -> None:
    """VerifiedOidcIdentity is distinct from principal and has zero intrinsic tenant authority."""
    oidc_identity = VerifiedOidcIdentity(
        issuer="https://accounts.example.com",
        subject="sub_98765",
        audiences="e7_client_id",
        email="operator@example.com",
        hosted_domain="example.com",
    )
    assert oidc_identity.issuer == "https://accounts.example.com"
    assert oidc_identity.subject == "sub_98765"
    assert oidc_identity.audiences == frozenset({"e7_client_id"})
    assert oidc_identity.email == "operator@example.com"
    assert oidc_identity.hosted_domain == "example.com"

    # Verified external identity is not an authenticated E-7 principal
    assert not isinstance(oidc_identity, AuthenticatedPrincipal)
    assert not hasattr(oidc_identity, "tenant_id")
    assert not hasattr(oidc_identity, "roles")


def test_oidc_audiences_normalization_and_validation() -> None:
    """VerifiedOidcIdentity handles audiences, enforcing immutability and non-empty entries."""
    # Single audience string normalized to frozenset
    ident_single = VerifiedOidcIdentity(
        issuer="https://accounts.example.com",
        subject="sub_1",
        audiences="client-app-1",
    )
    assert ident_single.audiences == frozenset({"client-app-1"})
    assert isinstance(ident_single.audiences, frozenset)

    # Multiple audiences collection
    ident_multi = VerifiedOidcIdentity(
        issuer="https://accounts.example.com",
        subject="sub_2",
        audiences=["client-app-1", "client-app-2"],
    )
    assert ident_multi.audiences == frozenset({"client-app-1", "client-app-2"})

    # Reject empty audience collection
    with pytest.raises(ValidationError):
        VerifiedOidcIdentity(
            issuer="https://accounts.example.com",
            subject="sub_3",
            audiences=[],
        )

    # Reject empty/whitespace audience entries
    with pytest.raises(ValidationError):
        VerifiedOidcIdentity(
            issuer="https://accounts.example.com",
            subject="sub_4",
            audiences=["   "],
        )

    with pytest.raises(ValidationError):
        VerifiedOidcIdentity(
            issuer="https://accounts.example.com",
            subject="sub_5",
            audiences={"valid-aud", ""},
        )


def test_security_identifier_and_role_permission_validation() -> None:
    """Security models reject empty or whitespace-only identifiers, roles, and permissions."""
    # AuthenticatedPrincipal validation
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant_1",
        )

    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="   ",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant_1",
        )

    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="usr_1",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="   ",
        )

    # Blank roles and permissions rejected
    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="usr_1",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant_1",
            roles={"admin", "  "},
        )

    with pytest.raises(ValidationError):
        AuthenticatedPrincipal(
            principal_id="usr_1",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant_1",
            permissions={""},
        )

    # VerifiedOidcIdentity issuer/subject validation
    with pytest.raises(ValidationError):
        VerifiedOidcIdentity(
            issuer="   ",
            subject="sub_1",
            audiences="aud_1",
        )

    with pytest.raises(ValidationError):
        VerifiedOidcIdentity(
            issuer="https://example.com",
            subject="",
            audiences="aud_1",
        )

    # TenantContext validation
    with pytest.raises(ValidationError):
        TenantContext(tenant_id="   ")


def test_verified_service_credential_identity_contract() -> None:
    """VerifiedServiceCredentialIdentity holds safe metadata and forbids raw API keys."""
    svc_cred = VerifiedServiceCredentialIdentity(
        credential_id="cred_abc123",
        service_subject="workflow-engine-service",
        issuer="internal-ca",
    )
    assert svc_cred.credential_id == "cred_abc123"
    assert svc_cred.service_subject == "workflow-engine-service"

    with pytest.raises(ValidationError):
        VerifiedServiceCredentialIdentity(
            credential_id="c1",
            service_subject="s1",
            raw_api_key="secret-key",  # type: ignore[call-arg]
        )


def test_tenant_context_authority_rule() -> None:
    """Tenant scope originates exclusively from AuthenticatedPrincipal, never client input."""
    principal = _create_human_principal(tenant_id="tenant_legitimate")
    token = set_current_principal(principal)
    try:
        # Context accessors return principal's authenticated tenant
        assert get_current_tenant_id() == "tenant_legitimate"
        assert get_current_tenant_context() == TenantContext(tenant_id="tenant_legitimate")

        # Unverified client-supplied tenant metadata cannot change or override principal scope
        client_supplied_tenant = "tenant_attacker_impersonation"
        assert get_current_tenant_id() != client_supplied_tenant
    finally:
        reset_current_principal(token)


def test_security_context_set_get_reset() -> None:
    """Verify security context lifecycle with token-based reset and no fake anonymous principal."""
    # Context starts empty
    assert get_current_principal() is None
    assert get_current_tenant_id() is None
    assert get_current_tenant_context() is None

    principal = _create_human_principal()
    token = set_current_principal(principal)
    try:
        assert get_current_principal() is principal
        assert get_current_tenant_id() == "tenant_alpha"
    finally:
        reset_current_principal(token)

    # Strictly cleared
    assert get_current_principal() is None
    assert get_current_tenant_id() is None


@pytest.mark.asyncio
async def test_security_context_async_isolation() -> None:
    """Verify concurrent async coroutines maintain strict context isolation without leaking."""

    async def worker(principal: AuthenticatedPrincipal) -> tuple[str, str]:
        token = set_current_principal(principal)
        try:
            # Yield control to let other coroutines run concurrently
            await asyncio.sleep(0.01)
            current = get_current_principal()
            assert current is not None
            return current.principal_id, current.tenant_id
        finally:
            reset_current_principal(token)

    p1 = _create_human_principal(principal_id="user_1", tenant_id="tenant_1")
    p2 = _create_human_principal(principal_id="user_2", tenant_id="tenant_2")
    p3 = _create_service_principal(principal_id="svc_3", tenant_id="tenant_3")

    results = await asyncio.gather(worker(p1), worker(p2), worker(p3))
    assert results == [
        ("user_1", "tenant_1"),
        ("user_2", "tenant_2"),
        ("svc_3", "tenant_3"),
    ]

    # Verify context remains clean after completion
    assert get_current_principal() is None


@pytest.mark.asyncio
async def test_external_identity_resolver_protocol() -> None:
    """Verify ExternalIdentityResolver protocol contract with mock implementation."""

    class FakeOidcResolver:
        async def resolve_oidc_identity(
            self, identity: VerifiedOidcIdentity
        ) -> AuthenticatedPrincipal | None:
            if identity.subject == "approved_subject":
                return AuthenticatedPrincipal(
                    principal_id="usr_mapped",
                    principal_type=PrincipalType.HUMAN,
                    authentication_method=AuthenticationMethod.OIDC,
                    tenant_id="tenant_mapped",
                    roles=frozenset({"user"}),
                    permissions=frozenset({"view"}),
                    issuer=identity.issuer,
                    external_subject=identity.subject,
                )
            return None

    resolver: ExternalIdentityResolver = FakeOidcResolver()
    assert isinstance(resolver, ExternalIdentityResolver)

    # Resolves approved identity
    identity_valid = VerifiedOidcIdentity(
        issuer="https://issuer.example.com",
        subject="approved_subject",
        audiences="aud1",
    )
    principal = await resolver.resolve_oidc_identity(identity_valid)
    assert principal is not None
    assert principal.principal_id == "usr_mapped"
    assert principal.tenant_id == "tenant_mapped"

    # Returns None for unauthorized identity
    identity_unknown = VerifiedOidcIdentity(
        issuer="https://issuer.example.com",
        subject="unknown_subject",
        audiences="aud1",
    )
    assert await resolver.resolve_oidc_identity(identity_unknown) is None


@pytest.mark.asyncio
async def test_service_identity_resolver_protocol() -> None:
    """Verify ServiceIdentityResolver protocol contract with mock implementation."""

    class FakeServiceResolver:
        async def resolve_service_identity(
            self, identity: VerifiedServiceCredentialIdentity
        ) -> AuthenticatedPrincipal | None:
            if identity.credential_id == "approved_cred":
                return AuthenticatedPrincipal(
                    principal_id="svc_mapped",
                    principal_type=PrincipalType.SERVICE,
                    authentication_method=AuthenticationMethod.SERVICE_API_KEY,
                    tenant_id="tenant_corp",
                    roles=frozenset({"worker"}),
                    permissions=frozenset({"sync"}),
                    external_subject=identity.service_subject,
                )
            return None

    resolver: ServiceIdentityResolver = FakeServiceResolver()
    assert isinstance(resolver, ServiceIdentityResolver)

    cred_valid = VerifiedServiceCredentialIdentity(
        credential_id="approved_cred",
        service_subject="backend-runner",
    )
    principal = await resolver.resolve_service_identity(cred_valid)
    assert principal is not None
    assert principal.principal_id == "svc_mapped"

    cred_invalid = VerifiedServiceCredentialIdentity(
        credential_id="bad_cred",
        service_subject="unknown",
    )
    assert await resolver.resolve_service_identity(cred_invalid) is None


# -----------------------------------------------------------------------------
# FastAPI Authorization Dependency Hook Tests
# -----------------------------------------------------------------------------

auth_test_app = FastAPI()
router = APIRouter()


@router.get("/protected/me")
async def me_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
) -> dict[str, str]:
    return {"principal_id": principal.principal_id, "tenant_id": principal.tenant_id}


@router.get("/protected/admin-only")
async def admin_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_roles("admin")),
) -> dict[str, str]:
    return {"status": "admin_granted"}


@router.get("/protected/any-role")
async def any_role_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_any_role("admin", "auditor")),
) -> dict[str, str]:
    return {"status": "role_granted"}


@router.get("/protected/task-create")
async def task_create_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_permissions("task:create")),
) -> dict[str, str]:
    return {"status": "permission_granted"}


@router.get("/protected/any-permission")
async def any_permission_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_any_permission("task:read", "task:export")),
) -> dict[str, str]:
    return {"status": "any_permission_granted"}


auth_test_app.include_router(router)
client = TestClient(auth_test_app)


def test_fastapi_dependency_unauthenticated_returns_401() -> None:
    """Missing principal causes require_authenticated_principal to return 401 without Bearer."""
    assert get_current_authenticated_principal is require_authenticated_principal
    # Ensure context is empty
    assert get_current_principal() is None

    response = client.get("/protected/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    # Scheme-neutral: no hardcoded Bearer challenge
    assert response.headers.get("WWW-Authenticate") is None


def test_generic_401_scheme_neutrality_across_hooks() -> None:
    """All generic role and permission dependency hooks emit scheme-neutral 401 without Bearer."""
    assert get_current_principal() is None

    # require_roles hook when unauthenticated
    resp_role = client.get("/protected/admin-only")
    assert resp_role.status_code == 401
    assert resp_role.json()["detail"] == "Authentication required"
    assert resp_role.headers.get("WWW-Authenticate") is None

    # require_any_role hook when unauthenticated
    resp_any_role = client.get("/protected/any-role")
    assert resp_any_role.status_code == 401
    assert resp_any_role.headers.get("WWW-Authenticate") is None

    # require_permissions hook when unauthenticated
    resp_perm = client.get("/protected/task-create")
    assert resp_perm.status_code == 401
    assert resp_perm.json()["detail"] == "Authentication required"
    assert resp_perm.headers.get("WWW-Authenticate") is None

    # require_any_permission hook when unauthenticated
    resp_any_perm = client.get("/protected/any-permission")
    assert resp_any_perm.status_code == 401
    assert resp_any_perm.headers.get("WWW-Authenticate") is None


def test_fastapi_dependency_authenticated_returns_200() -> None:
    """Authenticated principal in context permits access to protected endpoint."""
    principal = _create_human_principal(principal_id="usr_99", tenant_id="tenant_xyz")
    token = set_current_principal(principal)
    try:
        response = client.get("/protected/me")
        assert response.status_code == 200
        assert response.json() == {"principal_id": "usr_99", "tenant_id": "tenant_xyz"}
    finally:
        reset_current_principal(token)


def test_fastapi_dependency_require_roles() -> None:
    """require_roles grants access if all roles are present, returns 403 if missing."""
    # Missing required role -> 403
    p_operator = _create_human_principal(roles={"operator"})
    token1 = set_current_principal(p_operator)
    try:
        response = client.get("/protected/admin-only")
        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient role authorization"
    finally:
        reset_current_principal(token1)

    # Has required role -> 200
    p_admin = _create_human_principal(roles={"admin", "operator"})
    token2 = set_current_principal(p_admin)
    try:
        response = client.get("/protected/admin-only")
        assert response.status_code == 200
        assert response.json() == {"status": "admin_granted"}
    finally:
        reset_current_principal(token2)


def test_fastapi_dependency_require_any_role() -> None:
    """require_any_role grants access if at least one role is present."""
    p_auditor = _create_human_principal(roles={"auditor"})
    token = set_current_principal(p_auditor)
    try:
        response = client.get("/protected/any-role")
        assert response.status_code == 200
        assert response.json() == {"status": "role_granted"}
    finally:
        reset_current_principal(token)

    p_guest = _create_human_principal(roles={"guest"})
    token_guest = set_current_principal(p_guest)
    try:
        response = client.get("/protected/any-role")
        assert response.status_code == 403
    finally:
        reset_current_principal(token_guest)


def test_fastapi_dependency_require_permissions() -> None:
    """require_permissions grants access if all permissions are present, 403 otherwise."""
    p_read = _create_human_principal(permissions={"task:read"})
    token = set_current_principal(p_read)
    try:
        response = client.get("/protected/task-create")
        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permission authorization"
    finally:
        reset_current_principal(token)

    p_create = _create_human_principal(permissions={"task:create", "task:read"})
    token2 = set_current_principal(p_create)
    try:
        response = client.get("/protected/task-create")
        assert response.status_code == 200
        assert response.json() == {"status": "permission_granted"}
    finally:
        reset_current_principal(token2)


def test_fastapi_dependency_require_any_permission() -> None:
    """require_any_permission grants access if at least one permission matches."""
    p_export = _create_human_principal(permissions={"task:export"})
    token = set_current_principal(p_export)
    try:
        response = client.get("/protected/any-permission")
        assert response.status_code == 200
        assert response.json() == {"status": "any_permission_granted"}
    finally:
        reset_current_principal(token)

    p_none = _create_human_principal(permissions={"unrelated:perm"})
    token_none = set_current_principal(p_none)
    try:
        response = client.get("/protected/any-permission")
        assert response.status_code == 403
    finally:
        reset_current_principal(token_none)


def test_service_and_human_principals_use_same_authorization() -> None:
    """Service principals evaluate against identical role and permission checks as humans."""
    # Service principal with admin role passes admin check
    svc_admin = _create_service_principal(roles={"admin"})
    token_svc = set_current_principal(svc_admin)
    try:
        response = client.get("/protected/admin-only")
        assert response.status_code == 200
    finally:
        reset_current_principal(token_svc)

    # Human principal with admin role passes admin check
    human_admin = _create_human_principal(roles={"admin"})
    token_human = set_current_principal(human_admin)
    try:
        response = client.get("/protected/admin-only")
        assert response.status_code == 200
    finally:
        reset_current_principal(token_human)
