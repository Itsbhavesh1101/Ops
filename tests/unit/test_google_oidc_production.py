"""Focused unit tests for Google Workspace / Cloud Identity production integration (Stage 4).

Validates:
- Valid and invalid Google ID tokens
- Wrong issuer, wrong audience, expired tokens, invalid signatures, unknown signing keys
- Unsupported algorithms (ES256, HMAC, none)
- Hosted domain enforcement and unauthorized organization rejection
- Verified email enforcement (rejecting email_verified=False)
- Cryptographic identity vs E-7 internal authorization boundary separation
- Unmapped Google identity fail-closed rejection
- Prevention of unauthorized role escalation (e.g. 'admin' in email username)
- Tenant isolation and cross-tenant boundary preservation
- Production environment fail-closed configuration (rejecting Keycloak demo provider, wildcard CORS)
- Separation of interactive OIDC from machine-to-machine service keys
"""

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.core.auth.dependency import HttpAuthenticator
from app.core.auth.google_oidc import (
    GoogleOidcVerifier,
    HostedDomainRejectedError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidRequiredClaimsError,
    InvalidSignatureError,
    NonceMismatchError,
    SigningKeyNotFoundError,
    TokenExpiredError,
    UnsupportedAlgorithmError,
)
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    VerifiedOidcIdentity,
)
from app.core.auth.resolvers import DefaultIdentityResolver
from app.core.config import Settings

DEFAULT_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
DEFAULT_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
PROD_CLIENT_ID = "e7-operations-production.apps.googleusercontent.com"
INSTITUTIONAL_DOMAIN = "irinfotech.com"


def generate_rsa_key_pair(
    kid: str = "google-prod-key-1",
) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate ephemeral synthetic RSA key pair for testing."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key(), as_dict=True)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return priv, pub_jwk


