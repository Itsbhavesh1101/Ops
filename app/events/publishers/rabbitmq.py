"""RabbitMQ concrete event publisher adapter using aio-pika.

Part of Phase 1 Foundation Task 1G.3A.
Implements the provider-neutral EventPublisher protocol using durable topic exchanges,
publisher confirms, mandatory routing verification, and persistent message delivery.
Zero persistence/database dependencies.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
import aio_pika.abc

from app.core.telemetry.outbox import get_outbox_telemetry
from app.events.publishers.base import (
    EventPublisher,
    EventPublishError,
    PublisherConnectionError,
    PublisherError,
    PublisherNotStartedError,
    PublisherState,
    PublisherStateError,
)
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.schemas import EventEnvelope
from app.events.serialization import serialize_event_envelope

# Narrow connection factory seam for testability without live broker
ConnectionFactory = Callable[[str, float], Awaitable[aio_pika.abc.AbstractConnection]]


async def default_connection_factory(
    url: str,
    timeout: float,
) -> aio_pika.abc.AbstractConnection:
    """Production connection factory using aio-pika connect_robust."""
    return await aio_pika.connect_robust(url=url, timeout=timeout)


class RabbitMqEventPublisher(EventPublisher):
    """Concrete RabbitMQ event publisher using aio-pika.

    Invariants:
    - Transport only: zero database access or outbox polling.
    - Zero import-time or instantiation network calls. Connection requires explicit start().
    - Publisher confirms enabled: publish returns only after broker confirmation.
    - Mandatory routing: unroutable messages raise a safe transport error.
    - Durable topic exchange: exchange declared durable with type topic. Zero queue declarations.
    - Message framing: body serialized via canonical serialize_event_envelope; message_id
      strictly matches EventEnvelope.event_id; persistent delivery mode.
    """

    def __init__(
        self,
        settings: RabbitMqPublisherSettings,
        *,
        connection_factory: ConnectionFactory | None = None,
        mandatory: bool = True,
    ) -> None:
        """Initialize publisher without performing network I/O.

        Args:
            settings: Required RabbitMQ publisher configuration.
            connection_factory: Async callable to create a robust connection. Seam for testing.
            mandatory: Whether publishing requires mandatory queue routing. Defaults to True.
        """
        self._settings = settings
        self._connection_factory = connection_factory or default_connection_factory
        self._mandatory = mandatory
        self._state = PublisherState.NEW

        self._connection: aio_pika.abc.AbstractConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    @property
    def state(self) -> PublisherState:
        """Return the current lifecycle state of the publisher."""
        return self._state

    @property
    def exchange_name(self) -> str:
        """Return the configured exchange name."""
        return self._settings.rabbitmq_exchange_name

    async def start(self) -> None:
        """Explicitly connect to RabbitMQ, create confirm channel, and declare durable exchange.

        Raises:
            PublisherStateError: If publisher has already been closed.
            PublisherConnectionError: If connection, channel, or exchange declaration fails.
        """
        if self._state == PublisherState.STARTED:
            return

        if self._state == PublisherState.CLOSED:
            raise PublisherStateError("Cannot start publisher: publisher is closed.")

        connection: aio_pika.abc.AbstractConnection | None = None
        channel: aio_pika.abc.AbstractChannel | None = None

        try:
            conn = await self._connection_factory(
                self._settings.rabbitmq_url.get_secret_value(),
                self._settings.rabbitmq_connect_timeout_seconds,
            )
            connection = conn
            # Enable publisher confirms and surface unroutable mandatory messages
            chan = await conn.channel(
                publisher_confirms=True,
                on_return_raises=True,
            )
            channel = chan
            # Declare durable topic exchange only; zero queue declarations
            exchange = await chan.declare_exchange(
                name=self._settings.rabbitmq_exchange_name,
                type=aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
        except Exception as exc:
            # Clean up partially acquired resources safely
            if channel is not None:
                try:
                    await channel.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    await connection.close()
                except Exception:
                    pass

            self._connection = None
            self._channel = None
            self._exchange = None
            self._state = PublisherState.NEW

            safe_message = "Failed to establish RabbitMQ publisher connection or declare exchange."
            raise PublisherConnectionError(safe_message) from exc

        self._connection = connection
        self._channel = channel
        self._exchange = exchange
        self._state = PublisherState.STARTED

    async def close(self) -> None:
        """Close RabbitMQ channel and connection resources safely and idempotently."""
        if self._state == PublisherState.CLOSED:
            return

        self._state = PublisherState.CLOSED

        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception:
                pass
            self._channel = None

        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None

        self._exchange = None

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish a canonical EventEnvelope to the configured RabbitMQ topic exchange.

        Args:
            envelope: Canonical EventEnvelope to publish.

        Raises:
            PublisherNotStartedError: If publisher is not in STARTED state.
            EventPublishError: If broker publication, confirmation, or routing fails.
        """
        if self._state == PublisherState.NEW:
            raise PublisherNotStartedError(
                "Publisher has not been started. Call start() before publishing."
            )
        if self._state == PublisherState.CLOSED:
            raise PublisherNotStartedError("Publisher is closed and cannot publish.")
        if self._exchange is None:
            raise PublisherNotStartedError("Publisher exchange is not available.")

        telemetry = get_outbox_telemetry()
        with telemetry.span_publish(
            event_type=envelope.event_type,
            exchange_name=self.exchange_name,
            correlation_id=envelope.correlation_id,
        ):
            try:
                body_str = serialize_event_envelope(envelope)
                body_bytes = body_str.encode("utf-8")

                message = aio_pika.Message(
                    body=body_bytes,
                    content_type="application/json",
                    content_encoding="utf-8",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=str(envelope.event_id),
                    type=envelope.event_type,
                    correlation_id=envelope.correlation_id,
                    timestamp=envelope.occurred_at,
                )

                await self._exchange.publish(
                    message=message,
                    routing_key=envelope.event_type,
                    mandatory=self._mandatory,
                )
            except PublisherError:
                raise
            except Exception as exc:
                safe_error_msg = (
                    f"Failed to publish event '{envelope.event_type}' to RabbitMQ exchange."
                )
                raise EventPublishError(safe_error_msg) from exc

    async def __aenter__(self) -> "RabbitMqEventPublisher":
        """Async context manager entry: starts publisher."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit: closes publisher."""
        await self.close()
