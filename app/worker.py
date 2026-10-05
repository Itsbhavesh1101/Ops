"""Outbox-only background worker runtime entrypoint.

Part of Phase 1 Foundation Task 1J.3B.
Composes frozen Phase-1 infrastructure components:
- DatabaseManager (lifecycle owner for AsyncEngine and sessionmaker)
- SqlAlchemyUnitOfWorkFactory (canonical UoW factory provider)
- RabbitMqEventPublisher (instantiated and passed unstarted)
- OutboxWorkerSettings -> OutboxRunnerPolicy & OutboxDispatchPolicy
- OutboxDispatcherRunner (owns bounded concurrency and publisher lifecycle)

Invariants:
- Outbox-only: zero RabbitMQ consumer runner, zero inbound message handler,
  zero no-op consumer event handler.
- Publisher lifecycle owned strictly by OutboxDispatcherRunner.start() and close().
  The worker runtime never calls publisher.start() or publisher.close().
- Database disposal is guaranteed on all exit paths (clean shutdown, cancellation, failure).
- Fail-fast: unhandled runner or startup failures propagate to terminate the process non-zero.
- Container-safe signal handling: SIGTERM and SIGINT trigger graceful stop_event on POSIX.
- Zero hardcoded operational literals: all durations and concurrency bounds are
  configuration-driven.
"""

import asyncio
import signal
import sys
from collections.abc import Callable
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.telemetry import (
    TelemetryRuntime,
    TelemetrySettings,
    initialize_telemetry,
    resolve_telemetry_settings,
    shutdown_telemetry,
)
from app.db.engine import DatabaseManager
from app.db.uow import UnitOfWork, create_unit_of_work_factory
from app.events.outbox.config import OutboxWorkerSettings
from app.events.outbox.runner import ManagedEventPublisher, OutboxDispatcherRunner
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher

logger = structlog.get_logger("app.worker")


def _install_signal_handlers(
    stop_event: asyncio.Event,
) -> list[signal.Signals]:
    """Register SIGTERM and SIGINT handlers on POSIX platforms to set stop_event.

    Safely guards against Windows and non-standard event loops where add_signal_handler
    is not implemented or permitted.
    """
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


