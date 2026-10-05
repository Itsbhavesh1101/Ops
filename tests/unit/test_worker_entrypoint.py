"""Unit tests for outbox worker runtime entrypoint (Task 1J.3B).

Validates:
- Composition: DatabaseManager, UoW factory, unstarted RabbitMqEventPublisher,
  OutboxRunnerPolicy, OutboxDispatchPolicy, and OutboxDispatcherRunner.
- Publisher lifecycle ownership: OutboxDispatcherRunner owns start()/close();
  worker runtime never invokes publisher.start() or publisher.close().
- Consumer absence: zero construction/import of RabbitMqConsumerRunner,
  ConsumerEventProcessor, RabbitMqInboundMessageHandler, or no-op handlers.
- Normal shutdown: clean return upon stop_event; database disposed exactly once.
- Fail-fast: runner failure propagates; database disposal still executes.
- Disposal failure handling: disposal failure propagates on normal exit;
  disposal failure is attached as note if runner already failed.
- Signal handling: POSIX signal registration helper binds SIGTERM/SIGINT to stop_event;
  Windows/unsupported loops do not crash.
- OutboxWorkerSettings validation, environment prefix mapping, and policy factory methods.
"""

import ast
import asyncio
import signal
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import app.worker as worker_module
from app.core.config import Settings
from app.core.telemetry import (
    TelemetryRuntime,
    TelemetrySettings,
    get_telemetry_runtime,
    resolve_telemetry_settings,
)
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.config import OutboxWorkerSettings
from app.events.outbox.dispatcher import OutboxDispatchPolicy
from app.events.outbox.runner import OutboxRunnerPolicy
from app.events.publishers.config import RabbitMqPublisherSettings
from app.worker import _install_signal_handlers, _remove_signal_handlers, main, run_worker

