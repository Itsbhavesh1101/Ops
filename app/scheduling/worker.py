"""Dedicated asynchronous scheduling worker runtime.

Part of Phase 4A Generic Scheduling Core.
Responsibilities:
- Poll for REQUESTED schedule jobs using atomic SELECT FOR UPDATE SKIP LOCKED.
- Transition claimed jobs to SOLVING under a worker lease.
- Execute OR-Tools CP-SAT solve deterministically with timeout protection.
- Persist FEASIBLE candidate schedules with zero hard violations, or INFEASIBLE diagnostics.
- Ensure concurrent safety (two workers never solve the same job).
- Safe graceful shutdown and database resource disposal.
"""

import asyncio
import signal
import sys
from datetime import UTC, datetime

import structlog

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import DatabaseManager
from app.scheduling.config import SchedulerSettings
from app.scheduling.service import SchedulingService

logger = structlog.get_logger("app.scheduling.worker")


def _install_signal_handlers(
    stop_event: asyncio.Event,
) -> list[signal.Signals]:
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


class SchedulerWorker:
    """Dedicated background worker for claiming and executing scheduling jobs."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        settings: SchedulerSettings,
        service: SchedulingService | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.settings = settings
        self.service = service or SchedulingService()
        self._stop_event = asyncio.Event()

    async def run_once(self) -> int:
        """Execute a single polling and solve batch. Returns number of jobs processed."""
        processed_count = 0
        async with self.db_manager.session_factory() as session:
            async with session.begin():
                jobs = await self.service.claim_jobs(
                    session=session,
                    worker_id=self.settings.worker_id,
                    batch_size=self.settings.claim_batch_size,
                    max_concurrent_per_tenant=self.settings.max_concurrent_per_tenant,
                )

            # Settle each claimed job in its own transaction
            for job in jobs:
                async with self.db_manager.session_factory() as solve_session:
                    async with solve_session.begin():
                        try:
                            # Apply solve timeout
                            await asyncio.wait_for(
                                self.service.execute_job_solve(solve_session, job.id),
                                timeout=self.settings.solve_timeout_seconds,
                            )
                            processed_count += 1
                        except TimeoutError:
                            logger.error(
                                "Scheduler solve timed out",
                                job_id=str(job.id),
                                timeout=self.settings.solve_timeout_seconds,
                            )
                            job_rec = await self.service.get_job(
                                solve_session, job.tenant_id, job.id
                            )
                            if job_rec:
                                job_rec.status = "BUDGET_EXHAUSTED"
                                job_rec.failure_message = (
                                    f"Solve timeout after {self.settings.solve_timeout_seconds}s "
                                    "(budget exhausted without feasible solution)"
                                )
                                job_rec.completed_at = datetime.now(UTC)
                        except Exception:
                            logger.exception(
                                "Error executing schedule job solve",
                                job_id=str(job.id),
                            )
                            # execute_job_solve already handles marking job FAILED
                            pass

        return processed_count

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Run the main polling loop until stop_event is set."""
        if stop_event is not None:
            self._stop_event = stop_event

        signals = _install_signal_handlers(self._stop_event)
        logger.info(
            "Starting scheduler worker",
            worker_id=self.settings.worker_id,
            poll_interval=self.settings.poll_interval_seconds,
            batch_size=self.settings.claim_batch_size,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    processed = await self.run_once()
                    if processed == 0:
                        # Sleep poll interval if no jobs were found
                        try:
                            await asyncio.wait_for(
                                self._stop_event.wait(),
                                timeout=self.settings.poll_interval_seconds,
                            )
                        except TimeoutError:
                            pass
                except Exception as exc:
                    logger.error("Error in scheduler worker poll cycle", error=str(exc))
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.settings.poll_interval_seconds,
                        )
                    except TimeoutError:
                        pass
        finally:
            _remove_signal_handlers(signals)
            logger.info("Scheduler worker stopped", worker_id=self.settings.worker_id)


async def main() -> None:
    """Entry point for python -m app.scheduling.worker."""
    app_settings = get_settings()
    setup_logging(app_settings)
    # Loads required configuration from ambient E7_SCHEDULER_* environment variables
    scheduler_settings = SchedulerSettings()  # type: ignore[call-arg]

    db_manager = DatabaseManager(app_settings)
    worker = SchedulerWorker(db_manager=db_manager, settings=scheduler_settings)

    try:
        await worker.run()
    finally:
        await db_manager.dispose()


if __name__ == "__main__":
    asyncio.run(main())
