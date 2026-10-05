"""Unit tests for OutboxDispatcherRunner and OutboxRunnerPolicy.

Part of Phase 1 Foundation Task 1G.3B3.
Validates:
- OutboxRunnerPolicy validation, bounds checking, and strict immutability.
- No network I/O or worker creation upon runner instantiation.
- Pre-set stop_event cleanly exits without starting publisher or creating workers.
- Startup sequencing: publisher.start() executes before any worker dispatch call.
- Startup failure propagation: zero workers started, zero dispatch calls.
- Bounded concurrency: exactly max_concurrency workers created and running concurrently.
- Idle polling cadence with stop-aware prompt wakeup.
- Non-idle continuation without arbitrary sleep intervals.
- Normal graceful shutdown: in-flight dispatches allowed to complete within grace period.
- Grace period expiry: forced cancellation of hung workers with awaited cancellation.
- Publisher close ordering: publisher.close() executes only after all workers terminate.
- Worker failure supervision: sibling workers cancelled, publisher closed, primary error propagates.
- Primary error preservation when publisher.close() fails secondarily.
- Close failure propagation during normal external shutdown.
- Outer runner task cancellation propagation with clean teardown.
- One-shot lifecycle enforcement (no duplicate or restart calls).
- Architectural boundaries: zero aio_pika, zero RabbitMQ imports, zero DB sessions.
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.events.outbox.dispatcher import (
    DispatchOutcome,
    DispatchResult,
    OutboxDispatchPolicy,
)
from app.events.outbox.runner import (
    OutboxDispatcherRunner,
    OutboxRunnerPolicy,
    RunnerState,
)
from app.events.schemas import EventEnvelope

# -----------------------------------------------------------------------------
# Test Helpers & Fakes
# -----------------------------------------------------------------------------


class FakeManagedPublisher:
    """Fake publisher implementing ManagedEventPublisher with timeline recording."""

    def __init__(
        self,
        *,
        start_error: Exception | None = None,
        close_error: Exception | None = None,
        publish_error: Exception | None = None,
        publish_delay: float = 0.0,
        timeline: list[str] | None = None,
    ) -> None:
        self.start_error = start_error
        self.close_error = close_error
        self.publish_error = publish_error
        self.publish_delay = publish_delay
        self.timeline = timeline if timeline is not None else []
        self.start_call_count = 0
        self.close_call_count = 0
        self.active_publishes = 0
        self.is_started = False
        self.is_closed = False

    async def start(self) -> None:
        self.start_call_count += 1
        self.timeline.append("publisher_start")
        if self.start_error:
            raise self.start_error
        self.is_started = True

    async def close(self) -> None:
        self.close_call_count += 1
        self.timeline.append("publisher_close")
        if self.active_publishes > 0:
            self.timeline.append("ERROR_CLOSE_DURING_PUBLISH")
        if self.close_error:
            raise self.close_error
        self.is_closed = True

    async def publish(self, envelope: EventEnvelope) -> None:
        self.active_publishes += 1
        self.timeline.append("publisher_publish_start")
        try:
            if self.publish_delay > 0:
                await asyncio.sleep(self.publish_delay)
            if self.publish_error:
                raise self.publish_error
        finally:
            self.active_publishes -= 1
            self.timeline.append("publisher_publish_finish")


class FakeDispatcher:
    """Fake dispatcher simulating OutboxDispatcher.dispatch_once()."""

    def __init__(
        self,
        *,
        outcomes: list[DispatchOutcome | Exception] | None = None,
        default_outcome: DispatchOutcome = DispatchOutcome.IDLE,
        dispatch_delay: float = 0.0,
        timeline: list[str] | None = None,
    ) -> None:
        self.outcomes = list(outcomes) if outcomes is not None else []
        self.default_outcome = default_outcome
        self.dispatch_delay = dispatch_delay
        self.timeline = timeline if timeline is not None else []
        self.dispatch_count = 0
        self.active_dispatches = 0
        self.peak_dispatches = 0

    async def dispatch_once(self) -> DispatchResult:
        self.dispatch_count += 1
        self.active_dispatches += 1
        self.peak_dispatches = max(self.peak_dispatches, self.active_dispatches)
        self.timeline.append("dispatch_once_start")

        try:
            if self.dispatch_delay > 0:
                await asyncio.sleep(self.dispatch_delay)

            if self.outcomes:
                next_item = self.outcomes.pop(0)
                if isinstance(next_item, Exception):
                    raise next_item
                return DispatchResult(outcome=next_item, event_id=uuid4())

            return DispatchResult(outcome=self.default_outcome, event_id=uuid4())
        finally:
            self.active_dispatches -= 1
            self.timeline.append("dispatch_once_finish")


def _build_test_runner_policy(
    poll_interval: timedelta = timedelta(seconds=1),
    max_concurrency: int = 2,
    shutdown_grace_period: timedelta = timedelta(seconds=5),
) -> OutboxRunnerPolicy:
    return OutboxRunnerPolicy(
        poll_interval=poll_interval,
        max_concurrency=max_concurrency,
        shutdown_grace_period=shutdown_grace_period,
    )


def _build_test_dispatch_policy() -> OutboxDispatchPolicy:
    return OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=30),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=60),
        retry_multiplier=2.0,
    )


# -----------------------------------------------------------------------------
# OutboxRunnerPolicy Tests
# -----------------------------------------------------------------------------


def test_runner_policy_valid() -> None:
    """Verify valid runner policy construction without defaults."""
    policy = OutboxRunnerPolicy(
        poll_interval=timedelta(milliseconds=500),
        max_concurrency=4,
        shutdown_grace_period=timedelta(seconds=10),
    )
    assert policy.poll_interval == timedelta(milliseconds=500)
    assert policy.max_concurrency == 4
    assert policy.shutdown_grace_period == timedelta(seconds=10)


@pytest.mark.parametrize(
    "kwargs,expected_err",
    [
        (
            {
                "poll_interval": timedelta(seconds=0),
                "max_concurrency": 2,
                "shutdown_grace_period": timedelta(seconds=5),
            },
            "poll_interval must be strictly positive",
        ),
        (
            {
                "poll_interval": timedelta(seconds=-1),
                "max_concurrency": 2,
                "shutdown_grace_period": timedelta(seconds=5),
            },
            "poll_interval must be strictly positive",
        ),
        (
            {
                "poll_interval": timedelta(seconds=1),
                "max_concurrency": 0,
                "shutdown_grace_period": timedelta(seconds=5),
            },
            "max_concurrency must be >= 1",
        ),
        (
            {
                "poll_interval": timedelta(seconds=1),
                "max_concurrency": -3,
                "shutdown_grace_period": timedelta(seconds=5),
            },
            "max_concurrency must be >= 1",
        ),
        (
            {
                "poll_interval": timedelta(seconds=1),
                "max_concurrency": 2,
                "shutdown_grace_period": timedelta(seconds=0),
            },
            "shutdown_grace_period must be strictly positive",
        ),
        (
            {
                "poll_interval": timedelta(seconds=1),
                "max_concurrency": 2,
                "shutdown_grace_period": timedelta(seconds=-2),
            },
            "shutdown_grace_period must be strictly positive",
        ),
    ],
)
def test_runner_policy_rejections(kwargs: dict[str, Any], expected_err: str) -> None:
    """Verify invalid operational bounds are rejected strictly."""
    with pytest.raises(ValueError, match=expected_err):
        OutboxRunnerPolicy(**kwargs)


def test_runner_policy_immutable() -> None:
    """Verify OutboxRunnerPolicy is frozen and forbids extra fields."""
    policy = _build_test_runner_policy()
    with pytest.raises(ValidationError):
        policy.max_concurrency = 10

    with pytest.raises(ValidationError):
        OutboxRunnerPolicy(
            poll_interval=timedelta(seconds=1),
            max_concurrency=2,
            shutdown_grace_period=timedelta(seconds=5),
            extra_field="disallowed",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# OutboxDispatcherRunner Lifecycle & Execution Tests
# -----------------------------------------------------------------------------


def test_runner_construction_no_network_io() -> None:
    """Verify construction performs zero network I/O and starts zero workers."""
    publisher = FakeManagedPublisher()
    runner_policy = _build_test_runner_policy()
    dispatch_policy = _build_test_dispatch_policy()

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=runner_policy,
        dispatch_policy=dispatch_policy,
        uow_factory=lambda: None,
    )

    assert runner.state == RunnerState.NEW
    assert publisher.start_call_count == 0
    assert publisher.close_call_count == 0
    assert publisher.active_publishes == 0


@pytest.mark.asyncio
async def test_runner_pre_set_stop_event_clean_exit() -> None:
    """Verify pre-set stop_event cleanly exits without starting publisher or workers."""
    publisher = FakeManagedPublisher()
    dispatcher = FakeDispatcher()
    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED
    assert publisher.start_call_count == 0
    assert publisher.close_call_count == 0
    assert dispatcher.dispatch_count == 0


@pytest.mark.asyncio
async def test_runner_startup_order() -> None:
    """Verify publisher.start() executes before any worker dispatch call."""
    timeline: list[str] = []
    publisher = FakeManagedPublisher(timeline=timeline)
    dispatcher = FakeDispatcher(timeline=timeline)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(poll_interval=timedelta(milliseconds=50)),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_later() -> None:
        await asyncio.sleep(0.02)
        stop_event.set()

    asyncio.create_task(_stop_later())
    await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED
    assert "publisher_start" in timeline
    assert "dispatch_once_start" in timeline
    # publisher_start MUST be before any dispatch_once_start
    assert timeline.index("publisher_start") < timeline.index("dispatch_once_start")


@pytest.mark.asyncio
async def test_runner_startup_failure_propagates() -> None:
    """Verify publisher.start() failure creates zero workers and propagates error."""
    publisher = FakeManagedPublisher(start_error=ConnectionError("Failed to connect to broker"))
    dispatcher = FakeDispatcher()

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    with pytest.raises(ConnectionError, match="Failed to connect to broker"):
        await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED
    assert dispatcher.dispatch_count == 0


@pytest.mark.asyncio
async def test_runner_bounded_concurrency() -> None:
    """Verify exactly max_concurrency workers are executed concurrently."""
    publisher = FakeManagedPublisher()
    # Dispatcher has a 50ms delay, allowing concurrency to build up
    dispatcher = FakeDispatcher(dispatch_delay=0.05)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(
            max_concurrency=4,
            poll_interval=timedelta(milliseconds=10),
        ),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_after_concurrency() -> None:
        # Wait until peak concurrency is reached
        for _ in range(50):
            if dispatcher.peak_dispatches >= 4:
                break
            await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_after_concurrency())
    await runner.run(stop_event=stop_event)

    assert dispatcher.peak_dispatches == 4


@pytest.mark.asyncio
async def test_runner_idle_polling_cadence_and_stop_aware_wakeup() -> None:
    """Verify worker waits during IDLE and wakes promptly when stop_event is set."""
    publisher = FakeManagedPublisher()
    dispatcher = FakeDispatcher(default_outcome=DispatchOutcome.IDLE)

    # 10 second poll interval would hang test if not stop-aware
    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(poll_interval=timedelta(seconds=10)),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_quickly() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    asyncio.create_task(_stop_quickly())
    start_time = asyncio.get_running_loop().time()
    await runner.run(stop_event=stop_event)
    duration = asyncio.get_running_loop().time() - start_time

    # Wakes promptly upon stop_event; does not wait 10 seconds
    assert duration < 2.0
    assert runner.state == RunnerState.STOPPED


@pytest.mark.asyncio
async def test_runner_non_idle_continuation() -> None:
    """Verify worker continues without idle wait on non-IDLE outcomes."""
    publisher = FakeManagedPublisher()
    outcomes: list[DispatchOutcome | Exception] = [
        DispatchOutcome.PUBLISHED,
        DispatchOutcome.RETRY_SCHEDULED,
        DispatchOutcome.IDLE,
    ]
    dispatcher = FakeDispatcher(outcomes=outcomes)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(
            max_concurrency=1,
            poll_interval=timedelta(seconds=5),
        ),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_on_idle() -> None:
        while len(dispatcher.outcomes) > 0:
            await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_on_idle())
    await runner.run(stop_event=stop_event)

    assert dispatcher.dispatch_count >= 3


@pytest.mark.asyncio
async def test_runner_publisher_close_order() -> None:
    """Verify publisher.close() occurs strictly after workers have completed."""
    timeline: list[str] = []
    publisher = FakeManagedPublisher(timeline=timeline)
    dispatcher = FakeDispatcher(dispatch_delay=0.02, timeline=timeline)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(max_concurrency=2),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.03)
        stop_event.set()

    asyncio.create_task(_stop_soon())
    await runner.run(stop_event=stop_event)

    assert "ERROR_CLOSE_DURING_PUBLISH" not in timeline
    assert timeline[-1] == "publisher_close"


@pytest.mark.asyncio
async def test_runner_graceful_shutdown_within_grace() -> None:
    """Verify in-flight dispatch completes cleanly within grace period."""
    publisher = FakeManagedPublisher()
    dispatcher = FakeDispatcher(dispatch_delay=0.03)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(
            max_concurrency=1,
            shutdown_grace_period=timedelta(seconds=1),
        ),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_during_dispatch() -> None:
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_during_dispatch())
    await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED
    assert publisher.is_closed is True


@pytest.mark.asyncio
async def test_runner_grace_period_expiry_cancels_worker() -> None:
    """Verify worker exceeding shutdown_grace_period is force-cancelled."""
    publisher = FakeManagedPublisher()
    # 2 second delay exceeds 50ms grace period
    dispatcher = FakeDispatcher(dispatch_delay=2.0)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(
            max_concurrency=1,
            shutdown_grace_period=timedelta(milliseconds=50),
        ),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_immediately() -> None:
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_immediately())
    start_time = asyncio.get_running_loop().time()
    await runner.run(stop_event=stop_event)
    duration = asyncio.get_running_loop().time() - start_time

    # Terminates well before 2-second dispatch completes
    assert duration < 1.0
    assert runner.state == RunnerState.STOPPED
    assert publisher.is_closed is True


@pytest.mark.asyncio
async def test_runner_worker_failure_cancels_siblings_and_propagates() -> None:
    """Verify single worker failure stops siblings, closes publisher, and propagates error."""
    publisher = FakeManagedPublisher()
    outcomes: list[DispatchOutcome | Exception] = [
        RuntimeError("PrimaryWorkerCrash"),
    ]
    dispatcher = FakeDispatcher(outcomes=outcomes, default_outcome=DispatchOutcome.IDLE)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(max_concurrency=3),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    with pytest.raises(RuntimeError, match="PrimaryWorkerCrash"):
        await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED
    assert publisher.is_closed is True


@pytest.mark.asyncio
async def test_runner_primary_error_preservation_over_secondary_close_failure() -> None:
    """Verify primary worker failure is preserved when publisher.close() also fails."""
    publisher = FakeManagedPublisher(close_error=RuntimeError("SecondaryCloseCrash"))
    outcomes: list[DispatchOutcome | Exception] = [
        ValueError("PrimaryWorkerError"),
    ]
    dispatcher = FakeDispatcher(outcomes=outcomes)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(max_concurrency=2),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    with pytest.raises(ValueError, match="PrimaryWorkerError") as exc_info:
        await runner.run(stop_event=stop_event)

    # Note attached to primary exception
    assert hasattr(exc_info.value, "__notes__")
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("Secondary failure during publisher close" in n for n in notes)


@pytest.mark.asyncio
async def test_runner_close_failure_on_normal_stop_propagates() -> None:
    """Verify publisher.close() failure during normal stop propagates."""
    publisher = FakeManagedPublisher(close_error=RuntimeError("CloseFailureOnNormalStop"))
    dispatcher = FakeDispatcher()

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_soon())

    with pytest.raises(RuntimeError, match="CloseFailureOnNormalStop"):
        await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED


@pytest.mark.asyncio
async def test_runner_outer_cancellation_propagates() -> None:
    """Verify outer runner task cancellation cancels workers, closes publisher, and re-raises."""
    publisher = FakeManagedPublisher()
    dispatcher = FakeDispatcher(dispatch_delay=1.0)

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(max_concurrency=2),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()
    runner_task = asyncio.create_task(runner.run(stop_event=stop_event))

    await asyncio.sleep(0.02)
    runner_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner_task

    assert runner.state == RunnerState.STOPPED
    assert publisher.is_closed is True


@pytest.mark.asyncio
async def test_runner_one_shot_lifecycle_restrictions() -> None:
    """Verify runner rejects duplicate concurrent run() or restarting after stopped."""
    publisher = FakeManagedPublisher()
    dispatcher = FakeDispatcher()

    runner = OutboxDispatcherRunner(
        publisher=publisher,
        runner_policy=_build_test_runner_policy(),
        dispatcher=dispatcher,
    )

    stop_event = asyncio.Event()

    async def _concurrent_attempt() -> None:
        await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="OutboxDispatcherRunner is already running"):
            await runner.run(stop_event=stop_event)
        stop_event.set()

    asyncio.create_task(_concurrent_attempt())
    await runner.run(stop_event=stop_event)

    assert runner.state == RunnerState.STOPPED

    # Attempting to run again after stopping
    with pytest.raises(RuntimeError, match="OutboxDispatcherRunner cannot be restarted"):
        await runner.run(stop_event=asyncio.Event())


def test_architectural_boundaries_zero_forbidden_imports() -> None:
    """Verify runner module has zero imports of aio_pika, RabbitMQ, or SQLAlchemy."""
    import app.events.outbox.runner as runner_mod

    assert not hasattr(runner_mod, "aio_pika")
    assert not hasattr(runner_mod, "RabbitMqEventPublisher")
    assert not hasattr(runner_mod, "OutboxEventRecord")
    assert not hasattr(runner_mod, "OutboxDispatchRepository")

    source_code = Path(runner_mod.__file__).read_text(encoding="utf-8")
    assert "aio_pika" not in source_code
    assert "rabbitmq" not in source_code.lower()
    assert "sqlalchemy" not in source_code.lower()
    assert "asyncsession" not in source_code.lower()
    assert "outboxeventrecord" not in source_code.lower()
    assert "outboxdispatchrepository" not in source_code.lower()


def test_no_database_metadata_or_migration_changes() -> None:
    """Verify Base.metadata contains canonical foundation tables and canonical Phase 2A tables."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import app.db.models  # noqa: F401
    from app.db.base import Base

    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A Scheduling (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        # Phase 4B Calendar (1)
        "calendar_links",
    }
    tables = set(Base.metadata.tables.keys())
    assert phase1_tables <= tables, f"Missing Phase-1 tables: {phase1_tables - tables}"
    assert tables == canonical_phase4_tables, (
        f"Expected canonical Phase-4 tables {canonical_phase4_tables}, got {tables}"
    )
    assert len(tables) == 24

    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script_dir.get_current_head()
    assert head in ("0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012")
    assert head == "0012"

    version_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in version_dir.glob("*.py")]
    assert any("0005" in f for f in migration_files)
    assert any("0009" in f for f in migration_files)
    assert any("0010" in f for f in migration_files)
    assert any("0011" in f for f in migration_files)
    assert any("0012" in f for f in migration_files)
    assert not any("0013" in f for f in migration_files)
