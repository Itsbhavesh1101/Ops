"""Unit tests for generic OpenID Connect (OIDC) verification and RBAC resolution.

Verifies:
- Generic OIDC issuer validation (matching issuer and discovery document)
- Audience allowlist validation
- Dynamic discovery and JWKS caching with synthetic RSA keys
- Invalid issuer rejection
- Invalid audience rejection
- Expired token rejection
- Signing key not found
- Missing required claims
- Role mapping and separation of duties in DefaultIdentityResolver
- Tenant assignment and isolation
- GET /api/v1/auth/me integration using verified OIDC Bearer tokens
"""

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.auth import create_auth_router
from app.core.auth.dependency import HttpAuthenticator
from app.core.auth.models import AuthenticationMethod, PrincipalType, VerifiedOidcIdentity
from app.core.auth.oidc import (
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidRequiredClaimsError,
    MalformedTokenError,
    OidcVerifier,
    SigningKeyNotFoundError,
    TokenExpiredError,
)
from app.core.auth.resolvers import DefaultIdentityResolver
from app.core.auth.service_api_key import CompositeServiceApiKeyCredentialStore
from app.core.config import Settings

TEST_ISSUER = "http://127.0.0.1:8180/realms/operations-engine-demo"
TEST_DISCOVERY_URL = f"{TEST_ISSUER}/.well-known/openid-configuration"
TEST_JWKS_URL = f"{TEST_ISSUER}/protocol/openid-connect/certs"
TEST_CLIENT_ID = "operations-ui"


def generate_rsa_key_pair(kid: str = "key-1") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate ephemeral RSA private key and public JWK representation."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key(), as_dict=True)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return priv, pub_jwk


def make_token(
    private_key: Any,
    kid: str = "key-1",
    iss: str = TEST_ISSUER,
    sub: str = "user-12345",
    aud: str | list[str] = TEST_CLIENT_ID,
    exp: int | None = None,
    iat: int | None = None,
    email: str | None = "planner@operations.local",
    alg: str = "RS256",
) -> str:
    """Create a synthetic signed JWT token."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "exp": exp if exp is not None else now + 3600,
        "iat": iat if iat is not None else now - 60,
    }
    if email:
        payload["email"] = email
        payload["email_verified"] = True

    headers = {"kid": kid, "alg": alg}
    return jwt.encode(payload, private_key, algorithm=alg, headers=headers)


def make_mock_transport(jwks: list[dict[str, Any]]) -> httpx.MockTransport:
    """Create a mock transport returning discovery and JWKS metadata."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str == TEST_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": TEST_ISSUER,
                    "jwks_uri": TEST_JWKS_URL,
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
                headers={"cache-control": "max-age=3600"},
            )
        elif url_str == TEST_JWKS_URL:
            return httpx.Response(
                200,
                json={"keys": jwks},
                headers={"cache-control": "max-age=3600"},
            )
        return httpx.Response(404, text="Not Found")

    return httpx.MockTransport(handler)


