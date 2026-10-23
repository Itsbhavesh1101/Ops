"""Background worker and consumer runner for outbound notification engagement.

Part of Stage 5 E-8 Notification Integration.
Encapsulates:
- Subscription to canonical schedule events ('*.schedule.published', '*.schedule.changed').
- Transactional ConsumerEventProcessor with inbox deduplication.
- RabbitMQ inbound message decoding and ack/nack semantics.
- Outbound dispatch to configured E-8 dispatcher (AMQP or HTTP).
- Clean lifecycle management: start_background_tasks(stop_event) returning asyncio.Task instances.
"""

import asyncio
import os
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import structlog
from pydantic import SecretStr

from app.db.engine import DatabaseManager
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.consumers.rabbitmq_message import (
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
)
from app.events.consumers.rabbitmq_runner import RabbitMqConsumerRunner
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import (
    NotificationDispatcherProtocol,
    get_notification_dispatcher,
)
from app.integrations.engagement.handlers import (
    create_notification_consumer_processor,
)

logger = structlog.get_logger(__name__)


class NotificationWorker:
    """Dedicated background worker for transactional E-8 notification consumption."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        settings: NotificationSettings | None = None,
        consumer_settings: RabbitMqConsumerSettings | None = None,
        dispatcher: NotificationDispatcherProtocol | None = None,
        consumer_runner: Any = None,
        consumer_runner_factory: Callable[[], Any] | None = None,
        retry_backoff_seconds: float = 5.0,
    ) -> None:
        self.db_manager = db_manager
        self.settings = settings or NotificationSettings()
        self.consumer_settings = consumer_settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self._consumer_runner = consumer_runner
        self._consumer_runner_factory = consumer_runner_factory
        self.retry_backoff_seconds = max(0.1, retry_backoff_seconds)

    def _build_consumer_runner(self) -> RabbitMqConsumerRunner:
        if self._consumer_runner_factory is not None:
            return self._consumer_runner_factory()

        if self._consumer_runner is not None:
            runner = self._consumer_runner
            # If consumer_runner was injected directly, clear it after first use so it isn't
            # reused after stopping if a factory was not provided
            return runner

        if self.consumer_settings is not None:
            c_settings = self.consumer_settings
        else:
            # RabbitMQ domain event broker URL (E7_RABBITMQ_URL or RABBITMQ_URL)
            broker_url = (os.getenv("E7_RABBITMQ_URL") or os.getenv("RABBITMQ_URL") or "").strip()
            if not broker_url:
                raise ValueError(
                    "E7_RABBITMQ_URL or RABBITMQ_URL is required for notification consumer runner."
                )

            c_settings = RabbitMqConsumerSettings(
                rabbitmq_url=SecretStr(broker_url),
                rabbitmq_exchange_name=os.getenv(
                    "E7_DOMAIN_EVENTS_EXCHANGE",
                    os.getenv("E7_RABBITMQ_EXCHANGE_NAME", "e7.events"),
                ),
                rabbitmq_consumer_queue_name=os.getenv(
                    "E7_NOTIFICATION_CONSUMER_QUEUE", "e7.notifications.queue"
                ),
                rabbitmq_consumer_binding_keys=(
                    "*.schedule.published",
                    "*.schedule.changed",
                ),
                rabbitmq_dead_letter_exchange_name=os.getenv(
                    "E7_NOTIFICATION_DLX", "e7.notifications.dlx"
                ),
                rabbitmq_dead_letter_queue_name=os.getenv(
                    "E7_NOTIFICATION_DLQ", "e7.notifications.dlq"
                ),
                rabbitmq_dead_letter_routing_key="notification.dlq",
                rabbitmq_consumer_max_concurrency=10,
                rabbitmq_connect_timeout_seconds=self.settings.timeout_seconds,
            )

        runner_policy = RabbitMqConsumerRunnerPolicy.from_settings(
            c_settings,
            shutdown_grace_period=timedelta(seconds=10),
        )

        def _uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(self.db_manager.session_factory)

        processor = create_notification_consumer_processor(
            uow_factory=_uow_factory,
            dispatcher=self.dispatcher,
            settings=self.settings,
        )
        inbound_policy = RabbitMqInboundPolicy(max_message_bytes=1_048_576)
        inbound_handler = RabbitMqInboundMessageHandler(
            processor=processor,
            policy=inbound_policy,
        )
        return RabbitMqConsumerRunner(
            settings=c_settings,
            runner_policy=runner_policy,
            inbound_handler=inbound_handler,
        )

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run consumer runner loop with bounded backoff until stop_event is set."""
        while not stop_event.is_set():
            try:
                runner = self._build_consumer_runner()
                logger.info("notification_consumer_starting")
                await runner.run(stop_event=stop_event)
                logger.info("notification_consumer_stopped_cleanly")
                break
            except asyncio.CancelledError:
                logger.info("notification_consumer_cancelled")
                raise
            except Exception as exc:
                if stop_event.is_set():
                    break
                logger.error(
                    "notification_consumer_failed_restarting",
                    error=str(exc),
                    backoff_seconds=self.retry_backoff_seconds,
                )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.retry_backoff_seconds,
                    )
                    break
                except TimeoutError:
                    pass

    def start_background_tasks(self, stop_event: asyncio.Event) -> list[asyncio.Task[None]]:
        """Start notification consumer loop as an asyncio background task."""
        task = asyncio.create_task(
            self.run(stop_event),
            name="notification_consumer_runner_loop",
        )
        return [task]


__all__ = ["NotificationWorker"]