# -----------------------------------------------------------------------------
# Test Environment Fixture
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def default_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal valid environment variables for settings initialization."""
    monkeypatch.setenv("E7_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    monkeypatch.setenv("E7_RABBITMQ_EXCHANGE_NAME", "events")
    monkeypatch.setenv(
        "E7_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/operations_engine",
    )
    monkeypatch.setattr("app.core.telemetry.runtime._ACTIVE_RUNTIME", None)


# -----------------------------------------------------------------------------
# Fakes and Test Doubles
# -----------------------------------------------------------------------------


class FakePublisher:
    """Fake event publisher recording start/close/publish calls."""

    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.start_calls = 0
        self.close_calls = 0

    async def start(self) -> None:
        self.started = True
        self.start_calls += 1

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    async def publish(self, envelope: Any, routing_key: str | None = None) -> None:
        pass


class FakeRunner:
    """Fake outbox dispatcher runner recording run invocations."""

    def __init__(
        self,
        *,
        publisher: Any,
        runner_policy: OutboxRunnerPolicy,
        dispatch_policy: OutboxDispatchPolicy,
        uow_factory: Any,
        raise_on_run: BaseException | None = None,
    ) -> None:
        self.publisher = publisher
        self.runner_policy = runner_policy
        self.dispatch_policy = dispatch_policy
        self.uow_factory = uow_factory
        self.raise_on_run = raise_on_run
        self.run_called = False
        self.received_stop_event: asyncio.Event | None = None

    async def run(self, *, stop_event: asyncio.Event) -> None:
        self.run_called = True
        self.received_stop_event = stop_event
        # In a real runner, OutboxDispatcherRunner starts publisher here
        await self.publisher.start()
        try:
            if self.raise_on_run is not None:
                raise self.raise_on_run
        finally:
            await self.publisher.close()


class FakeDatabaseManager:
    """Fake DatabaseManager recording dispose calls."""

    def __init__(self, *, raise_on_dispose: BaseException | None = None) -> None:
        self.session_factory = MagicMock()
        self.dispose_called = False
        self.dispose_call_count = 0
        self.raise_on_dispose = raise_on_dispose

    async def dispose(self) -> None:
        self.dispose_called = True
        self.dispose_call_count += 1
        if self.raise_on_dispose is not None:
            raise self.raise_on_dispose


# -----------------------------------------------------------------------------
# 1. Composition Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_composition_wiring() -> None:
    """Verify run_worker composes all dependencies and runs the runner."""
    fake_db_manager = FakeDatabaseManager()
    fake_publisher = FakePublisher()
    created_runners: list[FakeRunner] = []

    def fake_db_factory(s: Settings) -> Any:
        return fake_db_manager

    def fake_uow_provider(sf: Any) -> Any:
        return lambda: MagicMock(spec=SqlAlchemyUnitOfWork)

    def fake_pub_factory(ps: RabbitMqPublisherSettings) -> Any:
        return fake_publisher

    def fake_runner_factory(
        *,
        publisher: Any,
        runner_policy: OutboxRunnerPolicy,
        dispatch_policy: OutboxDispatchPolicy,
        uow_factory: Any,
    ) -> Any:
        runner = FakeRunner(
            publisher=publisher,
            runner_policy=runner_policy,
            dispatch_policy=dispatch_policy,
            uow_factory=uow_factory,
        )
        created_runners.append(runner)
        return runner

    stop_event = asyncio.Event()
    stop_event.set()

    await run_worker(
        stop_event=stop_event,
        database_manager_factory=fake_db_factory,
        uow_factory_provider=fake_uow_provider,
        publisher_factory=fake_pub_factory,
        runner_factory=fake_runner_factory,
    )

    assert len(created_runners) == 1
    runner = created_runners[0]
    assert runner.run_called is True
    assert runner.received_stop_event is stop_event
    assert runner.publisher is fake_publisher

    # Verified: runner policy and dispatch policy constructed from defaults
    assert isinstance(runner.runner_policy, OutboxRunnerPolicy)
    assert isinstance(runner.dispatch_policy, OutboxDispatchPolicy)
    assert runner.runner_policy.max_concurrency == 1
    assert runner.runner_policy.poll_interval == timedelta(seconds=1.0)
    assert runner.dispatch_policy.lease_duration == timedelta(seconds=30.0)

    # Database manager disposed exactly once
    assert fake_db_manager.dispose_called is True
    assert fake_db_manager.dispose_call_count == 1


# -----------------------------------------------------------------------------
# 2. Publisher Ownership Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_lifecycle_not_owned_by_worker() -> None:
    """Verify run_worker does NOT directly call publisher.start() or close()."""
    fake_db_manager = FakeDatabaseManager()
    fake_publisher = FakePublisher()

    # Runner that does NOT touch publisher, so we can prove worker didn't either
    class PassiveRunner:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def run(self, *, stop_event: asyncio.Event) -> None:
            pass

    stop_event = asyncio.Event()
    await run_worker(
        stop_event=stop_event,
        database_manager_factory=lambda s: fake_db_manager,
        uow_factory_provider=lambda sf: lambda: None,
        publisher_factory=lambda ps: fake_publisher,
        runner_factory=PassiveRunner,
    )

    # Publisher start/close must remain uncalled by run_worker
    assert fake_publisher.start_calls == 0
    assert fake_publisher.close_calls == 0


def test_worker_source_has_zero_publisher_lifecycle_calls() -> None:
    """AST validation confirming app/worker.py never invokes .start() or .close() on publisher."""
    worker_path = Path(worker_module.__file__)
    source = worker_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("start", "close"):
            # Ensure start/close is not called on any publisher variable
            if isinstance(node.value, ast.Name) and "publisher" in node.value.id.lower():
                pytest.fail(f"Direct publisher lifecycle call detected in worker: {node.attr}")


# -----------------------------------------------------------------------------
# 3. Consumer Absence Tests
# -----------------------------------------------------------------------------


def test_consumer_components_absent_from_worker() -> None:
    """Verify app/worker.py does not import or reference any consumer components."""
    worker_path = Path(worker_module.__file__)
    source = worker_path.read_text(encoding="utf-8")

    forbidden_tokens = [
        "RabbitMqConsumerRunner",
        "ConsumerEventProcessor",
        "RabbitMqInboundMessageHandler",
        "InboxRepository",
        "ConsumerEventHandler",
        "inbound_handler",
        "consume(",
    ]
    for token in forbidden_tokens:
        assert token not in source, f"Forbidden consumer component '{token}' found in app/worker.py"


# -----------------------------------------------------------------------------
# 4. Normal Shutdown Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_shutdown_disposes_database_once() -> None:
    """Verify normal stop_event triggers clean return and single database disposal."""
    fake_db = FakeDatabaseManager()
    fake_pub = FakePublisher()

    stop_event = asyncio.Event()
    stop_event.set()

    await run_worker(
        stop_event=stop_event,
        database_manager_factory=lambda s: fake_db,
        uow_factory_provider=lambda sf: lambda: None,
        publisher_factory=lambda ps: fake_pub,
        runner_factory=lambda **kwargs: FakeRunner(**kwargs),
    )

    assert fake_db.dispose_call_count == 1
    assert fake_pub.started is True
    assert fake_pub.closed is True


# -----------------------------------------------------------------------------
# 5. Fail-Fast Exception Propagation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_failure_propagates_and_disposes_database() -> None:
    """Verify runner exception propagates out while still disposing database resources."""
    fake_db = FakeDatabaseManager()
    synthetic_err = RuntimeError("Outbox worker crashed unexpectedly")

    stop_event = asyncio.Event()
    with pytest.raises(RuntimeError, match="Outbox worker crashed unexpectedly"):
        await run_worker(
            stop_event=stop_event,
            database_manager_factory=lambda s: fake_db,
            uow_factory_provider=lambda sf: lambda: None,
            publisher_factory=lambda ps: FakePublisher(),
            runner_factory=lambda **kwargs: FakeRunner(raise_on_run=synthetic_err, **kwargs),
        )

    # Database was still disposed
    assert fake_db.dispose_called is True
    assert fake_db.dispose_call_count == 1


# -----------------------------------------------------------------------------
# 6. Database Disposal Failure Handling Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_database_disposal_failure_on_clean_run_propagates() -> None:
    """If runner completes cleanly but DB disposal fails, disposal error propagates."""
    disposal_err = RuntimeError("DB connection pool disposal failed")
    fake_db = FakeDatabaseManager(raise_on_dispose=disposal_err)

    stop_event = asyncio.Event()
    with pytest.raises(RuntimeError, match="DB connection pool disposal failed"):
        await run_worker(
            stop_event=stop_event,
            database_manager_factory=lambda s: fake_db,
            uow_factory_provider=lambda sf: lambda: None,
            publisher_factory=lambda ps: FakePublisher(),
            runner_factory=lambda **kwargs: FakeRunner(**kwargs),
        )


@pytest.mark.asyncio
async def test_runner_failure_preserves_primary_when_disposal_fails() -> None:
    """If runner fails and DB disposal also fails, primary runner error is preserved."""
    primary_err = RuntimeError("Primary runner failure")
    disposal_err = RuntimeError("Secondary disposal failure")
    fake_db = FakeDatabaseManager(raise_on_dispose=disposal_err)

    stop_event = asyncio.Event()
    with pytest.raises(RuntimeError, match="Primary runner failure") as exc_info:
        await run_worker(
            stop_event=stop_event,
            database_manager_factory=lambda s: fake_db,
            uow_factory_provider=lambda sf: lambda: None,
            publisher_factory=lambda ps: FakePublisher(),
            runner_factory=lambda **kwargs: FakeRunner(raise_on_run=primary_err, **kwargs),
        )

    # Primary error preserved
    err = exc_info.value
    assert err is primary_err

    # Secondary error attached as note (Python 3.11+)
    if hasattr(err, "__notes__"):
        assert any("Secondary failure during DatabaseManager disposal" in n for n in err.__notes__)


# -----------------------------------------------------------------------------
# 7. Signal Handling Tests
# -----------------------------------------------------------------------------


def test_signal_handling_on_windows_does_not_crash() -> None:
    """Verify signal registration does not crash on Windows."""
    stop_event = asyncio.Event()
    with patch("sys.platform", "win32"):
        installed = _install_signal_handlers(stop_event)
        assert installed == []
        _remove_signal_handlers(installed)


@pytest.mark.asyncio
async def test_signal_handling_posix_registration() -> None:
    """Verify signal registration registers SIGTERM and SIGINT on POSIX."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    mock_add = MagicMock()
    mock_remove = MagicMock()

    with (
        patch("sys.platform", "linux"),
        patch.object(loop, "add_signal_handler", mock_add),
        patch.object(loop, "remove_signal_handler", mock_remove),
    ):
        installed = _install_signal_handlers(stop_event)
        assert signal.SIGTERM in installed
        assert signal.SIGINT in installed
        assert mock_add.call_count == 2

        _remove_signal_handlers(installed)
        assert mock_remove.call_count == 2


