"""External resource freshness tracking service.

Part of Phase 7E Risk Monitor & Resource Freshness & Phase 7 Closure Reconciliation.
Enforces:
- Tracking provider, external ID, last fetch time, source version, and staleness state.
- Computation of freshness from durable PostgreSQL records (calendar_links and resources).
- Detection of STALE upstream resources when threshold duration is exceeded.
- Truthful degradation reporting on fetch failure.
- Tenant isolation across all freshness queries.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.scheduling import CalendarLinkRecord, ResourceRecord


class ResourceFreshnessRecord(BaseModel):
    """Tracking record for an externally sourced resource/master data entity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    external_id: str
    tenant_id: str
    last_successful_fetch: datetime | None = None
    source_timestamp: datetime | None = None
    source_version: str | None = None
    staleness_state: str = Field(default="OFFLINE")  # FRESH, STALE, DEGRADED, OFFLINE
    last_failure: str | None = None
    staleness_threshold_seconds: float = Field(default=3600.0)

    @property
    def is_stale(self) -> bool:
        """Return True if resource is STALE, DEGRADED, or OFFLINE."""
        return self.staleness_state in ("STALE", "DEGRADED", "OFFLINE")


class ResourceFreshnessTracker:
    """Session-aware and durable-backed freshness registry for external master data."""

    def __init__(self, default_threshold_seconds: float = 3600.0) -> None:
        self._default_threshold = default_threshold_seconds
        # Registry key: (tenant_id, provider, external_id) -> ResourceFreshnessRecord
        self._records: dict[tuple[str, str, str], ResourceFreshnessRecord] = {}

    def record_fetch(
        self,
        tenant_id: str,
        provider: str,
        external_id: str,
        source_timestamp: datetime | None = None,
        source_version: str | None = None,
        threshold_seconds: float | None = None,
    ) -> ResourceFreshnessRecord:
        """Record a successful upstream fetch."""
        now = datetime.now(UTC)
        threshold = threshold_seconds or self._default_threshold
        rec = ResourceFreshnessRecord(
            provider=provider,
            external_id=external_id,
            tenant_id=tenant_id,
            last_successful_fetch=now,
            source_timestamp=source_timestamp or now,
            source_version=source_version,
            staleness_state="FRESH",
            last_failure=None,
            staleness_threshold_seconds=threshold,
        )
        self._records[(tenant_id, provider, external_id)] = rec
        return rec

    def record_failure(
        self,
        tenant_id: str,
        provider: str,
        external_id: str,
        error_message: str,
        threshold_seconds: float | None = None,
    ) -> ResourceFreshnessRecord:
        """Record an upstream fetch failure."""
        threshold = threshold_seconds or self._default_threshold
        prior = self._records.get((tenant_id, provider, external_id))
        last_fetch = prior.last_successful_fetch if prior else None
        src_ts = prior.source_timestamp if prior else None
        src_ver = prior.source_version if prior else None

        rec = ResourceFreshnessRecord(
            provider=provider,
            external_id=external_id,
            tenant_id=tenant_id,
            last_successful_fetch=last_fetch,
            source_timestamp=src_ts,
            source_version=src_ver,
            staleness_state="DEGRADED",
            last_failure=error_message,
            staleness_threshold_seconds=threshold,
        )
        self._records[(tenant_id, provider, external_id)] = rec
        return rec

    def get_freshness(
        self, tenant_id: str, provider: str, external_id: str
    ) -> ResourceFreshnessRecord:
        """Inspect and return current freshness for a specific external entity."""
        key = (tenant_id, provider, external_id)
        if key not in self._records:
            return ResourceFreshnessRecord(
                provider=provider,
                external_id=external_id,
                tenant_id=tenant_id,
                staleness_state="OFFLINE",
                staleness_threshold_seconds=self._default_threshold,
            )

        current = self._records[key]
        now = datetime.now(UTC)
        if current.last_successful_fetch and current.staleness_state == "FRESH":
            age = (now - current.last_successful_fetch).total_seconds()
            if age > current.staleness_threshold_seconds:
                # Transition to STALE
                updated = ResourceFreshnessRecord(
                    provider=current.provider,
                    external_id=current.external_id,
                    tenant_id=current.tenant_id,
                    last_successful_fetch=current.last_successful_fetch,
                    source_timestamp=current.source_timestamp,
                    source_version=current.source_version,
                    staleness_state="STALE",
                    last_failure=current.last_failure,
                    staleness_threshold_seconds=current.staleness_threshold_seconds,
                )
                self._records[key] = updated
                return updated

        return current

    def list_stale_resources(self, tenant_id: str) -> list[ResourceFreshnessRecord]:
        """List all resources for tenant that are currently STALE, DEGRADED, or OFFLINE."""
        results: list[ResourceFreshnessRecord] = []
        for key in list(self._records.keys()):
            if key[0] == tenant_id:
                freshness = self.get_freshness(key[0], key[1], key[2])
                if freshness.is_stale:
                    results.append(freshness)
        return results

    def list_all_resources(self, tenant_id: str) -> list[ResourceFreshnessRecord]:
        """List all tracked resources for tenant."""
        results: list[ResourceFreshnessRecord] = []
        for key in list(self._records.keys()):
            if key[0] == tenant_id:
                results.append(self.get_freshness(key[0], key[1], key[2]))
        return results

    async def compute_from_durable_records(
        self,
        session: AsyncSession,
        tenant_id: str,
    ) -> list[ResourceFreshnessRecord]:
        """Compute and populate freshness states directly from durable PostgreSQL records.

        Pulls metadata from:
        - calendar_links: external calendar links and cursors
        - resources: resources with external_mapping attributes
        Survives process and worker restarts without requiring a dedicated freshness table.
        """
        now = datetime.now(UTC)

        # 1. Inspect calendar_links
        stmt_cal = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id
        )
        res_cal = await session.execute(stmt_cal)
        cal_links = res_cal.scalars().all()

        for link in cal_links:
            ext_id = link.external_event_id or link.external_calendar_id or str(link.id)
            last_fetch = link.last_synced_at or link.updated_at
            age = (now - last_fetch).total_seconds() if last_fetch else 99999999.0

            if link.sync_status in ("FAILED", "DEGRADED", "CONFLICT"):
                state = "DEGRADED"
            elif not last_fetch:
                state = "OFFLINE"
            elif age > self._default_threshold:
                state = "STALE"
            else:
                state = "FRESH"

            rec = ResourceFreshnessRecord(
                provider=link.provider,
                external_id=ext_id,
                tenant_id=tenant_id,
                last_successful_fetch=last_fetch,
                source_timestamp=link.provider_updated_at,
                source_version=link.etag or (
                    str(link.provider_version) if link.provider_version else None
                ),
                staleness_state=state,
                last_failure=link.error_message,
                staleness_threshold_seconds=self._default_threshold,
            )
            self._records[(tenant_id, link.provider, ext_id)] = rec

        # 2. Inspect resources with external mapping
        stmt_res = select(ResourceRecord).where(
            ResourceRecord.tenant_id == tenant_id
        )
        res_res = await session.execute(stmt_res)
        resources = res_res.scalars().all()

        for r in resources:
            attrs = r.attributes or {}
            mapping = attrs.get("external_mapping")
            if not mapping or not isinstance(mapping, dict):
                continue

            provider = mapping.get("provider", "erp")
            ext_id = mapping.get("external_id", r.resource_key)
            source_version = mapping.get("source_version")

            last_seen = None
            if mapping.get("last_seen_at"):
                try:
                    last_seen = datetime.fromisoformat(mapping["last_seen_at"])
                except Exception:
                    pass
            last_fetch = last_seen or r.updated_at
            age = (now - last_fetch).total_seconds() if last_fetch else 99999999.0

            if age > self._default_threshold:
                state = "STALE"
            else:
                state = "FRESH"

            rec = ResourceFreshnessRecord(
                provider=provider,
                external_id=str(ext_id),
                tenant_id=tenant_id,
                last_successful_fetch=last_fetch,
                source_timestamp=last_fetch,
                source_version=source_version,
                staleness_state=state,
                last_failure=None,
                staleness_threshold_seconds=self._default_threshold,
            )
            self._records[(tenant_id, provider, str(ext_id))] = rec

        return self.list_all_resources(tenant_id)


__all__ = [
    "ResourceFreshnessRecord",
    "ResourceFreshnessTracker",
]
