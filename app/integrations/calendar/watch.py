"""Durable Google Calendar push notification watch channel management and renewal lifecycle.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- Cryptographically strong unique channel IDs.
- Secure token hashing (SHA-256) for callback verification.
- Durable channel persistence in calendar_watch_channels.
- Safe renewal before expiration with atomic swap and retirement of old channels.
- Tolerant recovery for already expired channels.
- Audit event logging for all watch lifecycle operations.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import CalendarWatchChannelRecord
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter

logger = structlog.get_logger(__name__)

DEFAULT_RENEWAL_WINDOW_SECONDS = 86400  # 24 hours


class CalendarWatchRepository:
    """Repository managing durable persistence for calendar watch channels."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_channel_id(self, channel_id: str) -> CalendarWatchChannelRecord | None:
        """Fetch watch channel record by unique channel ID."""
        stmt = select(CalendarWatchChannelRecord).where(
            CalendarWatchChannelRecord.channel_id == channel_id.strip()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_active_channel(
        self,
        tenant_id: str,
        provider: str,
        external_calendar_id: str,
    ) -> CalendarWatchChannelRecord | None:
        """Fetch current active channel for a specific calendar."""
        stmt = (
            select(CalendarWatchChannelRecord)
            .where(
                CalendarWatchChannelRecord.tenant_id == tenant_id,
                CalendarWatchChannelRecord.provider == provider,
                CalendarWatchChannelRecord.external_calendar_id == external_calendar_id,
                CalendarWatchChannelRecord.status == "ACTIVE",
            )
            .order_by(CalendarWatchChannelRecord.created_at.desc())
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def find_channels_needing_renewal(
        self,
        cutoff_time: datetime,
        provider: str = "google",
        for_update: bool = False,
        tenant_id: str | None = None,
    ) -> list[CalendarWatchChannelRecord]:
        """Find ACTIVE channels that expire before the cutoff time or have no expiration."""
        stmt = select(CalendarWatchChannelRecord).where(
            CalendarWatchChannelRecord.provider == provider,
            CalendarWatchChannelRecord.status == "ACTIVE",
            (CalendarWatchChannelRecord.expiration_at <= cutoff_time)
            | (CalendarWatchChannelRecord.expiration_at.is_(None)),
        )
        if tenant_id:
            stmt = stmt.where(CalendarWatchChannelRecord.tenant_id == tenant_id)
        if for_update:
            stmt = stmt.with_for_update(skip_locked=True)
        return list((await self._session.execute(stmt)).scalars().all())


class CalendarWatchService:
    """Service managing watch channel registration, expiration detection, and renewal."""

    def __init__(self, settings: GoogleCalendarSettings | None = None) -> None:
        self._settings = settings or GoogleCalendarSettings()

    def _parse_google_expiration(self, raw_exp: Any) -> datetime | None:
        """Parse Google expiration value (epoch ms or ISO string) to timezone-aware UTC datetime."""
        if raw_exp is None:
            return None
        try:
            # Google returns timestamp in milliseconds as string or int
            exp_ms = float(raw_exp)
            return datetime.fromtimestamp(exp_ms / 1000.0, tz=UTC)
        except (ValueError, TypeError):
            pass
        try:
            return datetime.fromisoformat(str(raw_exp).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return None

    async def ensure_watch_channel(
        self,
        session: AsyncSession,
        tenant_id: str,
        external_calendar_id: str,
        adapter: GoogleCalendarAdapter,
        force_renewal: bool = False,
    ) -> CalendarWatchChannelRecord:
        """Ensure an active watch channel exists for the given tenant calendar.

        If a valid active channel exists and is not expiring soon, returns it.
        Otherwise, registers a new channel, atomically persists it, stops the old channel,
        and logs the audit event.
        """
        callback_url = self._settings.webhook_callback_url.strip()
        if not callback_url:
            raise ValueError(
                "E7_GOOGLE_CALENDAR_WEBHOOK_CALLBACK_URL is required to create a watch channel."
            )

        repo = CalendarWatchRepository(session)
        audit_repo = AuditRepository(session)
        provider = "google"

        active_channel = await repo.get_active_channel(tenant_id, provider, external_calendar_id)
        now = datetime.now(UTC)

        if active_channel and not force_renewal:
            if active_channel.expiration_at:
                remaining = (active_channel.expiration_at - now).total_seconds()
                if remaining > DEFAULT_RENEWAL_WINDOW_SECONDS:
                    return active_channel
            else:
                return active_channel

        # Generate cryptographically secure unique channel ID
        channel_id = f"e7-watch-{uuid4()}"
        raw_token = self._settings.webhook_token.get_secret_value().strip()
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest() if raw_token else None

        # Call adapter to register watch on Google
        watch_data = await adapter.watch_events(
            tenant_id=tenant_id,
            channel_id=channel_id,
            callback_url=callback_url,
            external_calendar_id=external_calendar_id,
            token=raw_token if raw_token else None,
            ttl_seconds=self._settings.channel_ttl_seconds,
        )

        returned_res_id = watch_data.get("resourceId")
        returned_exp = self._parse_google_expiration(watch_data.get("expiration"))
        if returned_exp is None and self._settings.channel_ttl_seconds:
            returned_exp = now + timedelta(seconds=self._settings.channel_ttl_seconds)

        # Create new active channel record
        new_record = CalendarWatchChannelRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            provider=provider,
            external_calendar_id=external_calendar_id,
            channel_id=channel_id,
            resource_id=returned_res_id,
            token_hash=token_hash,
            expiration_at=returned_exp,
            status="ACTIVE",
            last_renewed_at=now,
        )
        session.add(new_record)

        # Retire previous channel
        old_channel_id = None
        if active_channel:
            old_channel_id = active_channel.channel_id
            active_channel.status = "STOPPED"
            if active_channel.resource_id:
                try:
                    await adapter.stop_channel(
                        tenant_id=tenant_id,
                        channel_id=active_channel.channel_id,
                        resource_id=active_channel.resource_id,
                    )
                except Exception as stop_exc:
                    logger.warning(
                        "failed_to_stop_old_watch_channel",
                        old_channel_id=active_channel.channel_id,
                        error=str(stop_exc),
                    )

        event_name = "calendar.watch_renewed" if old_channel_id else "calendar.watch_created"
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type=event_name,
                resource_type="calendar_watch_channel",
                resource_id=str(new_record.id),
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "channel_id": channel_id,
                    "external_calendar_id": external_calendar_id,
                    "provider": provider,
                    "expiration_at": returned_exp.isoformat() if returned_exp else None,
                    "replaced_channel_id": old_channel_id,
                },
            )
        )
        await session.flush()
        return new_record

    async def stop_watch_channel(
        self,
        session: AsyncSession,
        tenant_id: str,
        channel_id: str,
        adapter: GoogleCalendarAdapter,
    ) -> bool:
        """Stop an existing active watch channel and update durable state."""
        repo = CalendarWatchRepository(session)
        audit_repo = AuditRepository(session)
        record = await repo.get_by_channel_id(channel_id)
        if not record:
            return False

        if record.resource_id:
            await adapter.stop_channel(
                tenant_id=tenant_id,
                channel_id=record.channel_id,
                resource_id=record.resource_id,
            )

        record.status = "STOPPED"
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="calendar.watch_stopped",
                resource_type="calendar_watch_channel",
                resource_id=str(record.id),
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "channel_id": channel_id,
                    "external_calendar_id": record.external_calendar_id,
                },
            )
        )
        await session.flush()
        return True

    async def renew_expiring_channels(
        self,
        session: AsyncSession,
        adapter: GoogleCalendarAdapter,
        threshold_seconds: int = DEFAULT_RENEWAL_WINDOW_SECONDS,
        for_update: bool = False,
        tenant_id: str | None = None,
    ) -> list[CalendarWatchChannelRecord]:
        """Scan and renew active watch channels that are approaching expiration."""
        repo = CalendarWatchRepository(session)
        cutoff = datetime.now(UTC) + timedelta(seconds=threshold_seconds)
        needing_renewal = await repo.find_channels_needing_renewal(
            cutoff, provider="google", for_update=for_update, tenant_id=tenant_id
        )

        renewed: list[CalendarWatchChannelRecord] = []
        for channel in needing_renewal:
            try:
                new_chan = await self.ensure_watch_channel(
                    session=session,
                    tenant_id=channel.tenant_id,
                    external_calendar_id=channel.external_calendar_id,
                    adapter=adapter,
                    force_renewal=True,
                )
                renewed.append(new_chan)
            except Exception as exc:
                logger.error(
                    "channel_renewal_failed",
                    channel_id=channel.channel_id,
                    tenant_id=channel.tenant_id,
                    error=str(exc),
                )
        return renewed


__all__ = [
    "DEFAULT_RENEWAL_WINDOW_SECONDS",
    "CalendarWatchRepository",
    "CalendarWatchService",
]