# -----------------------------------------------------------------------------
# 8. OutboxWorkerSettings Tests
# -----------------------------------------------------------------------------


def test_outbox_worker_settings_defaults() -> None:
    """Verify default outbox worker settings match expected Phase-1 bounds."""
    settings = OutboxWorkerSettings()
    assert settings.outbox_poll_interval_seconds == 1.0
    assert settings.outbox_max_concurrency == 1
    assert settings.outbox_shutdown_grace_seconds == 10.0
    assert settings.outbox_lease_duration_seconds == 30.0
    assert settings.outbox_publish_timeout_seconds == 5.0
    assert settings.outbox_retry_base_delay_seconds == 1.0
    assert settings.outbox_retry_max_delay_seconds == 60.0
    assert settings.outbox_retry_multiplier == 2.0

    # Policy creation helpers
    runner_policy = settings.create_runner_policy()
    assert isinstance(runner_policy, OutboxRunnerPolicy)
    assert runner_policy.poll_interval == timedelta(seconds=1.0)
    assert runner_policy.max_concurrency == 1
    assert runner_policy.shutdown_grace_period == timedelta(seconds=10.0)

    dispatch_policy = settings.create_dispatch_policy()
    assert isinstance(dispatch_policy, OutboxDispatchPolicy)
    assert dispatch_policy.lease_duration == timedelta(seconds=30.0)
    assert dispatch_policy.publish_timeout == timedelta(seconds=5.0)
    assert dispatch_policy.retry_base_delay == timedelta(seconds=1.0)
    assert dispatch_policy.retry_max_delay == timedelta(seconds=60.0)
    assert dispatch_policy.retry_multiplier == 2.0


