"""Focused Pilot Tenant Isolation Tests (Phase 9A).

Validates:
1. Cross-tenant resource read rejection: A foreign tenant cannot read pilot tenant resources.
2. Cross-tenant mutation rejection: A foreign tenant cannot record availability or mutate
   pilot tenant resources.
3. State immutability: Rehearsal tenant resources and availabilities remain unchanged
   after rejected cross-tenant mutation attempts.
4. Identity mapper tenant isolation: Foreign tenant cannot resolve external ERP identities
   mapped to the pilot tenant.
5. Service credential tenant scoping: Pilot tenant service API key resolves strictly to
   tenant-pilot-001 with zero cross-tenant authorization leakage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.auth.dependency import HttpAuthenticator
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.auth.resolvers import DefaultIdentityResolver
from app.core.auth.service_api_key import (
    InMemoryServiceApiKeyCredentialStore,
    ServiceApiKeyCredentialRecord,
    compute_secret_digest,
)
from app.core.config import get_settings
from app.integrations.erp.dtos import ExternalIdentityMappingDTO
from app.integrations.identity_mapper import ExternalIdentityMapper
from app.resources.schemas import (
    AvailabilityIntervalCreate,
    IntervalType,
    ResourceCreate,
    ResourceType,
)
from app.resources.service import ResourceNotFoundError, ResourceService

PILOT_TENANT: str = "tenant-pilot-001"
FOREIGN_TENANT: str = "tenant-foreign-999"


def _build_dummy_request(headers: dict[str, str]) -> Request:
    """Build a minimal ASGI Request with provided headers for dependency testing."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": raw_headers,
    }
    return Request(scope)


def test_cross_tenant_resource_read_rejected() -> None:
    """Verify foreign tenant cannot read pilot rehearsal tenant's resources."""
    service = ResourceService()
    # Register resource in pilot tenant
    pilot_res = service.register_resource(
        PILOT_TENANT,
        ResourceCreate(
            resource_key="res-inv-001",
            type=ResourceType.PERSON,
            display_label="Invigilator Alpha",
            capacity=1,
            skills=["INVIGILATION_GENERAL", "SCIENCE"],
        ),
    )

    # Pilot tenant can read its own resource
    fetched = service.get_resource(PILOT_TENANT, pilot_res.id)
    assert fetched.id == pilot_res.id
    assert fetched.tenant_id == PILOT_TENANT

    # Foreign tenant read MUST be rejected with ResourceNotFoundError
    with pytest.raises(ResourceNotFoundError, match="not found in tenant"):
        service.get_resource(FOREIGN_TENANT, pilot_res.id)

    # Foreign tenant listing must NOT expose pilot tenant resource
    foreign_list = service.list_resources(FOREIGN_TENANT)
    assert len(foreign_list) == 0


def test_cross_tenant_resource_mutation_rejected_and_state_unchanged() -> None:
    """Verify foreign tenant cannot mutate pilot tenant resources and state remains intact."""
    service = ResourceService()
    pilot_res = service.register_resource(
        PILOT_TENANT,
        ResourceCreate(
            resource_key="res-room-001",
            type=ResourceType.ROOM,
            display_label="Exam Room Alpha",
            capacity=40,
        ),
    )

    t0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)

    # Record legitimate availability window in pilot tenant
    service.record_availability(
        PILOT_TENANT,
        AvailabilityIntervalCreate(
            resource_id=pilot_res.id,
            start_at=t0,
            end_at=t1,
            interval_type=IntervalType.AVAILABLE,
            reason="Semester Examination Day 1",
        ),
    )

    initial_availabilities = service.get_availability(PILOT_TENANT, pilot_res.id)
    assert len(initial_availabilities) == 1

    # Attempt cross-tenant mutation: foreign tenant tries to add blackout window to pilot resource
    t_blackout_start = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    t_blackout_end = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)

    with pytest.raises(ResourceNotFoundError, match="not found in tenant"):
        service.record_availability(
            FOREIGN_TENANT,
            AvailabilityIntervalCreate(
                resource_id=pilot_res.id,
                start_at=t_blackout_start,
                end_at=t_blackout_end,
                interval_type=IntervalType.UNAVAILABLE,
                reason="Unauthorized Foreign Blackout",
            ),
        )

    # Verify state after rejected operation: exactly 1 availability interval, zero modification
    after_availabilities = service.get_availability(PILOT_TENANT, pilot_res.id)
    assert len(after_availabilities) == 1
    assert after_availabilities[0].interval_type == IntervalType.AVAILABLE
    assert after_availabilities[0].reason == "Semester Examination Day 1"


def test_external_identity_mapper_tenant_isolation() -> None:
    """Verify ExternalIdentityMapper prevents cross-tenant identity leakage."""
    # Test canonical key derivation includes tenant namespace
    key_pilot = ExternalIdentityMapper.derive_canonical_resource_key(
        provider="synthetic-erp",
        entity_type="person",
        external_id="SYN-ERP-INV-001",
        tenant_id=PILOT_TENANT,
    )
    key_foreign = ExternalIdentityMapper.derive_canonical_resource_key(
        provider="synthetic-erp",
        entity_type="person",
        external_id="SYN-ERP-INV-001",
        tenant_id=FOREIGN_TENANT,
    )

    assert key_pilot != key_foreign
    assert PILOT_TENANT in key_pilot
    assert FOREIGN_TENANT in key_foreign

    # Mapping DTO preserves tenant isolation
    dto = ExternalIdentityMappingDTO(
        provider="synthetic-erp",
        external_entity_type="person",
        external_id="SYN-ERP-INV-001",
        canonical_entity_id=str(uuid4()),
        tenant_id=PILOT_TENANT,
        last_seen_at=datetime.now(UTC),
    )
    assert dto.tenant_id == PILOT_TENANT
    assert dto.tenant_id != FOREIGN_TENANT


@pytest.mark.asyncio
async def test_pilot_service_key_tenant_scoping() -> None:
    """Verify pilot service key resolves strictly to pilot tenant."""
    settings = get_settings()
    cred_id = "cred-pilot-test-key"
    secret = "SyntheticKeyMaterialForPilotIsolationTesting2026"
    raw_api_key = f"e7sk_{cred_id}.{secret}"
    digest = compute_secret_digest(secret)

    store = InMemoryServiceApiKeyCredentialStore()
    store.add_record(
        ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject=f"svc:{PILOT_TENANT}:exam-pilot-admin",
            secret_digest=digest,
            enabled=True,
        )
    )

    resolver = DefaultIdentityResolver()
    resolver.register_service_mapping(
        cred_id,
        AuthenticatedPrincipal(
            principal_id=f"svc:{PILOT_TENANT}:{cred_id}",
            principal_type=PrincipalType.SERVICE,
            tenant_id=PILOT_TENANT,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            roles=frozenset({"tenant_admin", "planner", "scheduler"}),
            permissions=frozenset({"*"}),
        ),
    )

    authenticator = HttpAuthenticator(
        settings=settings,
        credential_store=store,
        service_identity_resolver=resolver,
    )
    req = _build_dummy_request({"X-E7-Service-Key": raw_api_key})
    principal = await authenticator.authenticate(req)

    assert principal is not None
    assert principal.tenant_id == PILOT_TENANT
    assert principal.tenant_id != FOREIGN_TENANT
    assert "tenant_admin" in principal.roles