async def run_worker(
    *,
    stop_event: asyncio.Event | None = None,
    settings: Settings | None = None,
    publisher_settings: RabbitMqPublisherSettings | None = None,
    outbox_settings: OutboxWorkerSettings | None = None,
    telemetry_settings: TelemetrySettings | None = None,
    database_manager_factory: Callable[[Settings], DatabaseManager] = DatabaseManager,
    uow_factory_provider: Callable[[Any], Callable[[], UnitOfWork]] = create_unit_of_work_factory,
    publisher_factory: Callable[[RabbitMqPublisherSettings], ManagedEventPublisher] = (
        RabbitMqEventPublisher
    ),
    runner_factory: Callable[..., OutboxDispatcherRunner] = OutboxDispatcherRunner,
    telemetry_initializer: Callable[..., TelemetryRuntime] = initialize_telemetry,
    telemetry_shutdown: Callable[[], None] = shutdown_telemetry,
) -> None:
    """Run the outbox dispatcher worker process until stopped.

    Coordinates runtime dependencies:
    1. Resolves application, publisher, and outbox settings.
    2. Resolves and initializes OpenTelemetry runtime (canonical identity: e7-worker).
    3. Instantiates DatabaseManager and constructs canonical UoW factory.
    4. Instantiates unstarted RabbitMqEventPublisher.
    5. Constructs OutboxRunnerPolicy and OutboxDispatchPolicy from outbox settings.
    6. Instantiates OutboxDispatcherRunner.
    7. Registers POSIX OS signals (SIGTERM/SIGINT) if stop_event is managed internally.
    8. Runs runner.run(stop_event=actual_stop_event) allowing runner to manage publisher lifecycle.
    9. Shuts down OpenTelemetry runtime before database disposal.
    10. Disposes DatabaseManager cleanly on all return or failure paths.

    Args:
        stop_event: Optional external stop signal. If None, an internal event is created
            and bound to OS termination signals on POSIX systems.
        settings: Optional application settings override.
        publisher_settings: Optional RabbitMQ publisher settings override.
        outbox_settings: Optional outbox operational settings override.
        telemetry_settings: Optional telemetry operational settings override.
        database_manager_factory: Seam for constructing DatabaseManager (testing).
        uow_factory_provider: Seam for producing UoW factory callable (testing).
        publisher_factory: Seam for constructing ManagedEventPublisher (testing).
        runner_factory: Seam for constructing OutboxDispatcherRunner (testing).
        telemetry_initializer: Seam for initializing OpenTelemetry runtime (testing).
        telemetry_shutdown: Seam for shutting down OpenTelemetry runtime (testing).

    Raises:
        BaseException: Any unhandled runner or infrastructure failure propagates out.
    """
    app_settings = settings or get_settings()
    setup_logging(app_settings)

    # 1. Resolve and validate telemetry settings (canonical identity: e7-worker)
    tel_settings = telemetry_settings or resolve_telemetry_settings(
        default_service_name="e7-worker"
    )

    # 2. Initialize telemetry runtime BEFORE database or runner construction
    # If this raises, DB has not been constructed yet, runner is not started, failure propagates.
    _telemetry_runtime = telemetry_initializer(
        tel_settings,
        environment=app_settings.environment,
    )

    pub_settings = (
        publisher_settings if publisher_settings is not None else RabbitMqPublisherSettings()  # type: ignore[call-arg]
    )
    out_settings = outbox_settings or OutboxWorkerSettings()

    actual_stop_event = stop_event if stop_event is not None else asyncio.Event()
    installed_signals: list[signal.Signals] = []
    if stop_event is None:
        installed_signals = _install_signal_handlers(actual_stop_event)

    db_manager = database_manager_factory(app_settings)
    uow_factory = uow_factory_provider(db_manager.session_factory)

    # Publisher is passed UNSTARTED. OutboxDispatcherRunner owns publisher start/close.
    publisher = publisher_factory(pub_settings)

    runner_policy = out_settings.create_runner_policy()
    dispatch_policy = out_settings.create_dispatch_policy()

    runner = runner_factory(
        publisher=publisher,
        runner_policy=runner_policy,
        dispatch_policy=dispatch_policy,
        uow_factory=uow_factory,
    )

    logger.info(
        "worker_starting",
        role="outbox_only",
        concurrency=runner_policy.max_concurrency,
        poll_interval=runner_policy.poll_interval.total_seconds(),
        lease_duration=dispatch_policy.lease_duration.total_seconds(),
    )

    primary_error: BaseException | None = None
    try:
        await runner.run(stop_event=actual_stop_event)
        logger.info("worker_stopped_cleanly", role="outbox_only")
    except BaseException as exc:
        primary_error = exc
        logger.error(
            "worker_failed",
            role="outbox_only",
            error_type=type(exc).__name__,
        )
        raise
    finally:
        _remove_signal_handlers(installed_signals)

        # 4. Telemetry runtime force-flushes/shuts down BEFORE database disposal
        try:
            telemetry_shutdown()
            logger.info("telemetry_runtime_shut_down", role="outbox_only")
        except BaseException as tel_exc:
            logger.error(
                "telemetry_shutdown_failed",
                role="outbox_only",
                error_type=type(tel_exc).__name__,
            )
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(
                        f"Secondary failure during telemetry shutdown: "
                        f"{type(tel_exc).__name__}: {tel_exc}"
                    )
            else:
                primary_error = tel_exc

        # 5. Database disposal is guaranteed on all exit paths
        try:
            await db_manager.dispose()
            logger.info("database_manager_disposed", role="outbox_only")
        except BaseException as cleanup_exc:
            logger.error(
                "database_disposal_failed",
                role="outbox_only",
                error_type=type(cleanup_exc).__name__,
            )
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(
                        f"Secondary failure during DatabaseManager disposal: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            else:
                primary_error = cleanup_exc

        if primary_error is not None:
            raise primary_error


def main() -> None:
    """Synchronous process entrypoint for 'python -m app.worker'."""
    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        # Re-raise so process exits non-zero without silent error swallowing
        raise


if __name__ == "__main__":
    main()