def test_outbox_worker_settings_environment_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify settings parse correctly from E7_ environment variables."""
    monkeypatch.setenv("E7_OUTBOX_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("E7_OUTBOX_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("E7_OUTBOX_SHUTDOWN_GRACE_SECONDS", "15.0")
    monkeypatch.setenv("E7_OUTBOX_LEASE_DURATION_SECONDS", "45.0")
    monkeypatch.setenv("E7_OUTBOX_PUBLISH_TIMEOUT_SECONDS", "10.0")
    monkeypatch.setenv("E7_OUTBOX_RETRY_BASE_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("E7_OUTBOX_RETRY_MAX_DELAY_SECONDS", "120.0")
    monkeypatch.setenv("E7_OUTBOX_RETRY_MULTIPLIER", "3.0")

    settings = OutboxWorkerSettings()
    assert settings.outbox_poll_interval_seconds == 2.5
    assert settings.outbox_max_concurrency == 4
    assert settings.outbox_shutdown_grace_seconds == 15.0
    assert settings.outbox_lease_duration_seconds == 45.0
    assert settings.outbox_publish_timeout_seconds == 10.0
    assert settings.outbox_retry_base_delay_seconds == 0.5
    assert settings.outbox_retry_max_delay_seconds == 120.0
    assert settings.outbox_retry_multiplier == 3.0


def test_outbox_worker_settings_validation_errors() -> None:
    """Verify strict validation of bounds on outbox settings."""
    # publish_timeout >= lease_duration must be rejected
    with pytest.raises(ValidationError, match="strictly less than outbox_lease_duration_seconds"):
        OutboxWorkerSettings(
            outbox_publish_timeout_seconds=30.0,
            outbox_lease_duration_seconds=30.0,
        )

    # retry_max < retry_base must be rejected
    with pytest.raises(ValidationError, match="greater than or equal to outbox_retry_base_delay"):
        OutboxWorkerSettings(
            outbox_retry_base_delay_seconds=10.0,
            outbox_retry_max_delay_seconds=5.0,
        )

    # non-positive max_concurrency must be rejected
    with pytest.raises(ValidationError):
        OutboxWorkerSettings(outbox_max_concurrency=0)

    # non-positive durations must be rejected
    with pytest.raises(ValidationError):
        OutboxWorkerSettings(outbox_poll_interval_seconds=-1.0)


# -----------------------------------------------------------------------------
# 9. Main CLI Entrypoint Tests
# -----------------------------------------------------------------------------


def test_main_propagates_unhandled_exceptions() -> None:
    """Verify main() allows unhandled exceptions to bubble out without swallowing."""
    with patch("app.worker.run_worker", side_effect=RuntimeError("Fatal error")):
        with pytest.raises(RuntimeError, match="Fatal error"):
            main()


# -----------------------------------------------------------------------------
# 10. Telemetry Lifecycle and Ordering Tests (Task 1K.3)
# -----------------------------------------------------------------------------


def test_worker_import_is_telemetry_inert() -> None:
    """Verify importing app.worker performs zero telemetry initialization."""
    assert get_telemetry_runtime() is None
    assert hasattr(worker_module, "run_worker")
    assert hasattr(worker_module, "main")


@pytest.mark.asyncio
async def test_worker_telemetry_ordering_clean_run() -> None:
    """Verify clean worker run follows exact required ordering:

    telemetry.init -> runner.run -> runner.return -> telemetry.shutdown -> db.dispose
    """
    events: list[str] = []

    def fake_telemetry_init(
        settings: TelemetrySettings, environment: str = "local"
    ) -> TelemetryRuntime:
        events.append(f"telemetry.init:{settings.otel_service_name}:{environment}")
        return TelemetryRuntime(enabled=True)

    def fake_telemetry_shutdown() -> None:
        events.append("telemetry.shutdown")

    class OrderedRunner:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, *, stop_event: asyncio.Event) -> None:
            events.append("runner.run")
            events.append("runner.return")

    class OrderedDbManager:
        def __init__(self) -> None:
            self.session_factory = MagicMock()

        async def dispose(self) -> None:
            events.append("db.dispose")

    stop_event = asyncio.Event()
    stop_event.set()

    await run_worker(
        stop_event=stop_event,
        telemetry_initializer=fake_telemetry_init,
        telemetry_shutdown=fake_telemetry_shutdown,
        database_manager_factory=lambda s: OrderedDbManager(),
        uow_factory_provider=lambda sf: lambda: None,
        publisher_factory=lambda ps: FakePublisher(),
        runner_factory=OrderedRunner,
    )

    assert events == [
        "telemetry.init:e7-worker:local",
        "runner.run",
        "runner.return",
        "telemetry.shutdown",
        "db.dispose",
    ]


@pytest.mark.asyncio
async def test_worker_telemetry_init_failure_prevents_runner_and_db() -> None:
    """If telemetry initialization raises, DB and runner are never constructed."""
    db_factory_called = False
    runner_factory_called = False

    def failing_telemetry_init(settings: TelemetrySettings, environment: str = "local") -> Any:
        raise RuntimeError("Telemetry init crashed")

    def spy_db_factory(s: Settings) -> Any:
        nonlocal db_factory_called
        db_factory_called = True
        return FakeDatabaseManager()

    def spy_runner_factory(**kwargs: Any) -> Any:
        nonlocal runner_factory_called
        runner_factory_called = True
        return FakeRunner(**kwargs)

    stop_event = asyncio.Event()
    with pytest.raises(RuntimeError, match="Telemetry init crashed"):
        await run_worker(
            stop_event=stop_event,
            telemetry_initializer=failing_telemetry_init,
            database_manager_factory=spy_db_factory,
            runner_factory=spy_runner_factory,
        )

    assert db_factory_called is False
    assert runner_factory_called is False


@pytest.mark.asyncio
async def test_worker_telemetry_shutdown_failure_on_clean_run_propagates() -> None:
    """If clean run but telemetry shutdown fails, database is still disposed
    and error propagates."""
    fake_db = FakeDatabaseManager()

    def failing_telemetry_shutdown() -> None:
        raise RuntimeError("Telemetry shutdown flush failed")

    stop_event = asyncio.Event()
    stop_event.set()

    with pytest.raises(RuntimeError, match="Telemetry shutdown flush failed"):
        await run_worker(
            stop_event=stop_event,
            telemetry_shutdown=failing_telemetry_shutdown,
            database_manager_factory=lambda s: fake_db,
            uow_factory_provider=lambda sf: lambda: None,
            publisher_factory=lambda ps: FakePublisher(),
            runner_factory=lambda **kwargs: FakeRunner(**kwargs),
        )

    # Database is still cleanly disposed
    assert fake_db.dispose_called is True
    assert fake_db.dispose_call_count == 1


@pytest.mark.asyncio
async def test_worker_runner_failure_preserves_primary_when_telemetry_and_db_fail() -> None:
    """If runner fails and both telemetry shutdown and DB disposal fail,
    primary error is preserved."""
    primary_err = RuntimeError("Fatal outbox runner crashed")
    fake_db = FakeDatabaseManager(raise_on_dispose=RuntimeError("Secondary DB failure"))

    def failing_telemetry_shutdown() -> None:
        raise RuntimeError("Secondary telemetry failure")

    stop_event = asyncio.Event()
    with pytest.raises(RuntimeError, match="Fatal outbox runner crashed") as exc_info:
        await run_worker(
            stop_event=stop_event,
            telemetry_shutdown=failing_telemetry_shutdown,
            database_manager_factory=lambda s: fake_db,
            uow_factory_provider=lambda sf: lambda: None,
            publisher_factory=lambda ps: FakePublisher(),
            runner_factory=lambda **kwargs: FakeRunner(raise_on_run=primary_err, **kwargs),
        )

    err = exc_info.value
    assert err is primary_err
    assert hasattr(err, "__notes__")
    assert any("Secondary failure during telemetry shutdown" in n for n in err.__notes__)
    assert any("Secondary failure during DatabaseManager disposal" in n for n in err.__notes__)


def test_worker_service_identity_defaults_to_e7_worker() -> None:
    """When E7_OTEL_SERVICE_NAME is unset, resolve_telemetry_settings defaults to e7-worker."""
    settings = resolve_telemetry_settings(
        default_service_name="e7-worker",
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    assert settings.otel_service_name == "e7-worker"
    assert settings.otel_enabled is True


def test_worker_service_identity_preserves_operator_override() -> None:
    """When operator explicitly sets E7_OTEL_SERVICE_NAME, that value takes precedence."""
    settings = resolve_telemetry_settings(
        default_service_name="e7-worker",
        _env_file=None,
        otel_enabled=True,
        otel_service_name="custom-operations-worker",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    assert settings.otel_service_name == "custom-operations-worker"