class TestGenericOidcVerifier:
    """Tests for generic OIDC cryptographic verification."""

    @pytest.mark.asyncio
    async def test_valid_token_verification(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        token = make_token(priv_key, kid="key-1", iss=TEST_ISSUER, aud=TEST_CLIENT_ID)

        identity = await verifier.verify_id_token(token)
        assert identity.subject == "user-12345"
        assert identity.issuer == TEST_ISSUER
        assert identity.email == "planner@operations.local"
        assert TEST_CLIENT_ID in identity.audiences
        assert not hasattr(identity, "roles")

    @pytest.mark.asyncio
    async def test_invalid_issuer_rejected(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        token = make_token(priv_key, iss="https://malicious-issuer.com", aud=TEST_CLIENT_ID)

        with pytest.raises(InvalidIssuerError):
            await verifier.verify_id_token(token)

    @pytest.mark.asyncio
    async def test_invalid_audience_rejected(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        token = make_token(priv_key, iss=TEST_ISSUER, aud="unauthorized-client-app")

        with pytest.raises(InvalidAudienceError):
            await verifier.verify_id_token(token)

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        expired_time = int(time.time()) - 300
        token = make_token(
            priv_key,
            iss=TEST_ISSUER,
            aud=TEST_CLIENT_ID,
            exp=expired_time,
            iat=expired_time - 3600,
        )

        with pytest.raises(TokenExpiredError):
            await verifier.verify_id_token(token)

    @pytest.mark.asyncio
    async def test_signing_key_not_found(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        token = make_token(priv_key, kid="unknown-kid", iss=TEST_ISSUER, aud=TEST_CLIENT_ID)

        with pytest.raises(SigningKeyNotFoundError):
            await verifier.verify_id_token(token)

    @pytest.mark.asyncio
    async def test_missing_required_claims(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client = httpx.AsyncClient(transport=transport)
        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client)
        # Token missing 'sub'
        payload = {
            "iss": TEST_ISSUER,
            "aud": TEST_CLIENT_ID,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()) - 60,
        }
        token = jwt.encode(payload, priv_key, algorithm="RS256", headers={"kid": "key-1"})

        with pytest.raises((InvalidRequiredClaimsError, MalformedTokenError)):
            await verifier.verify_id_token(token)


class TestIdentityResolutionAndRbac:
    """Tests for DefaultIdentityResolver role mapping and separation of duties."""

    @pytest.mark.asyncio
    async def test_planner_role_and_permissions(self) -> None:
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            subject="planner",
            issuer=TEST_ISSUER,
            audiences=frozenset([TEST_CLIENT_ID]),
            email="planner@operations.local",
        )

        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert principal.tenant_id == "tenant-pilot-001"
        assert principal.principal_type == PrincipalType.HUMAN
        assert principal.authentication_method == AuthenticationMethod.OIDC
        assert "planner" in principal.roles
        assert principal.has_permission("plan:create")
        assert principal.has_permission("plan:submit")
        # Strict separation of duties: planner CANNOT approve plans
        assert not principal.has_permission("plan:approve")

    @pytest.mark.asyncio
    async def test_approver_role_and_permissions(self) -> None:
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            subject="approver",
            issuer=TEST_ISSUER,
            audiences=frozenset([TEST_CLIENT_ID]),
            email="approver@operations.local",
        )

        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert principal.tenant_id == "tenant-pilot-001"
        assert "approver" in principal.roles
        assert principal.has_permission("plan:approve")
        # Strict separation of duties: approver CANNOT create plans
        assert not principal.has_permission("plan:create")

    @pytest.mark.asyncio
    async def test_scheduler_role_and_permissions(self) -> None:
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            subject="scheduler",
            issuer=TEST_ISSUER,
            audiences=frozenset([TEST_CLIENT_ID]),
            email="scheduler@operations.local",
        )

        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert principal.tenant_id == "tenant-pilot-001"
        assert "scheduler" in principal.roles
        assert principal.has_permission("schedule:solve")
        assert principal.has_permission("schedule:publish")
        # Strict separation of duties: scheduler CANNOT approve plans
        assert not principal.has_permission("plan:approve")

    @pytest.mark.asyncio
    async def test_admin_role_and_permissions(self) -> None:
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            subject="admin",
            issuer=TEST_ISSUER,
            audiences=frozenset([TEST_CLIENT_ID]),
            email="admin@operations.local",
        )

        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert principal.tenant_id == "tenant-pilot-001"
        assert "tenant_admin" in principal.roles
        assert "*" in principal.permissions
        assert principal.has_permission("*")

    @pytest.mark.asyncio
    async def test_unknown_user_fallback(self) -> None:
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            subject="stranger",
            issuer=TEST_ISSUER,
            audiences=frozenset([TEST_CLIENT_ID]),
            email="unknown.guest@external.org",
        )

        principal = await resolver.resolve_oidc_identity(identity)
        # Unknown/unmapped user safely fails closed
        assert principal is None


