"""Unit and contract tests for canonical ExternalIdentityMapper.

Part of Phase 7 Closure Reconciliation (Audit Item 2: External-ID Mapping).
Enforces:
- External provider identity to canonical E-7 identity mapping.
- Conceptual fields preservation:
  * provider
  * external_entity_type
  * external_id
  * canonical_entity_id
  * tenant_id
  * last_seen_at
  * source_updated_at / source_version
- MySQL primary keys MUST NOT silently become canonical E-7 identities.
- Reuse of existing durable infrastructure:
  * calendar_links table for calendar entities
  * resources table (resource_key + attributes JSONB) for resource entities
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models.scheduling import CalendarLinkRecord, ResourceRecord
from app.integrations.erp.dtos import ExternalIdentityMappingDTO
from app.integrations.identity_mapper import ExternalIdentityMapper


def test_mysql_primary_key_never_silently_becomes_canonical_e7_id() -> None:
    """Verifies that raw MySQL primary keys are transformed into namespaced canonical keys."""
    tenant_id = "tenant-school-1"
    raw_mysql_staff_id = "105"  # MySQL auto-increment integer ID
    provider = "mysql_iredu"
    entity_type = "staff"

    canonical_key = ExternalIdentityMapper.derive_canonical_resource_key(
        provider=provider,
        entity_type=entity_type,
        external_id=raw_mysql_staff_id,
        tenant_id=tenant_id,
    )

    # Must NOT equal the raw MySQL primary key string
    assert canonical_key != raw_mysql_staff_id
    assert canonical_key == "urn:e7:tenant-school-1:mysql_iredu:staff:105"
    assert "urn:e7:" in canonical_key


@pytest.mark.asyncio
async def test_calendar_identity_mapping_lifecycle() -> None:
    """Tests creating and resolving calendar identity mappings via calendar_links."""
    mapper = ExternalIdentityMapper()
    tenant_id = "tenant-alpha"
    provider = "google"
    calendar_id = "primary"
    event_id = "evt_gcal_998877"
    assignment_id = uuid4()
    now = datetime.now(UTC)

    # Mock AsyncSession with synchronous Result
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_result_1 = MagicMock()
    mock_result_1.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result_1

    dto = await mapper.map_calendar_entity(
        session=mock_session,
        tenant_id=tenant_id,
        provider=provider,
        external_calendar_id=calendar_id,
        external_event_id=event_id,
        canonical_assignment_id=assignment_id,
        etag='"etag-v1"',
        provider_version=1,
        provider_updated_at=now,
    )

    assert isinstance(dto, ExternalIdentityMappingDTO)
    assert dto.provider == "google"
    assert dto.external_entity_type == "calendar_event"
    assert dto.external_id == event_id
    assert dto.canonical_entity_id == str(assignment_id)
    assert dto.tenant_id == tenant_id
    assert dto.source_version == "1"
    assert dto.source_updated_at == now
    assert dto.last_seen_at is not None

    # Case 2: Resolution of existing record
    existing_rec = CalendarLinkRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        link_kind="EVENT",
        provider=provider,
        external_calendar_id=calendar_id,
        external_event_id=event_id,
        sync_status="SYNCED",
        etag='"etag-v1"',
        provider_version=1,
        provider_updated_at=now,
        last_synced_at=now,
    )
    mock_result_2 = MagicMock()
    mock_result_2.scalar_one_or_none.return_value = existing_rec
    mock_session.execute.return_value = mock_result_2

    resolved = await mapper.resolve_calendar_entity(
        session=mock_session,
        tenant_id=tenant_id,
        provider=provider,
        external_event_id=event_id,
    )

    assert resolved is not None
    assert resolved.canonical_entity_id == str(assignment_id)
    assert resolved.external_id == event_id
    assert resolved.provider == "google"


@pytest.mark.asyncio
async def test_resource_identity_mapping_lifecycle() -> None:
    """Tests mapping and resolving external ERP/facility identities in resources."""
    mapper = ExternalIdentityMapper()
    tenant_id = "tenant-omega"
    resource_id = uuid4()
    now = datetime.now(UTC)

    # Mock existing ResourceRecord in DB
    existing_resource = ResourceRecord(
        id=resource_id,
        tenant_id=tenant_id,
        resource_key="urn:e7:tenant-omega:mysql_iredu:staff:42",
        type="PERSON",
        display_label="Dr. Alan Turing",
        attributes={},
    )

    mock_session = AsyncMock()
    mock_result_1 = MagicMock()
    mock_result_1.scalar_one_or_none.return_value = existing_resource
    mock_session.execute.return_value = mock_result_1

    # 1. Map external ERP staff ID 42 to canonical E-7 ResourceRecord
    dto = await mapper.map_resource_entity(
        session=mock_session,
        tenant_id=tenant_id,
        provider="mysql_iredu",
        external_type="staff",
        external_id="42",
        resource_id=resource_id,
        source_version="v2026.09",
        source_updated_at=now,
    )

    assert isinstance(dto, ExternalIdentityMappingDTO)
    assert dto.provider == "mysql_iredu"
    assert dto.external_entity_type == "staff"
    assert dto.external_id == "42"
    assert dto.canonical_entity_id == str(resource_id)
    assert dto.tenant_id == tenant_id
    assert dto.source_version == "v2026.09"

    # Verify resource.attributes was updated with external_mapping
    assert "external_mapping" in existing_resource.attributes
    assert existing_resource.attributes["external_mapping"]["external_id"] == "42"

    # 2. Resolve external identity back to canonical ResourceRecord
    mock_result_2 = MagicMock()
    mock_result_2.scalars.return_value.all.return_value = [existing_resource]
    mock_session.execute.return_value = mock_result_2

    resolved = await mapper.resolve_resource_entity(
        session=mock_session,
        tenant_id=tenant_id,
        provider="mysql_iredu",
        external_type="staff",
        external_id="42",
    )

    assert resolved is not None
    assert resolved.canonical_entity_id == str(resource_id)
    assert resolved.external_id == "42"
    assert resolved.external_entity_type == "staff"
