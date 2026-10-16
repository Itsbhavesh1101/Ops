"""Background worker and recurring runtime triggers for Google Calendar integration.

Part of Phase 7C Google Calendar Production Closure Reconciliation.
Provides:
- Recurring watch channel renewal trigger (CalendarWorker.run_renewal_cycle)
- Recurring inbound calendar reconciliation trigger (CalendarWorker.run_reconciliation_cycle)
- Bounded polling cadence driven by GoogleCalendarSettings
- Multi-worker concurrency safety via row-level locks (SKIP LOCKED) preventing duplicate renewals
- Full reuse of CalendarWatchService, CalendarReconciliationService, and InboundCalendarSyncService
- Graceful shutdown and database resource management conforming to E-7 worker architecture.

Runtime Call Chains:
Watch Renewal:
  CalendarWorker (worker)
  -> run_renewal_cycle (recurring trigger)
  -> CalendarWatchService.renew_expiring_channels (watch renewal service)
  -> CalendarWatchRepository.find_channels_needing_renewal (renewal query)
  -> CalendarWatchService.ensure_watch_channel / adapter.watch_events (watch registration)
  -> session.add(CalendarWatchChannelRecord) / session.commit (durable state update)
  -> adapter.stop_channel (old channel stop)

Periodic Reconciliation:
  CalendarWorker (worker)
  -> run_reconciliation_cycle (recurring trigger)
  -> select enabled/configured calendar scopes from calendar_watch_channels
  -> CalendarReconciliationService.reconcile_inbound_calendar
  -> InboundCalendarSyncService.import_incremental_changes
  -> CalendarLinkRecord update (syncToken update)
  -> session.commit (preserves idempotency and audit state)
"""

import asyncio
import signal
import sys

import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import DatabaseManager
from app.db.models.scheduling import CalendarWatchChannelRecord
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.inbound import InboundCalendarSyncService
from app.integrations.calendar.reconciliation import CalendarReconciliationService
from app.integrations.calendar.watch import (
    DEFAULT_RENEWAL_WINDOW_SECONDS,
    CalendarWatchService,
)

logger = structlog.get_logger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event) -> list[signal.Signals]:
    """Register SIGTERM and SIGINT handlers on POSIX platforms."""
    installed: list[signal.Signals] = []
    if sys.platform == "win32":
        return installed

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return installed

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    return installed


def _remove_signal_handlers(signals: list[signal.Signals]) -> None:
    """Remove previously registered POSIX signal handlers."""
    if sys.platform == "win32" or not signals:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    for sig in signals:
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass


