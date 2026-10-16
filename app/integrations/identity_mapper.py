"""Canonical external identity mapping layer for E-7 integrations.

Part of Phase 7 Closure Reconciliation.
Enforces:
- Provider-neutral identity mapping between external systems and E-7 entities.
- Preservation of conceptual mapping attributes:
  * provider
  * external_entity_type
  * external_id
  * canonical_entity_id
  * tenant_id
  * last_seen_at
  * source_updated_at / source_version
- Reuse of existing durable infrastructure:
  * calendar_links table for external calendar entities
  * resources table (resource_key + attributes JSONB) for resource entities
- Strict prevention of raw MySQL integer primary keys silently becoming canonical E-7 IDs.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.scheduling import CalendarLinkRecord, ResourceRecord
from app.integrations.erp.dtos import ExternalIdentityMappingDTO


class ExternalIdentityMapper:
    """Canonical mapper bridging external provider identities and E-7 entities."""

    @staticmethod
    def derive_canonical_resource_key(
        provider: str,
        entity_type: str,
        external_id: str,
        tenant_id: str,
    ) -> str:
        """Derive a canonical E-7 resource key preventing raw MySQL PK leakage.

        Guarantees that external auto-increment integer IDs (e.g. 101) never silently
        become canonical operational E-7 identifiers.
        """
        clean_ext = str(external_id).strip()
        clean_type = str(entity_type).strip().lower()
        clean_prov = str(provider).strip().lower()
        # Formulate opaque, namespaced canonical key
        return f"urn:e7:{tenant_id}:{clean_prov}:{clean_type}:{clean_ext}"

    async def map_calendar_entity(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider: str,
        external_calendar_id: str,
        external_event_id: str,
        canonical_assignment_id: UUID | None,
        etag: str | None = None,
        provider_version: int | None = None,
        provider_updated_at: datetime | None = None,
    ) -> ExternalIdentityMappingDTO:
        """Record or update an external calendar identity mapping in calendar_links."""
        now = datetime.now(UTC)
        stmt = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.provider == provider,
            CalendarLinkRecord.external_calendar_id == external_calendar_id,
            CalendarLinkRecord.external_event_id == external_event_id,
            CalendarLinkRecord.link_kind == "EVENT",
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record is None:
            record = CalendarLinkRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                assignment_id=canonical_assignment_id,
                link_kind="EVENT",
                provider=provider,
                external_calendar_id=external_calendar_id,
                external_event_id=external_event_id,
                sync_status="SYNCED",
                etag=etag,
                provider_version=provider_version,
                provider_updated_at=provider_updated_at or now,
                last_synced_at=now,
            )
            session.add(record)
        else:
            if canonical_assignment_id:
                record.assignment_id = canonical_assignment_id
            record.etag = etag or record.etag
            record.provider_version = provider_version or record.provider_version
            record.provider_updated_at = provider_updated_at or record.provider_updated_at
            record.last_synced_at = now
            record.sync_status = "SYNCED"

        await session.flush()

        return ExternalIdentityMappingDTO(
            provider=provider,
            external_entity_type="calendar_event",
            external_id=external_event_id,
            canonical_entity_id=str(record.assignment_id or record.id),
            tenant_id=tenant_id,
            last_seen_at=now,
            source_updated_at=record.provider_updated_at,
            source_version=str(provider_version) if provider_version is not None else etag,
        )

    async def resolve_calendar_entity(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider: str,
        external_event_id: str,
    ) -> ExternalIdentityMappingDTO | None:
        """Resolve external calendar event identity to canonical E-7 assignment."""
        stmt = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.provider == provider,
            CalendarLinkRecord.external_event_id == external_event_id,
            CalendarLinkRecord.link_kind == "EVENT",
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        if record is None:
            return None

        return ExternalIdentityMappingDTO(
            provider=provider,
            external_entity_type="calendar_event",
            external_id=external_event_id,
            canonical_entity_id=str(record.assignment_id or record.id),
            tenant_id=tenant_id,
            last_seen_at=record.last_synced_at or record.updated_at,
            source_updated_at=record.provider_updated_at,
            source_version=record.etag or (
                str(record.provider_version) if record.provider_version else None
            ),
        )

    async def map_resource_entity(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider: str,
        external_type: str,
        external_id: str,
        resource_id: UUID,
        source_version: str | None = None,
        source_updated_at: datetime | None = None,
    ) -> ExternalIdentityMappingDTO:
        """Map an external ERP/facility resource to canonical E-7 ResourceRecord."""
        stmt = select(ResourceRecord).where(
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.id == resource_id,
        )
        res = await session.execute(stmt)
        resource = res.scalar_one_or_none()
        if not resource:
            raise ValueError(f"Resource {resource_id} not found for tenant {tenant_id}")

        now = datetime.now(UTC)
        current_attrs = dict(resource.attributes or {})
        current_attrs["external_mapping"] = {
            "provider": provider,
            "external_type": external_type,
            "external_id": str(external_id),
            "last_seen_at": now.isoformat(),
            "source_version": source_version,
            "source_updated_at": source_updated_at.isoformat() if source_updated_at else None,
        }
        resource.attributes = current_attrs
        await session.flush()

        return ExternalIdentityMappingDTO(
            provider=provider,
            external_entity_type=external_type,
            external_id=str(external_id),
            canonical_entity_id=str(resource.id),
            tenant_id=tenant_id,
            last_seen_at=now,
            source_updated_at=source_updated_at,
            source_version=source_version,
        )

    async def resolve_resource_entity(
        self,
        session: AsyncSession,
        tenant_id: str,
        provider: str,
        external_type: str,
        external_id: str,
    ) -> ExternalIdentityMappingDTO | None:
        """Resolve external ERP/facility identity to canonical E-7 ResourceRecord."""
        stmt = select(ResourceRecord).where(
            ResourceRecord.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        resources = res.scalars().all()

        target_ext_id = str(external_id)
        for r in resources:
            attrs = r.attributes or {}
            mapping = attrs.get("external_mapping", {})
            if (
                mapping.get("provider") == provider
                and mapping.get("external_type") == external_type
                and mapping.get("external_id") == target_ext_id
            ):
                last_seen = None
                if mapping.get("last_seen_at"):
                    try:
                        last_seen = datetime.fromisoformat(mapping["last_seen_at"])
                    except Exception:
                        pass
                src_upd = None
                if mapping.get("source_updated_at"):
                    try:
                        src_upd = datetime.fromisoformat(mapping["source_updated_at"])
                    except Exception:
                        pass

                return ExternalIdentityMappingDTO(
                    provider=provider,
                    external_entity_type=external_type,
                    external_id=target_ext_id,
                    canonical_entity_id=str(r.id),
                    tenant_id=tenant_id,
                    last_seen_at=last_seen or r.updated_at,
                    source_updated_at=src_upd,
                    source_version=mapping.get("source_version"),
                )

        return None


__all__ = ["ExternalIdentityMapper"]
