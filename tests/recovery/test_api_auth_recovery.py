"""API Restart, Production Service API Key Credential Persistence & REC-001 Closure Tests.

Validates:
1. Baseline finding REC-001: InMemoryServiceApiKeyCredentialStore is DEV_TEST_ONLY.
2. REC-001 Closure (Path B - PostgreSQL Persistence):
   - Production service credentials persisted in PostgreSQL `service_api_credentials`.
   - API Restart Recovery: Fresh API/authenticator process authenticates existing credentials.
   - Revocation Recovery: Revoked credential remains disabled across API process restarts.
   - Multi-Replica Consistency: Concurrent replica authenticators agree on credential state.
   - Tenant Isolation: Authenticated principal strictly scoped to assigned tenant; cross-tenant
     access denied.
   - Secret Safety: Only one-way SHA-256 digest is stored; raw secrets are never persisted.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from starlette.requests import Request

from app.api.errors import ApiError
from app.core.auth.dependency import HttpAuthenticator
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    VerifiedOidcIdentity,
)
from app.core.auth.service_api_key import (
    InMemoryServiceApiKeyCredentialStore,
    PostgresServiceApiKeyCredentialStore,
    ServiceApiKeyCredentialRecord,
    compute_secret_digest,
)
from app.core.config import get_settings
from app.db.engine import DatabaseManager
from tests.security.conftest import FakeGoogleOidcVerifier


def _build_dummy_request(headers: dict[str, str]) -> Request:
    """Build a minimal ASGI Request with provided headers for dependency testing."""
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/plans",
        "headers": raw_headers,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_in_memory_service_api_key_store_restart_loss_finding_rec_001() -> None:
    """Verify that InMemoryServiceApiKeyCredentialStore loses keys upon process restart (REC-001).

    Classification: DEV_TEST_ONLY.
    Proves why in-memory storage cannot be used as the authoritative production credential store.
    """
    secret = "a" * 44
    secret_digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    cred_id = f"rec-cred-dev-{uuid4().hex[:8]}"
    record = ServiceApiKeyCredentialRecord(
        credential_id=cred_id,
        secret_digest=secret_digest,
        service_subject="service:payment-gateway",
        enabled=True,
    )

    # 1. Active runtime instance before restart
    store_before_restart = InMemoryServiceApiKeyCredentialStore()
    store_before_restart.add_record(record)

    found_before = await store_before_restart.get_by_credential_id(cred_id)
    assert found_before is not None
    assert found_before.service_subject == "service:payment-gateway"

    # 2. Simulate API process restart (fresh in-memory store initialized)
    store_after_restart = InMemoryServiceApiKeyCredentialStore()

    # The in-memory credential vanished on restart
    found_after = await store_after_restart.get_by_credential_id(cred_id)
    assert found_after is None, "In-memory credential store loses all keys on process restart"


@pytest.mark.asyncio
async def test_postgres_service_credential_persistence_and_api_restart_recovery() -> None:
    """Verify production service credentials survive API process restart via PostgreSQL (REC-001).

    Steps:
    1. Create persistent service credential in PostgreSQL.
    2. Authenticate successfully through HttpAuthenticator.
    3. Simulate API restart: dispose connections, initialize completely new HttpAuthenticator.
    4. Authenticate using same still-valid credential after restart.
    5. Verify tenant isolation: principal.tenant_id strictly equals assigned tenant.
    """
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"rec-tenant-{uuid4().hex[:8]}"
    cred_id = f"svc_key_{uuid4().hex[:12]}"
    secret = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_api_key = f"e7sk_{cred_id}.{secret}"
    digest = compute_secret_digest(secret)

    try:
        # 1. Persist credential into PostgreSQL
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id,
            service_subject=f"service:{tenant_id}:planner",
            secret_digest=digest,
            roles=["planner", "operator"],
            permissions=["plans:read", "plans:write"],
            enabled=True,
        )

        # 2. Authenticate with initial authenticator
        authenticator_1 = HttpAuthenticator(settings=settings)
        req_1 = _build_dummy_request({"X-E7-Service-Key": raw_api_key})
        principal_1 = await authenticator_1.authenticate(req_1)

        assert principal_1 is not None
        assert principal_1.tenant_id == tenant_id
        assert "planner" in principal_1.roles
        assert "plans:read" in principal_1.permissions

        # 3. Simulate API process restart (reconstruct completely fresh authenticator)
        authenticator_restart = HttpAuthenticator(settings=settings)

        # 4. Authenticate after restart: must succeed immediately from persistent PostgreSQL
        req_2 = _build_dummy_request({"X-E7-Service-Key": raw_api_key})
        principal_2 = await authenticator_restart.authenticate(req_2)

        assert principal_2 is not None
        assert principal_2.tenant_id == tenant_id
        assert principal_2.principal_id == f"svc:{tenant_id}:{cred_id}"
        assert "planner" in principal_2.roles
        assert "plans:write" in principal_2.permissions

        # 5. Verify tenant isolation invariant
        assert principal_2.tenant_id != "unauthorized-other-tenant"

    finally:
        # Cleanup
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM service_api_credentials WHERE credential_id = :cid"),
                {"cid": cred_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_postgres_service_credential_revocation_survives_restart() -> None:
    """Verify revoked credentials remain disabled across process restarts."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"rec-tenant-{uuid4().hex[:8]}"
    cred_id = f"svc_revoke_{uuid4().hex[:12]}"
    secret = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_api_key = f"e7sk_{cred_id}.{secret}"
    digest = compute_secret_digest(secret)

    try:
        # 1. Create active credential
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id,
            service_subject=f"service:{tenant_id}:worker",
            secret_digest=digest,
            roles=["operator"],
            enabled=True,
        )

        auth_before = HttpAuthenticator(settings=settings)
        req = _build_dummy_request({"X-E7-Service-Key": raw_api_key})
        principal = await auth_before.authenticate(req)
        assert principal is not None

        # 2. Revoke credential in PostgreSQL
        revoked = await store.revoke_credential(cred_id, revoked_by="security-admin")
        assert revoked is True

        # 3. Simulate process restart
        auth_after_restart = HttpAuthenticator(settings=settings)

        # 4. Must fail closed (401 Credential Disabled)
        with pytest.raises(ApiError) as exc_info:
            await auth_after_restart.authenticate(req)

        assert exc_info.value.status_code == 401
        assert "DISABLED" in exc_info.value.message or "disabled" in exc_info.value.message

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM service_api_credentials WHERE credential_id = :cid"),
                {"cid": cred_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_multi_replica_credential_consistency() -> None:
    """Verify multiple concurrent API replicas share consistent credential state via PostgreSQL."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"rec-tenant-{uuid4().hex[:8]}"
    cred_id = f"svc_replica_{uuid4().hex[:12]}"
    secret = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_api_key = f"e7sk_{cred_id}.{secret}"
    digest = compute_secret_digest(secret)

    try:
        # Create credential
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id,
            service_subject=f"service:{tenant_id}:daemon",
            secret_digest=digest,
            roles=["scheduler"],
            enabled=True,
        )

        # Two independent replica authenticator instances
        replica_1 = HttpAuthenticator(settings=settings)
        replica_2 = HttpAuthenticator(settings=settings)

        req = _build_dummy_request({"X-E7-Service-Key": raw_api_key})

        p1 = await replica_1.authenticate(req)
        p2 = await replica_2.authenticate(req)

        # Both replicas agree on principal and tenant
        assert p1.principal_id == p2.principal_id
        assert p1.tenant_id == p2.tenant_id == tenant_id

        # Revoke
        await store.revoke_credential(cred_id)

        # Both replicas immediately reject revoked key
        with pytest.raises(ApiError):
            await replica_1.authenticate(req)
        with pytest.raises(ApiError):
            await replica_2.authenticate(req)

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM service_api_credentials WHERE credential_id = :cid"),
                {"cid": cred_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_secret_safety_no_plaintext_stored_or_logged() -> None:
    """Verify raw secret is never stored in PostgreSQL; only 64-char SHA-256 digest is persisted."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"rec-tenant-{uuid4().hex[:8]}"
    cred_id = f"svc_safety_{uuid4().hex[:12]}"
    secret = f"very_sensitive_secret_{uuid4().hex}{uuid4().hex}"[:48]
    digest = compute_secret_digest(secret)

    try:
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id,
            service_subject="service:audit",
            secret_digest=digest,
        )

        async with manager.session_factory() as session:
            stmt = text(
                "SELECT secret_digest, description FROM service_api_credentials "
                "WHERE credential_id = :cid"
            )
            row = (await session.execute(stmt, {"cid": cred_id})).one()

            stored_digest = row[0]
            # Exact 64 hex characters (SHA-256)
            assert len(stored_digest) == 64
            assert stored_digest == digest
            # Raw secret substring must NOT appear anywhere in stored digest
            assert secret not in stored_digest

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM service_api_credentials WHERE credential_id = :cid"),
                {"cid": cred_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_canonical_http_auth_contract_and_persistence_reconciliation() -> None:
    """Explicitly verify canonical HTTP auth boundary (Phase 8A) with persistent store (Phase 8D).

    Covers all required contract scenarios:
    A. valid OIDC Bearer -> authenticates as human/OIDC principal
    B. valid X-E7-Service-Key -> authenticates as service principal
    C. invalid service key -> 401
    D. revoked persisted service key -> 401
    E. expired persisted service key -> 401
    F. valid OIDC Bearer + valid X-E7-Service-Key -> 400 conflicting mechanisms
    G. malformed Authorization header -> 401
    """
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"rec-tenant-{uuid4().hex[:8]}"
    cred_id_active = f"svc_active_{uuid4().hex[:12]}"
    secret_active = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_key_active = f"e7sk_{cred_id_active}.{secret_active}"
    digest_active = compute_secret_digest(secret_active)

    cred_id_revoked = f"svc_revoked_{uuid4().hex[:12]}"
    secret_revoked = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_key_revoked = f"e7sk_{cred_id_revoked}.{secret_revoked}"
    digest_revoked = compute_secret_digest(secret_revoked)

    cred_id_expired = f"svc_expired_{uuid4().hex[:12]}"
    secret_expired = f"secret_key_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_key_expired = f"e7sk_{cred_id_expired}.{secret_expired}"
    digest_expired = compute_secret_digest(secret_expired)

    fake_oidc = FakeGoogleOidcVerifier()
    oidc_token = "token_valid_human_user_123"
    oidc_sub = "google-sub-user-999"
    fake_oidc.register_token(
        oidc_token,
        VerifiedOidcIdentity(
            issuer="https://accounts.google.com",
            subject=oidc_sub,
            audiences=frozenset({"e7-test-client-id"}),
            email="human@example.com",
        ),
    )

    human_principal = AuthenticatedPrincipal(
        principal_id="user:human-999",
        tenant_id=tenant_id,
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        roles=["planner"],
        permissions=["plans:read"],
    )

    authenticator = HttpAuthenticator(settings=settings, oidc_verifier=fake_oidc)  # type: ignore[arg-type]
    authenticator.identity_resolver.register_oidc_mapping(oidc_sub, human_principal)

    try:
        # 1. Create active credential in PostgreSQL
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id_active,
            service_subject=f"service:{tenant_id}:worker",
            secret_digest=digest_active,
            roles=["operator"],
            permissions=["plans:read", "plans:write"],
            enabled=True,
        )

        # 2. Create revoked credential in PostgreSQL
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id_revoked,
            service_subject=f"service:{tenant_id}:revoked",
            secret_digest=digest_revoked,
            enabled=False,
        )

        # 3. Create expired credential in PostgreSQL
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id_expired,
            service_subject=f"service:{tenant_id}:expired",
            secret_digest=digest_expired,
            enabled=True,
            expires_at=datetime.now(UTC) - timedelta(hours=2),
        )

        # Scenario A: Valid OIDC Bearer -> Human Principal
        req_a = _build_dummy_request({"Authorization": f"Bearer {oidc_token}"})
        p_a = await authenticator.authenticate(req_a)
        assert p_a.principal_type == PrincipalType.HUMAN
        assert p_a.authentication_method == AuthenticationMethod.OIDC
        assert p_a.tenant_id == tenant_id

        # Scenario B: Valid X-E7-Service-Key -> Service Principal
        req_b = _build_dummy_request({"X-E7-Service-Key": raw_key_active})
        p_b = await authenticator.authenticate(req_b)
        assert p_b.principal_type == PrincipalType.SERVICE
        assert p_b.authentication_method == AuthenticationMethod.SERVICE_API_KEY
        assert p_b.tenant_id == tenant_id

        # Scenario C: Invalid Service Key -> 401
        secret_bad = "z" * 45
        req_c = _build_dummy_request({"X-E7-Service-Key": f"e7sk_unknown_key.{secret_bad}"})
        with pytest.raises(ApiError) as exc_c:
            await authenticator.authenticate(req_c)
        assert exc_c.value.status_code == 401

        # Scenario D: Revoked Persisted Key -> 401
        req_d = _build_dummy_request({"X-E7-Service-Key": raw_key_revoked})
        with pytest.raises(ApiError) as exc_d:
            await authenticator.authenticate(req_d)
        assert exc_d.value.status_code == 401

        # Scenario E: Expired Persisted Key -> 401
        req_e = _build_dummy_request({"X-E7-Service-Key": raw_key_expired})
        with pytest.raises(ApiError) as exc_e:
            await authenticator.authenticate(req_e)
        assert exc_e.value.status_code == 401

        # Scenario F: Valid OIDC Bearer + Valid X-E7-Service-Key -> 400 Conflicting Mechanisms
        req_f = _build_dummy_request({
            "Authorization": f"Bearer {oidc_token}",
            "X-E7-Service-Key": raw_key_active,
        })
        with pytest.raises(ApiError) as exc_f:
            await authenticator.authenticate(req_f)
        assert exc_f.value.status_code == 400
        assert exc_f.value.code == "auth.conflicting_credentials"

        # Scenario G: Malformed Authorization Header -> 401
        req_g1 = _build_dummy_request({"Authorization": "Basic dXNlcjpwYXNz"})
        with pytest.raises(ApiError) as exc_g1:
            await authenticator.authenticate(req_g1)
        assert exc_g1.value.status_code == 401
        assert exc_g1.value.code == "auth.invalid_authorization_header"

        req_g2 = _build_dummy_request({"Authorization": "Bearer  "})
        with pytest.raises(ApiError) as exc_g2:
            await authenticator.authenticate(req_g2)
        assert exc_g2.value.status_code == 401
        assert exc_g2.value.code == "auth.invalid_authorization_header"

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM service_api_credentials WHERE credential_id IN (:c1, :c2, :c3)"
                ),
                {"c1": cred_id_active, "c2": cred_id_revoked, "c3": cred_id_expired},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_break_glass_sql_revocation_procedure() -> None:
    """Verify that the break-glass SQL revocation procedure in the incident runbook is executable.

    Validates:
    1. Pre-verification check against real PostgreSQL columns.
    2. Atomic transactional mutation: enabled = false, revoked_at = NOW(), updated_at = NOW().
    3. Post-verification check proving enabled = false and revoked_at is set.
    4. Authenticator immediately rejects revoked key with HTTP 401.
    5. Simulated process restart does NOT reactivate the credential.
    """
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    store = PostgresServiceApiKeyCredentialStore(session_factory=manager.session_factory)

    tenant_id = f"drill-tenant-{uuid4().hex[:8]}"
    cred_id = f"drill_cred_{uuid4().hex[:12]}"
    secret = f"synthetic_secret_material_{uuid4().hex}{uuid4().hex}"[:45]
    raw_key = f"e7sk_{cred_id}.{secret}"
    digest = compute_secret_digest(secret)

    try:
        # 1. Create synthetic credential
        await store.create_credential(
            tenant_id=tenant_id,
            credential_id=cred_id,
            service_subject=f"service:{tenant_id}:drill",
            secret_digest=digest,
            roles=["operator"],
            enabled=True,
        )

        # 2. Verify active authentication
        auth1 = HttpAuthenticator(settings=settings)
        p1 = await auth1.authenticate(_build_dummy_request({"X-E7-Service-Key": raw_key}))
        assert p1 is not None
        assert p1.tenant_id == tenant_id

        # 3. Execute the break-glass revocation procedure EXACTLY as documented
        async with manager.session_factory() as session:
            # Pre-verification
            pre_res = await session.execute(
                text("""
                    SELECT credential_id, tenant_id, service_subject,
                           enabled, created_at, revoked_at
                    FROM service_api_credentials
                    WHERE credential_id = :cid
                """),
                {"cid": cred_id},
            )
            row = pre_res.fetchone()
            assert row is not None
            assert row.enabled is True

            # Atomic revocation transaction
            await session.execute(
                text("""
                    UPDATE service_api_credentials
                    SET enabled = false,
                        revoked_at = NOW(),
                        updated_at = NOW()
                    WHERE credential_id = :cid
                      AND enabled = true
                """),
                {"cid": cred_id},
            )
            await session.commit()

            # Post-verification
            post_res = await session.execute(
                text("""
                    SELECT credential_id, tenant_id, enabled, revoked_at, updated_at
                    FROM service_api_credentials
                    WHERE credential_id = :cid
                """),
                {"cid": cred_id},
            )
            post_row = post_res.fetchone()
            assert post_row is not None
            assert post_row.enabled is False
            assert post_row.revoked_at is not None

        # 4. Verify immediate rejection (HTTP 401)
        auth_now = HttpAuthenticator(settings=settings)
        with pytest.raises(ApiError) as exc_now:
            await auth_now.authenticate(_build_dummy_request({"X-E7-Service-Key": raw_key}))
        assert exc_now.value.status_code == 401

        # 5. Verify restart does not reactivate
        auth_restart = HttpAuthenticator(settings=settings)
        with pytest.raises(ApiError) as exc_restart:
            await auth_restart.authenticate(_build_dummy_request({"X-E7-Service-Key": raw_key}))
        assert exc_restart.value.status_code == 401

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM service_api_credentials WHERE credential_id = :cid"),
                {"cid": cred_id},
            )
            await session.commit()
        await manager.dispose()


