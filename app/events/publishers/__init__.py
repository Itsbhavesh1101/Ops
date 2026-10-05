"""app.events.publishers package — Event Publisher Transport.

Part of Phase 1 Foundation Task 1G.3A.
Exports provider-neutral EventPublisher protocol, state model, exceptions,
and the concrete RabbitMqEventPublisher adapter.
"""

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
from app.events.publishers.rabbitmq import (
    ConnectionFactory,
    RabbitMqEventPublisher,
    default_connection_factory,
)

__all__ = [
    "ConnectionFactory",
    "EventPublishError",
    "EventPublisher",
    "PublisherConnectionError",
    "PublisherError",
    "PublisherNotStartedError",
    "PublisherState",
    "PublisherStateError",
    "RabbitMqEventPublisher",
    "RabbitMqPublisherSettings",
    "default_connection_factory",
]