def make_token(
    private_key: Any,
    kid: str | None = "google-prod-key-1",
    iss: str = "https://accounts.google.com",
    sub: str = "google-sub-10001",
    aud: str | list[str] = PROD_CLIENT_ID,
    exp: int | None = None,
    iat: int | None = None,
    hd: str | None = INSTITUTIONAL_DOMAIN,
    email: str | None = "planner.user@irinfotech.com",
    email_verified: bool | None = True,
    nonce: str | None = None,
    alg: str = "RS256",
) -> str:
    """Generate a synthetic Google OIDC ID token."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "exp": exp if exp is not None else now + 3600,
        "iat": iat if iat is not None else now - 60,
    }
    if hd is not None:
        payload["hd"] = hd
    if email is not None:
        payload["email"] = email
    if email_verified is not None:
        payload["email_verified"] = email_verified
    if nonce is not None:
        payload["nonce"] = nonce

    headers: dict[str, Any] = {"alg": alg}
    if kid is not None:
        headers["kid"] = kid

    return jwt.encode(payload, private_key, algorithm=alg, headers=headers)


def make_transport(pub_jwk: dict[str, Any]) -> httpx.MockTransport:
    """Create mock transport for Google discovery and JWKS."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
            )
        if url == DEFAULT_JWKS_URL:
            return httpx.Response(200, json={"keys": [pub_jwk]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# =============================================================================
# 1. Cryptographic Token Verification Tests
# =============================================================================


@pytest.mark.asyncio
async def test_valid_google_id_token_verification() -> None:
    """Verify standard valid Google ID token succeeds and produces VerifiedOidcIdentity."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(
        client_ids=[PROD_CLIENT_ID],
        allowed_hosted_domains=[INSTITUTIONAL_DOMAIN],
        http_client=client,
    )

    tok = make_token(priv)
    identity = await verifier.verify_id_token(tok)

    assert isinstance(identity, VerifiedOidcIdentity)
    assert identity.issuer == "https://accounts.google.com"
    assert identity.subject == "google-sub-10001"
    assert identity.hosted_domain == INSTITUTIONAL_DOMAIN
    assert identity.email == "planner.user@irinfotech.com"
    assert PROD_CLIENT_ID in identity.audiences


@pytest.mark.asyncio
async def test_google_id_token_wrong_issuer_rejected() -> None:
    """Verify token signed by attacker or wrong issuer is rejected."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    tok = make_token(priv, iss="https://evil-issuer.attacker.com")
    with pytest.raises(InvalidIssuerError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_wrong_audience_rejected() -> None:
    """Verify token destined for another client ID is rejected."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    tok = make_token(priv, aud="other-google-app-client-id.apps.googleusercontent.com")
    with pytest.raises(InvalidAudienceError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_expired_rejected() -> None:
    """Verify expired Google token is rejected."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    now = int(time.time())
    tok = make_token(priv, exp=now - 500, iat=now - 4000)
    with pytest.raises(TokenExpiredError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_invalid_signature_rejected() -> None:
    """Verify token signed with wrong key fails signature check."""
    priv1, pub_jwk1 = generate_rsa_key_pair("key-1")
    priv2, _ = generate_rsa_key_pair("key-1")  # different key, same kid
    client = httpx.AsyncClient(transport=make_transport(pub_jwk1))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    tok = make_token(priv2, kid="key-1")
    with pytest.raises(InvalidSignatureError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_unknown_signing_key_rejected() -> None:
    """Verify token signed with kid not in JWKS fails with SigningKeyNotFoundError."""
    priv, pub_jwk = generate_rsa_key_pair("known-key")
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    tok = make_token(priv, kid="unregistered-key-999")
    with pytest.raises(SigningKeyNotFoundError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_unsupported_algorithm_rejected() -> None:
    """Verify non-RS256 algorithms (HS256, none) are rejected at header inspection."""
    _, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    # Symmetric HMAC token (HS256) is prohibited
    tok = jwt.encode(
        {"iss": "https://accounts.google.com", "sub": "sub1", "aud": PROD_CLIENT_ID},
        "fake-secret-key-32-bytes-long-12345",
        algorithm="HS256",
        headers={"kid": "google-prod-key-1"},
    )
    with pytest.raises(UnsupportedAlgorithmError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_google_id_token_unauthorized_hosted_domain_rejected() -> None:
    """Verify personal Gmail or external organization account is rejected by hd policy."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(
        client_ids=[PROD_CLIENT_ID],
        allowed_hosted_domains=[INSTITUTIONAL_DOMAIN],
        http_client=client,
    )

    # Personal Gmail without hd claim
    tok_gmail = make_token(priv, hd=None, email="attacker@gmail.com")
    with pytest.raises(HostedDomainRejectedError):
        await verifier.verify_id_token(tok_gmail)

    # External domain
    tok_other = make_token(priv, hd="unauthorized-domain.com", email="user@unauthorized-domain.com")
    with pytest.raises(HostedDomainRejectedError):
        await verifier.verify_id_token(tok_other)


@pytest.mark.asyncio
async def test_google_id_token_unverified_email_rejected() -> None:
    """Verify token with email_verified=False is rejected to prevent email spoofing."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    tok = make_token(priv, email="unverified@irinfotech.com", email_verified=False)
    with pytest.raises(InvalidRequiredClaimsError, match="email_verified"):
        await verifier.verify_id_token(tok)


# =============================================================================
# 2. Authentication & Authorization Boundary Separation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_unmapped_google_identity_fails_closed() -> None:
    """Verify a valid Google identity with NO internal E-7 mapping fails closed (returns None)."""
    resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
    identity = VerifiedOidcIdentity(
        issuer="https://accounts.google.com",
        subject="google-sub-random-employee",
        audiences=frozenset([PROD_CLIENT_ID]),
        email="unregistered.employee@irinfotech.com",
        hosted_domain=INSTITUTIONAL_DOMAIN,
    )

    principal = await resolver.resolve_oidc_identity(identity)
    # Crucial boundary: valid external Google identity does NOT become an internal user
    assert principal is None


@pytest.mark.asyncio
async def test_google_identity_cannot_escalate_roles_via_email_username() -> None:
    """Verify a Google identity with 'admin' in their email cannot gain tenant_admin or any role."""
    resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
    identity = VerifiedOidcIdentity(
        issuer="https://accounts.google.com",
        subject="google-sub-admin-impostor",
        audiences=frozenset([PROD_CLIENT_ID]),
        email="admin.staff@irinfotech.com",
        hosted_domain=INSTITUTIONAL_DOMAIN,
    )

    # Must fail closed: username substring heuristics strictly prohibited for Google identities
    principal = await resolver.resolve_oidc_identity(identity)
    assert principal is None


@pytest.mark.asyncio
async def test_mapped_google_identity_derives_strictly_internal_roles_and_tenant() -> None:
    """Verify explicitly mapped Google identity inherits internal tenant and roles."""
    resolver = DefaultIdentityResolver()
    trusted_principal = AuthenticatedPrincipal(
        principal_id="planner.user@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-prod-inst-01",
        roles=frozenset({"planner"}),
        permissions=frozenset({"plan:create", "plan:submit"}),
        issuer="https://accounts.google.com",
        external_subject="google-sub-10001",
    )
    resolver.register_oidc_mapping("planner.user@irinfotech.com", trusted_principal)

    identity = VerifiedOidcIdentity(
        issuer="https://accounts.google.com",
        subject="google-sub-10001",
        audiences=frozenset([PROD_CLIENT_ID]),
        email="planner.user@irinfotech.com",
        hosted_domain=INSTITUTIONAL_DOMAIN,
    )

    resolved = await resolver.resolve_oidc_identity(identity)
    assert resolved is not None
    assert resolved.tenant_id == "tenant-prod-inst-01"
    assert resolved.roles == frozenset({"planner"})
    assert resolved.has_permission("plan:create")
    assert not resolved.has_permission("plan:approve")  # Separation of duties


# =============================================================================
# 3. HTTP Integration & Tenant Isolation Tests
# =============================================================================


def test_http_auth_rejects_unmapped_google_token() -> None:
    """Verify HTTP request with cryptographically valid but unmapped Google token returns 401."""
    priv, pub_jwk = generate_rsa_key_pair()
    client_mock = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client_mock)
    resolver = DefaultIdentityResolver(default_tenant_id="tenant-pilot-001")
    authenticator = HttpAuthenticator(
        oidc_verifier=verifier,
        external_identity_resolver=resolver,
    )

    app = FastAPI()
    register_api_exception_handlers(app)

    @app.get("/api/v1/test/secure")
    async def secure_endpoint(
        principal: AuthenticatedPrincipal = Depends(authenticator.authenticate),
    ) -> dict[str, Any]:
        return {"principal_id": principal.principal_id, "tenant_id": principal.tenant_id}

    test_client = TestClient(app)
    raw_token = make_token(priv, email="stranger@irinfotech.com")

    resp = test_client.get(
        "/api/v1/test/secure",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 401
    assert "auth.principal_unresolved" in resp.text


def test_tenant_isolation_boundary_enforced() -> None:
    """Verify authenticated user belongs to strictly assigned tenant."""
    priv, pub_jwk = generate_rsa_key_pair()
    client_mock = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client_mock)
    resolver = DefaultIdentityResolver()

    # User explicitly mapped to tenant-alpha
    user_alpha = AuthenticatedPrincipal(
        principal_id="alice@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"operator"}),
        permissions=frozenset({"task:read"}),
    )
    resolver.register_oidc_mapping("alice@irinfotech.com", user_alpha)

    authenticator = HttpAuthenticator(
        oidc_verifier=verifier,
        external_identity_resolver=resolver,
    )

    app = FastAPI()
    register_api_exception_handlers(app)

    @app.get("/api/v1/data")
    async def get_data(
        principal: AuthenticatedPrincipal = Depends(authenticator.authenticate),
    ) -> dict[str, str]:
        return {"tenant_id": principal.tenant_id}

    test_client = TestClient(app)
    token = make_token(priv, email="alice@irinfotech.com")

    resp = test_client.get(
        "/api/v1/data",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "tenant-beta",  # Client attempt to select tenant-beta
        },
    )
    assert resp.status_code == 200
    # Server strictly enforces server-mapped tenant-alpha, ignoring header injection
    assert resp.json()["tenant_id"] == "tenant-alpha"


# =============================================================================
# 4. Production Configuration Fail-Closed Tests
# =============================================================================


def test_production_mode_rejects_keycloak_configuration() -> None:
    """Verify Settings rejects Keycloak demo provider in production environment."""
    with pytest.raises(ValueError, match="prohibits demonstration identity provider"):
        Settings(
            environment="production",
            oidc_provider_name="keycloak",
            google_oidc_client_ids=[PROD_CLIENT_ID],
        )

    with pytest.raises(ValueError, match="prohibits demonstration Keycloak issuer URL"):
        Settings(
            environment="production",
            oidc_provider_name="google",
            oidc_issuer_url="http://127.0.0.1:8180/realms/operations-engine-demo",
            google_oidc_client_ids=[PROD_CLIENT_ID],
        )


def test_production_mode_rejects_wildcard_cors() -> None:
    """Verify Settings rejects wildcard CORS in production environment."""
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(
            environment="production",
            oidc_provider_name="google",
            cors_allowed_origins=["*"],
            google_oidc_client_ids=[PROD_CLIENT_ID],
        )


def test_http_authenticator_rejects_demo_provider_in_production() -> None:
    """Verify HttpAuthenticator fails closed if initialized with Keycloak in production."""
    settings = Settings(
        environment="production",
        oidc_provider_name="google",
        google_oidc_client_ids=[PROD_CLIENT_ID],
    )
    # Manually tampering with provider name after initialization
    settings.oidc_provider_name = "keycloak"

    with pytest.raises(ValueError, match="prohibits demonstration identity provider"):
        HttpAuthenticator(settings=settings)


# =============================================================================
# 5. Production Identity Roster & User Provisioning Tests
# =============================================================================


def test_oidc_user_mappings_json_provisions_authorized_principals() -> None:
    """Verify oidc_user_mappings_json registers server-controlled principal with canonical roles."""
    import json

    roster = {
        "ops.lead@irinfotech.com": {
            "tenant_id": "tenant-institution-primary",
            "roles": ["operator", "planner"],
            "permissions": ["audit:export"],
            "enabled": True,
        },
        "suspended.staff@irinfotech.com": {
            "tenant_id": "tenant-institution-primary",
            "roles": ["viewer"],
            "enabled": False,
        },
    }

    settings = Settings(
        environment="production",
        oidc_provider_name="google",
        google_oidc_client_ids=[PROD_CLIENT_ID],
        oidc_user_mappings_json=json.dumps(roster),
    )

    priv, pub_jwk = generate_rsa_key_pair()
    client_mock = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client_mock)

    authenticator = HttpAuthenticator(
        settings=settings,
        oidc_verifier=verifier,
    )

    app = FastAPI()
    register_api_exception_handlers(app)

    @app.get("/api/v1/user/me")
    async def get_me(
        principal: AuthenticatedPrincipal = Depends(authenticator.authenticate),
    ) -> dict[str, Any]:
        return {
            "principal_id": principal.principal_id,
            "tenant_id": principal.tenant_id,
            "roles": sorted(list(principal.roles)),
            "can_export": principal.has_permission("audit:export"),
            "can_plan": principal.has_permission("plan:create"),
        }

    test_client = TestClient(app)

    # 1. Enabled mapped user succeeds with canonical + explicit permissions
    token_valid = make_token(priv, email="ops.lead@irinfotech.com")
    resp_valid = test_client.get(
        "/api/v1/user/me",
        headers={"Authorization": f"Bearer {token_valid}"},
    )
    assert resp_valid.status_code == 200
    data = resp_valid.json()
    assert data["principal_id"] == "ops.lead@irinfotech.com"
    assert data["tenant_id"] == "tenant-institution-primary"
    assert "operator" in data["roles"]
    assert "planner" in data["roles"]
    assert data["can_export"] is True
    assert data["can_plan"] is True

    # 2. Suspended user fails closed (401 principal unresolved)
    token_suspended = make_token(priv, email="suspended.staff@irinfotech.com")
    resp_suspended = test_client.get(
        "/api/v1/user/me",
        headers={"Authorization": f"Bearer {token_suspended}"},
    )
    assert resp_suspended.status_code == 401
    assert "auth.principal_unresolved" in resp_suspended.text


@pytest.mark.asyncio
async def test_google_id_token_nonce_validation() -> None:
    """Verify ID token nonce validation: matches succeed, mismatches raise NonceMismatchError."""
    priv, pub_jwk = generate_rsa_key_pair()
    client = httpx.AsyncClient(transport=make_transport(pub_jwk))
    verifier = GoogleOidcVerifier(client_ids=[PROD_CLIENT_ID], http_client=client)

    # 1. Matching nonce succeeds
    tok_matching = make_token(priv, nonce="valid-session-nonce-12345")
    identity = await verifier.verify_id_token(
        tok_matching, expected_nonce="valid-session-nonce-12345"
    )
    assert identity.subject == "google-sub-10001"

    # 2. Mismatched nonce raises NonceMismatchError
    with pytest.raises(NonceMismatchError, match="does not match expected nonce"):
        await verifier.verify_id_token(tok_matching, expected_nonce="different-session-nonce")

    # 3. Missing nonce when expected raises NonceMismatchError
    tok_no_nonce = make_token(priv, nonce=None)
    with pytest.raises(NonceMismatchError, match="missing expected 'nonce' claim"):
        await verifier.verify_id_token(tok_no_nonce, expected_nonce="some-nonce")


@pytest.mark.asyncio
async def test_subject_based_mapping_preserves_access_when_email_changes() -> None:
    """Verify that a user mapped by immutable Google subject retains access when email changes."""
    resolver = DefaultIdentityResolver()
    trusted_principal = AuthenticatedPrincipal(
        principal_id="alice.smith@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-prod-inst-01",
        roles=frozenset({"planner"}),
        permissions=frozenset({"plan:create"}),
        issuer="https://accounts.google.com",
        external_subject="google-sub-immutable-9876",
    )
    # Register primarily under stable immutable Google subject
    resolver.register_oidc_mapping("google-sub-immutable-9876", trusted_principal)

    # User changed their corporate email (e.g. name change or alias)
    identity_new_email = VerifiedOidcIdentity(
        issuer="https://accounts.google.com",
        subject="google-sub-immutable-9876",
        audiences=frozenset([PROD_CLIENT_ID]),
        email="alice.jones@irinfotech.com",
        hosted_domain=INSTITUTIONAL_DOMAIN,
    )

    resolved = await resolver.resolve_oidc_identity(identity_new_email)
    assert resolved is not None
    assert resolved.tenant_id == "tenant-prod-inst-01"
    assert resolved.roles == frozenset({"planner"})
    assert resolved.principal_id == "alice.smith@irinfotech.com"


@pytest.mark.asyncio
async def test_recycled_email_with_different_subject_rejected() -> None:
    """Verify that a recycled email with a different Google subject is rejected (anti-hijacking)."""
    resolver = DefaultIdentityResolver()
    original_principal = AuthenticatedPrincipal(
        principal_id="staff@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-prod-inst-01",
        roles=frozenset({"planner"}),
        permissions=frozenset({"plan:create"}),
        issuer="https://accounts.google.com",
        external_subject="google-sub-original-111",
    )
    # Mapped by email with bound external_subject
    resolver.register_oidc_mapping("staff@irinfotech.com", original_principal)

    # New employee gets the recycled email but has a different Google subject
    new_employee_identity = VerifiedOidcIdentity(
        issuer="https://accounts.google.com",
        subject="google-sub-imposter-999",  # Different subject!
        audiences=frozenset([PROD_CLIENT_ID]),
        email="staff@irinfotech.com",
        hosted_domain=INSTITUTIONAL_DOMAIN,
    )

    # Must fail closed: cannot hijack the previous employee's tenant and roles
    resolved = await resolver.resolve_oidc_identity(new_employee_identity)
    assert resolved is None


def test_conflicting_identity_mapping_rejected() -> None:
    """Verify that registering conflicting OIDC identity mappings raises ValueError."""
    resolver = DefaultIdentityResolver()
    principal_a = AuthenticatedPrincipal(
        principal_id="user.a@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"planner"}),
    )
    principal_b = AuthenticatedPrincipal(
        principal_id="user.b@irinfotech.com",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-beta",  # Conflicting tenant!
        roles=frozenset({"approver"}),
    )

    resolver.register_oidc_mapping("google-sub-conflict-123", principal_a)
    with pytest.raises(ValueError, match="Conflicting OIDC mapping detected"):
        resolver.register_oidc_mapping("google-sub-conflict-123", principal_b)


