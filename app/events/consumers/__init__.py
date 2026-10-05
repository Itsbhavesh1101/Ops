"""app.events.consumers package — Inbound event consumers and transport adapters.

Part of Phase 1 Foundation Task 1G.4C1.
"""

from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.consumers.rabbitmq_message import (
    ConsumerProcessor,
    DuplicateKeyError,
    InvalidJsonConstantError,
    InvalidMessageReason,
    RabbitMqConsumerTransportError,
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
    RabbitMqMessageAckError,
    RabbitMqMessageHandlingOutcome,
    RabbitMqMessageHandlingResult,
    RabbitMqMessageRejectError,
)
from app.events.consumers.rabbitmq_runner import (
    RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST,
    ConsumerRunnerState,
    InboundMessageHandlerProtocol,
    RabbitMqConsumerConnectionError,
    RabbitMqConsumerLifecycleError,
    RabbitMqConsumerRunner,
    RabbitMqConsumerSubscriptionError,
    RabbitMqConsumerTopologyError,
    RunnerState,
)

__all__ = [
    "ConsumerProcessor",
    "ConsumerRunnerState",
    "DuplicateKeyError",
    "InboundMessageHandlerProtocol",
    "InvalidJsonConstantError",
    "InvalidMessageReason",
    "RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST",
    "RabbitMqConsumerConnectionError",
    "RabbitMqConsumerLifecycleError",
    "RabbitMqConsumerRunner",
    "RabbitMqConsumerRunnerPolicy",
    "RabbitMqConsumerSettings",
    "RabbitMqConsumerSubscriptionError",
    "RabbitMqConsumerTopologyError",
    "RabbitMqConsumerTransportError",
    "RabbitMqInboundMessageHandler",
    "RabbitMqInboundPolicy",
    "RabbitMqMessageAckError",
    "RabbitMqMessageHandlingOutcome",
    "RabbitMqMessageHandlingResult",
    "RabbitMqMessageRejectError",
    "RunnerState",
]
