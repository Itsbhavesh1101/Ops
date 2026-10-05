"""RabbitMQ consumer topology, bounded runner, and graceful lifecycle supervision.

Part of Phase 1 Foundation Task 1G.4C2.
Provides:
- RabbitMqConsumerRunner: long-running consumer runner supervising RabbitMQ topology,
  channel QoS, bounded concurrent message execution, and graceful teardown.
- Fail-fast supervisor: immediately halts consumption and propagates the first observed
  callback exception.
- Graceful shutdown: allows in-flight message handlers up to a configurable grace period,
  forcefully cancelling upon timeout.
- Architectural isolation: delegates single-message disposition entirely to the frozen
  RabbitMqInboundMessageHandler. Zero DB queries, zero event parsing, zero duplicate ACKs.
"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import aio_pika
import aio_pika.abc

from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.consumers.rabbitmq_message import RabbitMqMessageHandlingResult

# Canonical verification boundary constant
RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


class RabbitMqConsumerLifecycleError(Exception):
    """Base exception for RabbitMQ consumer lifecycle operations."""


class RabbitMqConsumerConnectionError(RabbitMqConsumerLifecycleError):
    """Raised when establishing or configuring RabbitMQ connection/channel fails."""


class RabbitMqConsumerTopologyError(RabbitMqConsumerLifecycleError):
    """Raised when declaring or binding exchanges/queues fails."""


class RabbitMqConsumerSubscriptionError(RabbitMqConsumerLifecycleError):
    """Raised when registering or cancelling consumer subscription fails."""


class ConsumerRunnerState(StrEnum):
    """Deterministic lifecycle states for RabbitMqConsumerRunner."""

    NEW = "NEW"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


# Alias for naming consistency across runners
RunnerState = ConsumerRunnerState


@runtime_checkable
class InboundMessageHandlerProtocol(Protocol):
    """Structural protocol representing the frozen C1 single-message handling boundary."""

    async def handle(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
    ) -> RabbitMqMessageHandlingResult:
        """Handle a single incoming RabbitMQ message."""
        ...


class RabbitMqConsumerRunner:
    """Supervises RabbitMQ consumer connection, topology, and bounded message processing."""

    def __init__(
        self,
        *,
        settings: RabbitMqConsumerSettings,
        runner_policy: RabbitMqConsumerRunnerPolicy,
        inbound_handler: InboundMessageHandlerProtocol,
        connection_factory: (
            Callable[..., Awaitable[aio_pika.abc.AbstractConnection]] | None
        ) = None,
    ) -> None:
        """Initialize RabbitMqConsumerRunner with settings, policy, and inbound handler.

        Performs zero network I/O during construction.

        Args:
            settings: Validated RabbitMQ consumer transport and topology settings.
            runner_policy: Validated operational runner policy (max concurrency and grace period).
            inbound_handler: Inbound message handler adhering to InboundMessageHandlerProtocol.
            connection_factory: Optional injectable factory for aio_pika.connect_robust.

        Raises:
            TypeError: If arguments do not conform to expected types/protocols.
            ValueError: If max_concurrency differs between settings and runner_policy.
        """
        if not isinstance(settings, RabbitMqConsumerSettings):
            raise TypeError(
                "settings must be a RabbitMqConsumerSettings instance, "
                f"got '{type(settings).__name__}'"
            )
        if not isinstance(runner_policy, RabbitMqConsumerRunnerPolicy):
            raise TypeError(
                f"runner_policy must be a RabbitMqConsumerRunnerPolicy instance, "
                f"got '{type(runner_policy).__name__}'"
            )
        if runner_policy.max_concurrency != settings.rabbitmq_consumer_max_concurrency:
            raise ValueError(
                f"max_concurrency mismatch: runner_policy has {runner_policy.max_concurrency}, "
                f"settings has {settings.rabbitmq_consumer_max_concurrency}"
            )
        if not isinstance(inbound_handler, InboundMessageHandlerProtocol):
            raise TypeError(
                f"inbound_handler must implement InboundMessageHandlerProtocol, "
                f"got '{type(inbound_handler).__name__}'"
            )

        self._settings = settings
        self._runner_policy = runner_policy
        self._inbound_handler = inbound_handler
        self._connection_factory = (
            connection_factory if connection_factory is not None else aio_pika.connect_robust
        )

        self._state = ConsumerRunnerState.NEW
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._concurrency_semaphore: asyncio.Semaphore | None = None
        self._primary_error: BaseException | None = None
        self._cleanup_error: BaseException | None = None
        self._stop_requested: asyncio.Event | None = None
        self._failure_event: asyncio.Event | None = None
        self._consumer_tag: str | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._connection: aio_pika.abc.AbstractConnection | None = None
        self._queue: aio_pika.abc.AbstractQueue | None = None
        self._current_concurrency: int = 0
        self._peak_concurrency: int = 0
        self._supervisor_task: asyncio.Task[Any] | None = None

    @property
    def state(self) -> ConsumerRunnerState:
        """Return the current lifecycle state of the runner."""
        return self._state

    @property
    def settings(self) -> RabbitMqConsumerSettings:
        """Return the transport and topology settings."""
        return self._settings

    @property
    def runner_policy(self) -> RabbitMqConsumerRunnerPolicy:
        """Return the operational runner policy."""
        return self._runner_policy

    @property
    def active_callbacks_count(self) -> int:
        """Return the number of currently active message processing callback tasks."""
        return len(
            [t for t in self._active_tasks if not t.done() and t is not self._supervisor_task]
        )

    @property
    def peak_concurrency(self) -> int:
        """Return the maximum observed concurrent executions of inbound_handler.handle()."""
        return self._peak_concurrency

    async def run(
        self,
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """Run the RabbitMQ consumer topology setup and bounded supervisor until stopped.

        Phases:
        1. Pre-flight checks:
           - One-shot lifecycle check (rejects duplicate or restarted execution).
           - Pre-set stop check (if stop_event is already set, exits immediately with zero network).
        2. Topology & Consumer Startup:
           - Connects robustly to RabbitMQ.
           - Opens channel and configures channel prefetch QoS.
           - Declares durable main topic exchange.
           - Declares durable dead-letter exchange (direct) and dead-letter queue.
           - Binds dead-letter queue to DLX with configured dead-letter routing key.
           - Declares durable consumer queue with DLX arguments:
             x-dead-letter-exchange and x-dead-letter-routing-key.
           - Binds consumer queue to main exchange for all configured topic binding keys.
           - Registers consumer callback with explicit acknowledgement mode (no_ack=False).
           - Transitions state to RUNNING.
        3. Supervisor Execution:
           - Concurrently awaits external stop_event or internal callback failure_event.
           - On callback failure: fail-fast shutdown, cancel subscription, record primary error.
           - On external stop: triggers graceful shutdown.
           - On outer cancellation: cancels subscription, cancels callbacks, cleans up,
             and re-raises CancelledError.
        4. Graceful Teardown:
           - Cancels consumer subscription to stop accepting new deliveries.
           - Rejects waiting callbacks that have not yet entered processing.
           - Allows active callbacks to finish up to shutdown_grace_period.
           - Force-cancels and awaits any remaining callbacks upon grace expiry.
           - Closes channel and connection in order.
           - Propagates primary error, close failure, or returns cleanly.

        Args:
            stop_event: External asyncio.Event signaling graceful shutdown.

        Raises:
            RuntimeError: If runner is already running or has already stopped.
            asyncio.CancelledError: If outer run() task is cancelled.
            RabbitMqConsumerLifecycleError: If connection, topology, or subscription fails.
            BaseException: If a message processing callback fails (original error propagated).
        """
        if self._state == ConsumerRunnerState.RUNNING:
            raise RuntimeError("RabbitMqConsumerRunner is already running")
        if self._state in (ConsumerRunnerState.STOPPING, ConsumerRunnerState.STOPPED):
            raise RuntimeError("RabbitMqConsumerRunner cannot be restarted after stopping")
        if self._state != ConsumerRunnerState.NEW:
            raise RuntimeError(f"Cannot run RabbitMqConsumerRunner in state '{self._state}'")

        if stop_event.is_set():
            self._state = ConsumerRunnerState.STOPPED
            return

        self._supervisor_task = asyncio.current_task()
        self._concurrency_semaphore = asyncio.Semaphore(self._runner_policy.max_concurrency)
        self._stop_requested = asyncio.Event()
        self._failure_event = asyncio.Event()
        self._primary_error = None
        self._cleanup_error = None
        self._active_tasks.clear()
        self._current_concurrency = 0
        self._peak_concurrency = 0

        self._state = ConsumerRunnerState.STARTING

        conn: aio_pika.abc.AbstractConnection | None = None
        chan: aio_pika.abc.AbstractChannel | None = None
        main_queue: aio_pika.abc.AbstractQueue | None = None
        consumer_tag: str | None = None

        try:
            # 1. Connect robustly
            try:
                conn = await self._connection_factory(
                    self._settings.rabbitmq_url.get_secret_value(),
                    timeout=self._settings.rabbitmq_connect_timeout_seconds,
                )
            except BaseException as exc:
                raise RabbitMqConsumerConnectionError(
                    "Failed to establish RabbitMQ connection"
                ) from exc

            # 2. Open channel
            try:
                chan = await conn.channel()
            except BaseException as exc:
                raise RabbitMqConsumerConnectionError("Failed to open RabbitMQ channel") from exc

            # 3. Configure QoS prefetch
            try:
                await chan.set_qos(prefetch_count=self._runner_policy.max_concurrency)
            except BaseException as exc:
                raise RabbitMqConsumerConnectionError(
                    "Failed to configure RabbitMQ channel QoS"
                ) from exc

            # 4. Declare main event exchange (topic, durable)
            try:
                main_exchange = await chan.declare_exchange(
                    name=self._settings.rabbitmq_exchange_name,
                    type=aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError(
                    "Failed to declare main event exchange"
                ) from exc

            # 5. Declare dead-letter exchange (direct, durable)
            try:
                dlx = await chan.declare_exchange(
                    name=self._settings.rabbitmq_dead_letter_exchange_name,
                    type=aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError(
                    "Failed to declare dead-letter exchange"
                ) from exc

            # 6. Declare dead-letter queue (durable, non-exclusive, non-auto-delete)
            try:
                dlq = await chan.declare_queue(
                    name=self._settings.rabbitmq_dead_letter_queue_name,
                    durable=True,
                    exclusive=False,
                    auto_delete=False,
                )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError("Failed to declare dead-letter queue") from exc

            # 7. Bind dead-letter queue to DLX with dead-letter routing key
            try:
                await dlq.bind(
                    exchange=dlx,
                    routing_key=self._settings.rabbitmq_dead_letter_routing_key,
                )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError(
                    "Failed to bind dead-letter queue to dead-letter exchange"
                ) from exc

            # 8. Declare main consumer queue with DLX arguments
            try:
                main_queue = await chan.declare_queue(
                    name=self._settings.rabbitmq_consumer_queue_name,
                    durable=True,
                    exclusive=False,
                    auto_delete=False,
                    arguments={
                        "x-dead-letter-exchange": (
                            self._settings.rabbitmq_dead_letter_exchange_name
                        ),
                        "x-dead-letter-routing-key": (
                            self._settings.rabbitmq_dead_letter_routing_key
                        ),
                    },
                )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError("Failed to declare consumer queue") from exc

            # 9. Bind main consumer queue for every configured topic binding key
            try:
                for binding_key in self._settings.rabbitmq_consumer_binding_keys:
                    await main_queue.bind(
                        exchange=main_exchange,
                        routing_key=binding_key,
                    )
            except BaseException as exc:
                raise RabbitMqConsumerTopologyError(
                    "Failed to bind consumer queue to event exchange"
                ) from exc

            # 10. Register consumer subscription (explicit acknowledgement mode)
            try:
                consumer_tag = await main_queue.consume(
                    self._on_message,
                    no_ack=False,
                )
            except BaseException as exc:
                raise RabbitMqConsumerSubscriptionError(
                    "Failed to register consumer on queue"
                ) from exc

            self._connection = conn
            self._channel = chan
            self._queue = main_queue
            self._consumer_tag = consumer_tag
            self._state = ConsumerRunnerState.RUNNING

        except BaseException as startup_err:
            self._state = ConsumerRunnerState.STOPPED
            if chan is not None:
                try:
                    await chan.close()
                except BaseException:
                    pass
            if conn is not None:
                try:
                    await conn.close()
                except BaseException:
                    pass
            raise startup_err

        # Supervisor monitoring loop
        stop_watcher = asyncio.create_task(stop_event.wait(), name="consumer-stop-watcher")
        fail_watcher = asyncio.create_task(
            self._failure_event.wait(), name="consumer-failure-watcher"
        )

        try:
            done, pending = await asyncio.wait(
                [stop_watcher, fail_watcher],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            stop_watcher.cancel()
            fail_watcher.cancel()
            await asyncio.gather(stop_watcher, fail_watcher, return_exceptions=True)
            await self._teardown(is_outer_cancellation=True)
            raise

        # Normal shutdown or fail-fast shutdown teardown
        await self._teardown(is_outer_cancellation=False)

        if self._primary_error is not None:
            raise self._primary_error

        if self._cleanup_error is not None:
            raise self._cleanup_error

    async def _on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Callback invoked by aio-pika upon message delivery."""
        current_task = asyncio.current_task()
        if current_task is not None and current_task is not self._supervisor_task:
            self._active_tasks.add(current_task)
        try:
            await self._dispatch_message(message)
        except asyncio.CancelledError:
            # Propagate cancellation so message remains unacknowledged (unsettled)
            raise
        except BaseException as exc:
            # First failure wins: capture the first primary failure
            if self._primary_error is None:
                self._primary_error = exc
            if self._failure_event is not None:
                self._failure_event.set()
            # Do not re-raise out of task: supervisor is notified and
            # propagates primary_error from run()
        finally:
            if current_task is not None and current_task is not self._supervisor_task:
                self._active_tasks.discard(current_task)

    async def _dispatch_message(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
    ) -> None:
        """Process an incoming message within concurrency limits."""
        # Drop newly delivered messages immediately if stop was requested before semaphore
        if self._state != ConsumerRunnerState.RUNNING or (
            self._stop_requested is not None and self._stop_requested.is_set()
        ):
            return

        if self._concurrency_semaphore is None:
            return

        async with self._concurrency_semaphore:
            # Check again after acquiring semaphore (Section 36 & 73)
            if self._state != ConsumerRunnerState.RUNNING or (
                self._stop_requested is not None and self._stop_requested.is_set()
            ):
                return

            self._current_concurrency += 1
            if self._current_concurrency > self._peak_concurrency:
                self._peak_concurrency = self._current_concurrency

            try:
                # Delegate entirely to the frozen C1 single-message handler
                await self._inbound_handler.handle(message)
            finally:
                self._current_concurrency -= 1

    async def _teardown(self, *, is_outer_cancellation: bool = False) -> None:
        """Execute ordered graceful shutdown and resource cleanup."""
        self._state = ConsumerRunnerState.STOPPING
        if self._stop_requested is not None:
            self._stop_requested.set()

        cleanup_error: BaseException | None = None

        # 1. Cancel consumer subscription so broker ceases delivering messages
        if self._consumer_tag is not None and self._queue is not None:
            try:
                await self._queue.cancel(self._consumer_tag)
            except BaseException as exc:
                if self._primary_error is not None:
                    self._primary_error.add_note(
                        f"Secondary failure during consumer cancel: {type(exc).__name__}"
                    )
                elif cleanup_error is None and not isinstance(exc, asyncio.CancelledError):
                    cleanup_error = exc
            finally:
                self._consumer_tag = None

        # 2. Handle active message callbacks
        if is_outer_cancellation:
            active = [
                t
                for t in list(self._active_tasks)
                if not t.done() and t is not self._supervisor_task
            ]
            for t in active:
                t.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
        else:
            grace_seconds = self._runner_policy.shutdown_grace_period.total_seconds()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + grace_seconds

            while True:
                active = [
                    t
                    for t in list(self._active_tasks)
                    if not t.done() and t is not self._supervisor_task
                ]
                if not active:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # Grace period expired: force cancel remaining active callbacks
                    for t in active:
                        t.cancel()
                    await asyncio.gather(*active, return_exceptions=True)
                    break
                await asyncio.wait(
                    active,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )

        # 3. Close channel
        if self._channel is not None:
            try:
                await self._channel.close()
            except BaseException as exc:
                if self._primary_error is not None:
                    self._primary_error.add_note(
                        f"Secondary failure during channel close: {type(exc).__name__}"
                    )
                elif cleanup_error is None and not isinstance(exc, asyncio.CancelledError):
                    cleanup_error = exc
            finally:
                self._channel = None

        # 4. Close connection
        if self._connection is not None:
            try:
                await self._connection.close()
            except BaseException as exc:
                if self._primary_error is not None:
                    self._primary_error.add_note(
                        f"Secondary failure during connection close: {type(exc).__name__}"
                    )
                elif cleanup_error is None and not isinstance(exc, asyncio.CancelledError):
                    cleanup_error = exc
            finally:
                self._connection = None

        self._state = ConsumerRunnerState.STOPPED
        self._cleanup_error = cleanup_error
