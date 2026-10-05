"""Bounded outbox dispatcher runner and graceful lifecycle supervision.

Part of Phase 1 Foundation Task 1G.3B3.
Provides:
- Bounded concurrent worker execution of frozen OutboxDispatcher.dispatch_once().
- Publisher lifecycle management (start once before workers; close once after workers terminate).
- Stop-aware idle polling cadence (wakes promptly on external stop signal).
- Graceful shutdown within a configurable grace period, with forced cancellation upon timeout.
- Fail-fast worker supervision (propagates primary worker exceptions, cancels siblings).
- Primary exception preservation if publisher close subsequently fails.
- Provider-neutral publisher lifecycle protocol (ManagedEventPublisher).
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from app.db.uow import UnitOfWork
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    DispatchResult,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.publishers.base import EventPublisher


@runtime_checkable
class ManagedEventPublisher(EventPublisher, Protocol):
    """Provider-neutral event publisher protocol with lifecycle management."""

    async def start(self) -> None:
        """Start the publisher and initialize underlying transport resources."""
        ...

    async def close(self) -> None:
        """Close the publisher and release underlying transport resources."""
        ...


@runtime_checkable
class SingleEventDispatcher(Protocol):
    """Provider-neutral single-event dispatch interface."""

    async def dispatch_once(self) -> DispatchResult:
        """Dispatch a single ready outbox event."""
        ...


class RunnerState(StrEnum):
    """Lifecycle states for the outbox dispatcher runner."""

    NEW = "NEW"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class OutboxRunnerPolicy(BaseModel):
    """Immutable operational configuration for the outbox dispatcher runner.

    Defines polling intervals, worker concurrency bounds, and graceful shutdown timeouts.
    Contains strictly explicit configuration without hidden production defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_interval: timedelta
    max_concurrency: int
    shutdown_grace_period: timedelta

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Validate operational bounds across runner policy settings."""
        if self.poll_interval <= timedelta(0):
            raise ValueError(f"poll_interval must be strictly positive, got {self.poll_interval}")
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {self.max_concurrency}")
        if self.shutdown_grace_period <= timedelta(0):
            raise ValueError(
                f"shutdown_grace_period must be strictly positive, got {self.shutdown_grace_period}"
            )
        return self


class OutboxDispatcherRunner:
    """Supervises bounded concurrent outbox dispatch workers across a graceful lifecycle."""

    def __init__(
        self,
        *,
        publisher: ManagedEventPublisher,
        runner_policy: OutboxRunnerPolicy,
        dispatch_policy: OutboxDispatchPolicy | None = None,
        uow_factory: (Callable[[], UnitOfWork] | Callable[[], Any] | None) = None,
        dispatcher: SingleEventDispatcher | None = None,
    ) -> None:
        """Initialize OutboxDispatcherRunner with operational dependencies.

        Args:
            publisher: Provider-neutral ManagedEventPublisher instance.
            runner_policy: Validated operational runner policy.
            dispatch_policy: Optional technical dispatch policy for internal OutboxDispatcher.
            uow_factory: Optional UnitOfWork factory for internal OutboxDispatcher.
            dispatcher: Optional pre-constructed SingleEventDispatcher instance.

        Raises:
            TypeError: If arguments do not conform to expected protocols/types.
            ValueError: If neither a dispatcher nor required dependencies are provided.
        """
        if not isinstance(publisher, ManagedEventPublisher):
            raise TypeError(
                f"publisher must implement ManagedEventPublisher protocol, "
                f"got '{type(publisher).__name__}'"
            )
        if not isinstance(runner_policy, OutboxRunnerPolicy):
            raise TypeError(
                f"runner_policy must be an OutboxRunnerPolicy, got '{type(runner_policy).__name__}'"
            )

        if dispatcher is not None:
            if not isinstance(dispatcher, SingleEventDispatcher):
                raise TypeError(
                    f"dispatcher must implement SingleEventDispatcher protocol, "
                    f"got '{type(dispatcher).__name__}'"
                )
            self._dispatcher = dispatcher
            self._dispatch_policy = dispatch_policy or getattr(dispatcher, "_policy", None)
        else:
            if uow_factory is None or dispatch_policy is None:
                raise ValueError(
                    "uow_factory and dispatch_policy are required when dispatcher is not provided"
                )
            if not isinstance(dispatch_policy, OutboxDispatchPolicy):
                raise TypeError(
                    f"dispatch_policy must be an OutboxDispatchPolicy, "
                    f"got '{type(dispatch_policy).__name__}'"
                )
            self._dispatcher = OutboxDispatcher(
                uow_factory=uow_factory,
                publisher=publisher,
                policy=dispatch_policy,
            )
            self._dispatch_policy = dispatch_policy

        self._publisher = publisher
        self._runner_policy = runner_policy
        self._state = RunnerState.NEW

    @property
    def state(self) -> RunnerState:
        """Return the current lifecycle state of the runner."""
        return self._state

    @property
    def runner_policy(self) -> OutboxRunnerPolicy:
        """Return the operational runner policy."""
        return self._runner_policy

    @property
    def dispatch_policy(self) -> OutboxDispatchPolicy | None:
        """Return the technical dispatch policy."""
        return self._dispatch_policy

    async def run(
        self,
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """Run the bounded outbox dispatcher worker supervisor until stopped.

        Phases:
        1. Check pre-conditions:
           - One-shot lifecycle: rejects running if already running, stopping, or stopped.
           - Pre-set stop check: if stop_event is already set, transitions to STOPPED immediately
             without starting the publisher or creating workers.
        2. Publisher Startup:
           - Calls await publisher.start().
           - If start fails: propagates immediately, creates zero workers, transitions to STOPPED.
           - Transitions state to RUNNING.
        3. Worker Execution:
           - Spawns exactly runner_policy.max_concurrency worker tasks.
           - Workers repeatedly invoke frozen OutboxDispatcher.dispatch_once().
           - Non-IDLE outcomes continue immediately without sleep.
           - IDLE outcomes wait for poll_interval or prompt wakeup if stop_event is set.
        4. Supervision & Fail-Fast:
           - If any worker raises an exception: cancels sibling workers, awaits cleanup,
             closes publisher, and propagates the original primary exception.
        5. Graceful Teardown:
           - Upon observing stop_event: transitions to STOPPING.
           - Awaits active workers up to shutdown_grace_period.
           - If grace period expires, force-cancels remaining workers and awaits cancellation.
           - Closes publisher after all worker tasks terminate.
           - Transitions to STOPPED.

        Args:
            stop_event: External asyncio.Event signaling graceful shutdown request.

        Raises:
            RuntimeError: If called concurrently or after runner has already stopped.
            asyncio.CancelledError: If outer run() task is cancelled.
            BaseException: If a worker or publisher operation fails.
        """
        if self._state == RunnerState.RUNNING:
            raise RuntimeError("OutboxDispatcherRunner is already running")
        if self._state in (RunnerState.STOPPING, RunnerState.STOPPED):
            raise RuntimeError("OutboxDispatcherRunner cannot be restarted after stopping")
        if self._state != RunnerState.NEW:
            raise RuntimeError(f"Cannot run OutboxDispatcherRunner in state '{self._state}'")

        if stop_event.is_set():
            self._state = RunnerState.STOPPED
            return

        try:
            await self._publisher.start()
        except BaseException:
            self._state = RunnerState.STOPPED
            raise

        self._state = RunnerState.RUNNING
        internal_stop_event = asyncio.Event()

        async def _stop_listener() -> None:
            await stop_event.wait()
            internal_stop_event.set()

        stop_watcher = asyncio.create_task(_stop_listener(), name="outbox-stop-watcher")
        worker_tasks = [
            asyncio.create_task(
                self._worker_loop(i, internal_stop_event),
                name=f"outbox-worker-{i}",
            )
            for i in range(self._runner_policy.max_concurrency)
        ]

        primary_error: BaseException | None = None
        try:
            while True:
                done, _ = await asyncio.wait(
                    worker_tasks + [stop_watcher],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    if task is not stop_watcher:
                        exc = task.exception()
                        if exc is not None and not isinstance(exc, asyncio.CancelledError):
                            primary_error = exc
                            break

                if primary_error is not None:
                    break

                if stop_watcher in done or stop_event.is_set():
                    break

                active_workers = [t for t in worker_tasks if not t.done()]
                if not active_workers:
                    break

        except asyncio.CancelledError:
            self._state = RunnerState.STOPPING
            internal_stop_event.set()
            stop_watcher.cancel()
            for t in worker_tasks:
                t.cancel()
            await asyncio.gather(*worker_tasks, stop_watcher, return_exceptions=True)
            self._state = RunnerState.STOPPED
            try:
                await self._publisher.close()
            except BaseException:
                pass
            raise

        # Initiate teardown
        self._state = RunnerState.STOPPING
        internal_stop_event.set()
        stop_watcher.cancel()

        active_workers = [t for t in worker_tasks if not t.done()]
        if primary_error is not None:
            # Worker failure: immediately cancel siblings
            for t in active_workers:
                t.cancel()
            if active_workers:
                await asyncio.gather(*active_workers, return_exceptions=True)
        else:
            # Normal shutdown: wait within grace period
            if active_workers:
                done_grace, pending_grace = await asyncio.wait(
                    active_workers,
                    timeout=self._runner_policy.shutdown_grace_period.total_seconds(),
                )
                for t in done_grace:
                    exc = t.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        if primary_error is None:
                            primary_error = exc

                if pending_grace:
                    for t in pending_grace:
                        t.cancel()
                    await asyncio.gather(*pending_grace, return_exceptions=True)

        # Close publisher after all workers have terminated
        try:
            await self._publisher.close()
        except BaseException as close_exc:
            if primary_error is not None:
                primary_error.add_note(
                    f"Secondary failure during publisher close: {type(close_exc).__name__}"
                )
            else:
                self._state = RunnerState.STOPPED
                raise

        self._state = RunnerState.STOPPED

        if primary_error is not None:
            raise primary_error

    async def _worker_loop(
        self,
        worker_id: int,
        internal_stop_event: asyncio.Event,
    ) -> None:
        """Run bounded dispatch loop for a single worker."""
        while not internal_stop_event.is_set():
            result = await self._dispatcher.dispatch_once()

            if result.outcome == DispatchOutcome.IDLE:
                try:
                    await asyncio.wait_for(
                        internal_stop_event.wait(),
                        timeout=self._runner_policy.poll_interval.total_seconds(),
                    )
                except TimeoutError:
                    pass
            else:
                # Non-IDLE outcome: immediately continue without sleep
                continue
