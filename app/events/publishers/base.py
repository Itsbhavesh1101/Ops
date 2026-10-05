"""Provider-neutral event publisher protocol, state model, and exceptions.

Part of Phase 1 Foundation Task 1G.3A.
Defines the transport abstraction layer for publishing canonical EventEnvelope instances.
Exposes zero AMQP or RabbitMQ types, preserving provider neutrality and loose coupling.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.events.schemas import EventEnvelope


class PublisherState(StrEnum):
    """Lifecycle states for event publishers."""

    NEW = "NEW"
    STARTED = "STARTED"
    CLOSED = "CLOSED"


class PublisherError(Exception):
    """Base exception for all event publisher operations."""


class EventPublishError(PublisherError):
    """Raised when publishing an event envelope to the message transport fails."""


class PublisherNotStartedError(PublisherError):
    """Raised when publish() is called before start() or after close()."""


class PublisherConnectionError(PublisherError):
    """Raised when establishing connection, opening channel, or declaring exchange fails."""


class PublisherStateError(PublisherError):
    """Raised when an invalid publisher lifecycle state transition is requested."""


@runtime_checkable
class EventPublisher(Protocol):
    """Provider-neutral event publisher interface.

    Accepts a canonical EventEnvelope and publishes it to the underlying transport.
    Exposes zero broker-specific or ORM persistence types.
    """

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish a canonical EventEnvelope to the transport.

        Args:
            envelope: Canonical, validated EventEnvelope.

        Raises:
            PublisherNotStartedError: If publisher has not been started or is closed.
            EventPublishError: If transport publication fails.
        """
        ...