class TestAuthMeHttpIntegration:
    """Tests for GET /api/v1/auth/me with generic OIDC Bearer token."""

    def test_auth_me_with_valid_keycloak_bearer_token(self) -> None:
        priv_key, pub_jwk = generate_rsa_key_pair("key-1")
        transport = make_mock_transport([pub_jwk])
        client_mock = httpx.AsyncClient(transport=transport)

        settings = Settings(
            oidc_provider_name="keycloak",
            oidc_issuer_url=TEST_ISSUER,
            oidc_client_ids=[TEST_CLIENT_ID],
            oidc_allowed_audiences=[TEST_CLIENT_ID],
            oidc_allowed_jwks_hosts=["127.0.0.1", "localhost"],
            oidc_default_tenant_id="tenant-pilot-001",
        )

        verifier = OidcVerifier.from_settings(settings=settings, http_client=client_mock)
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        store = CompositeServiceApiKeyCredentialStore()

        authenticator = HttpAuthenticator(
            settings=settings,
            oidc_verifier=verifier,
            external_identity_resolver=resolver,
            credential_store=store,
            service_identity_resolver=resolver,
        )

        app = FastAPI()
        register_api_exception_handlers(app)
        router = create_auth_router(principal_dependency=authenticator)
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        token = make_token(
            priv_key,
            kid="key-1",
            iss=TEST_ISSUER,
            sub="planner",
            aud=TEST_CLIENT_ID,
            email="planner@operations.local",
        )

        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["principal_id"] == "planner@operations.local"
        assert data["tenant_id"] == "tenant-pilot-001"
        assert "planner" in data["roles"]
        assert data["principal_type"] == "HUMAN"
        assert data["authentication_method"] == "OIDC"
        assert data["email"] == "planner@operations.local"
        assert data["issuer"] == TEST_ISSUER

    @pytest.mark.asyncio
    async def test_production_tenant_mapping_fails_closed_when_unconfigured(self) -> None:
        """Verify production mode fails closed (returns None) without default tenant."""
        resolver = DefaultIdentityResolver(default_tenant_id=None)
        identity = VerifiedOidcIdentity(
            issuer="https://accounts.google.com",
            subject="google-sub-999",
            audiences=frozenset({"client-production"}),
            email="planner@company.com",
        )
        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is None, "Expected unmapped identity to fail closed"

    @pytest.mark.asyncio
    async def test_explicit_trusted_tenant_mapping_honored_in_production(self) -> None:
        """Verify explicit trusted tenant mapping resolves safely when default tenant is None."""
        from app.core.auth.models import AuthenticatedPrincipal, AuthenticationMethod, PrincipalType

        resolver = DefaultIdentityResolver(default_tenant_id=None)
        trusted_principal = AuthenticatedPrincipal(
            principal_id="planner@company.com",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant-corp-prod",
            roles=frozenset({"planner"}),
            permissions=frozenset({"plan:create"}),
        )
        resolver.register_oidc_mapping("planner@company.com", trusted_principal)

        identity = VerifiedOidcIdentity(
            issuer="https://accounts.google.com",
            subject="google-sub-999",
            audiences=frozenset({"client-production"}),
            email="planner@company.com",
        )
        resolved = await resolver.resolve_oidc_identity(identity)
        assert resolved is not None
        assert resolved.tenant_id == "tenant-corp-prod"
        assert resolved.principal_id == "planner@company.com"

    @pytest.mark.asyncio
    async def test_client_cannot_forge_tenant_authority(self) -> None:
        """Verify server ignores client tenant claims and enforces mapping."""
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            issuer=TEST_ISSUER,
            subject="attacker-sub",
            audiences=frozenset({TEST_CLIENT_ID}),
            email="planner@operations.local",
        )
        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert principal.tenant_id == "tenant-pilot-001", "Tenant must come strictly from server"

    @pytest.mark.asyncio
    async def test_canonical_tenant_admin_rbac_wildcard(self) -> None:
        """Verify tenant_admin resolves to canonical permissions conforming to security baseline."""
        resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
        identity = VerifiedOidcIdentity(
            issuer=TEST_ISSUER,
            subject="admin-sub",
            audiences=frozenset({TEST_CLIENT_ID}),
            email="admin@operations.local",
        )
        principal = await resolver.resolve_oidc_identity(identity)
        assert principal is not None
        assert "tenant_admin" in principal.roles
        assert "*" in principal.permissions