class CalendarWorker:
    """Dedicated background worker for Google Calendar watch renewal and reconciliation."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        settings: GoogleCalendarSettings | None = None,
        adapter: GoogleCalendarAdapter | None = None,
        watch_service: CalendarWatchService | None = None,
        reconciliation_service: CalendarReconciliationService | None = None,
        sync_service: InboundCalendarSyncService | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.settings = settings or GoogleCalendarSettings()
        self.adapter = adapter or GoogleCalendarAdapter(settings=self.settings)
        self.watch_service = watch_service or CalendarWatchService(settings=self.settings)
        self.reconciliation_service = reconciliation_service or CalendarReconciliationService(
            adapter=self.adapter
        )
        self.sync_service = sync_service or InboundCalendarSyncService()
        self._stop_event = asyncio.Event()

    async def run_renewal_cycle(
        self,
        threshold_seconds: int = DEFAULT_RENEWAL_WINDOW_SECONDS,
        tenant_id: str | None = None,
    ) -> int:
        """Execute a single watch channel renewal cycle.

        Exact call chain:
        worker -> recurring trigger -> CalendarWatchService -> renewal query
        -> watch registration -> durable state update -> old channel stop.
        """
        renewed_count = 0
        async with self.db_manager.session_factory() as session:
            try:
                renewed = await self.watch_service.renew_expiring_channels(
                    session=session,
                    adapter=self.adapter,
                    threshold_seconds=threshold_seconds,
                    for_update=True,
                    tenant_id=tenant_id,
                )
                await session.commit()
                renewed_count = len(renewed)
                if renewed_count > 0:
                    logger.info(
                        "calendar_watch_renewal_cycle_completed",
                        renewed_count=renewed_count,
                    )
            except Exception as exc:
                await session.rollback()
                logger.error(
                    "calendar_watch_renewal_cycle_failed",
                    error=str(exc),
                )
                raise
        return renewed_count

    async def run_reconciliation_cycle(self, tenant_id: str | None = None) -> int:
        """Execute a single inbound reconciliation cycle across configured/active calendars.

        Exact call chain:
        worker -> recurring trigger -> select scopes -> reconcile_inbound_calendar
        -> InboundCalendarSyncService -> update syncToken -> commit.
        """
        processed_scopes = 0
        async with self.db_manager.session_factory() as session:
            # Select distinct active calendar scopes needing reconciliation
            stmt = select(
                CalendarWatchChannelRecord.tenant_id,
                CalendarWatchChannelRecord.external_calendar_id,
                CalendarWatchChannelRecord.provider,
            ).where(CalendarWatchChannelRecord.status == "ACTIVE")
            if tenant_id:
                stmt = stmt.where(CalendarWatchChannelRecord.tenant_id == tenant_id)
            stmt = stmt.distinct()
            scopes = (await session.execute(stmt)).all()

        # Reconcile each calendar scope in its own transaction for failure isolation
        for tenant_id, external_calendar_id, provider in scopes:
            async with self.db_manager.session_factory() as scope_session:
                try:
                    result = await self.reconciliation_service.reconcile_inbound_calendar(
                        session=scope_session,
                        tenant_id=tenant_id,
                        external_calendar_id=external_calendar_id,
                        provider=provider,
                        sync_service=self.sync_service,
                    )
                    await scope_session.commit()
                    processed_scopes += 1
                    logger.info(
                        "calendar_inbound_reconciliation_scope_completed",
                        tenant_id=tenant_id,
                        external_calendar_id=external_calendar_id,
                        created_count=len(result.created_or_updated),
                        deleted_count=len(result.deleted_external_ids),
                    )
                except Exception as exc:
                    await scope_session.rollback()
                    logger.error(
                        "calendar_inbound_reconciliation_scope_failed",
                        tenant_id=tenant_id,
                        external_calendar_id=external_calendar_id,
                        error=str(exc),
                    )

        return processed_scopes

    async def run_once(self) -> dict[str, int]:
        """Execute a single pass of watch renewal and inbound reconciliation."""
        renewed = await self.run_renewal_cycle()
        reconciled = await self.run_reconciliation_cycle()
        return {
            "channels_renewed": renewed,
            "scopes_reconciled": reconciled,
        }

    async def _renewal_loop(self, stop_event: asyncio.Event) -> None:
        """Periodic watch channel renewal loop."""
        interval = max(self.settings.renewal_interval_seconds, 1.0)
        logger.info("calendar_watch_renewal_loop_started", interval_seconds=interval)
        while not stop_event.is_set():
            try:
                await self.run_renewal_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("calendar_watch_renewal_cycle_failed", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
        logger.info("calendar_watch_renewal_loop_stopped")

    async def _reconciliation_loop(self, stop_event: asyncio.Event) -> None:
        """Periodic inbound calendar reconciliation loop."""
        interval = max(self.settings.reconciliation_interval_seconds, 1.0)
        logger.info("calendar_inbound_reconciliation_loop_started", interval_seconds=interval)
        while not stop_event.is_set():
            try:
                await self.run_reconciliation_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("calendar_inbound_reconciliation_cycle_failed", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
        logger.info("calendar_inbound_reconciliation_loop_stopped")

    def start_background_tasks(self, stop_event: asyncio.Event) -> list[asyncio.Task[None]]:
        """Start both recurring calendar loops as background asyncio tasks."""
        renewal_task = asyncio.create_task(
            self._renewal_loop(stop_event),
            name="calendar_watch_renewal_loop",
        )
        recon_task = asyncio.create_task(
            self._reconciliation_loop(stop_event),
            name="calendar_inbound_reconciliation_loop",
        )
        return [renewal_task, recon_task]

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Run the periodic renewal and reconciliation loop until stop_event is set."""
        if stop_event is not None:
            self._stop_event = stop_event

        signals = _install_signal_handlers(self._stop_event)
        logger.info(
            "starting_calendar_worker",
            reconciliation_interval_seconds=self.settings.reconciliation_interval_seconds,
            renewal_interval_seconds=self.settings.renewal_interval_seconds,
            enabled=self.settings.enabled,
        )

        tasks = self.start_background_tasks(self._stop_event)
        try:
            await self._stop_event.wait()
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            _remove_signal_handlers(signals)
            logger.info("calendar_worker_stopped")


async def main() -> None:
    """Entry point for python -m app.integrations.calendar.worker."""
    app_settings = get_settings()
    setup_logging(app_settings)
    cal_settings = GoogleCalendarSettings()

    db_manager = DatabaseManager(app_settings)
    worker = CalendarWorker(db_manager=db_manager, settings=cal_settings)

    try:
        await worker.run()
    finally:
        await db_manager.dispose()


if __name__ == "__main__":
    asyncio.run(main())
