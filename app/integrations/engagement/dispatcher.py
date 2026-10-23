"""Production notification dispatchers for E-8 engagement boundary.

Part of Phase 7D E-8 Production Notification Boundary.
Enforces:
- Provider-neutral NotificationDispatcherProtocol interface.
- HTTP REST gateway dispatcher with idempotency and retry handling.
- AMQP RabbitMQ topic dispatcher with transactional outbox alignment.
- Truthful reporting: network and remote failures raise or report failure truthfully.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import aio_pika
import aio_pika.abc
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.models import NotificationCommand


class NotificationDispatchError(Exception):
    """Raised when an outbound notification command cannot be delivered to E-8."""


@runtime_checkable
class NotificationDispatcherProtocol(Protocol):
    """Protocol defining notification dispatch to external E-8 boundary."""

    async def dispatch(
        self,
        command: NotificationCommand,
        session: AsyncSession | None = None,
    ) -> bool:
        """Dispatch a canonical NotificationCommand to E-8.

        Returns True if newly dispatched, or False if duplicate/already processed.
        """
        ...


ConnectionFactory = Callable[[str, float], Awaitable[aio_pika.abc.AbstractConnection]]


async def default_amqp_connection_factory(
    url: str,
    timeout: float,
) -> aio_pika.abc.AbstractConnection:
    """Production connection factory using aio-pika connect_robust."""
    return await aio_pika.connect_robust(url=url, timeout=timeout)


def _sanitize_error_message(msg: str) -> str:
    """Mask credentials (passwords, tokens) in error strings or connection URLs."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", msg)


async def _record_dispatch_audit(
    session: AsyncSession,
    command: NotificationCommand,
    outcome: AuditEventOutcome,
    transport_type: str,
    error_msg: str | None = None,
) -> None:
    """Emit audit event tracking notification delivery attempt.

    Logs only privacy-safe operational metadata; raw payloads and context are omitted.
    """
    audit_repo = AuditRepository(session)
    details: dict[str, Any] = {
        "command_id": command.command_id,
        "transport": transport_type,
        "trigger_kind": command.trigger.trigger_kind.value,
        "source_event": command.trigger.source_event_name,
        "recipient_count": len(command.recipient_references),
    }
    if error_msg:
        details["error"] = _sanitize_error_message(error_msg)

    await audit_repo.append(
        AuditEventCreate(
            tenant_id=command.organization_scope_reference,
            actor_principal_id="e7-notification-service",
            actor_principal_type="SERVICE",
            authentication_method=f"SERVICE_{transport_type.upper()}",
            event_type="notification.dispatched",
            resource_type="notification_command",
            resource_id=command.command_id,
            outcome=outcome,
            correlation_id=command.correlation_reference,
            details=details,
        )
    )


class HttpNotificationDispatcher(NotificationDispatcherProtocol):
    """HTTP client dispatcher communicating with E-8 notification gateway.

    Guarantees:
    - Zero in-memory deduplication set: outbox retries are never suppressed locally.
    - Idempotency is enforced by the remote E-8 boundary using command_id.
    - HTTP 409 is treated as duplicate acceptance ONLY when established by response contract.
    """

    def __init__(
        self,
        settings: NotificationSettings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or NotificationSettings()
        self._http_client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=self._settings.timeout_seconds)

    async def dispatch(
        self,
        command: NotificationCommand,
        session: AsyncSession | None = None,
    ) -> bool:
        """Deliver command via HTTP POST to configured E-8 gateway endpoint."""
        endpoint = self._settings.endpoint_url.strip()
        if not endpoint:
            raise NotificationDispatchError("E-8 endpoint_url is not configured.")

        client = self._get_client()
        headers = {
            "Content-Type": "application/json",
            "X-Command-ID": command.command_id,
            "X-Correlation-ID": command.correlation_reference,
        }
        api_key = self._settings.api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = command.model_dump(mode="json")

        try:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code in (200, 201, 202):
                if session is not None:
                    await _record_dispatch_audit(
                        session=session,
                        command=command,
                        outcome=AuditEventOutcome.SUCCESS,
                        transport_type="HTTP",
                    )
                return True

            if resp.status_code == 409:
                # Treat as idempotent duplicate acceptance only if established
                # by the structured E-8 response contract
                is_duplicate = False
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        err_val = str(
                            data.get("error") or data.get("code") or data.get("detail") or ""
                        ).lower()
                        if any(
                            term in err_val
                            for term in ("duplicate", "already_processed", "idempotent")
                        ):
                            is_duplicate = True
                except Exception:
                    # Fail closed: never guess duplicate acceptance from arbitrary body text
                    is_duplicate = False

                if is_duplicate:
                    if session is not None:
                        await _record_dispatch_audit(
                            session=session,
                            command=command,
                            outcome=AuditEventOutcome.SUCCESS,
                            transport_type="HTTP",
                        )
                    return False

                error_msg = f"E-8 HTTP 409 Conflict (non-duplicate): {resp.text}"
                if session is not None:
                    await _record_dispatch_audit(
                        session=session,
                        command=command,
                        outcome=AuditEventOutcome.FAILURE,
                        transport_type="HTTP",
                        error_msg=error_msg,
                    )
                raise NotificationDispatchError(error_msg)

            error_msg = f"E-8 HTTP error {resp.status_code}: {resp.text}"
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.FAILURE,
                    transport_type="HTTP",
                    error_msg=error_msg,
                )
            raise NotificationDispatchError(error_msg)

        except httpx.TimeoutException as exc:
            error_msg = f"E-8 request timed out: {exc}"
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.FAILURE,
                    transport_type="HTTP",
                    error_msg=error_msg,
                )
            raise NotificationDispatchError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"E-8 connection error: {exc}"
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.FAILURE,
                    transport_type="HTTP",
                    error_msg=error_msg,
                )
            raise NotificationDispatchError(error_msg) from exc


class AmqpNotificationDispatcher(NotificationDispatcherProtocol):
    """RabbitMQ AMQP topic exchange notification dispatcher.

    Dispatches canonical NotificationCommand instances to the e8.notifications topic
    exchange with routing key: {tenant_id}.notification.{trigger_kind} in accordance
    with ADR-012.

    Guarantees:
    - Zero in-memory deduplication set: transactional outbox at-least-once delivery is preserved.
    - Publisher confirms ensure broker queue acceptance before returning True.
    - Mandatory routing raises UnroutableError when message cannot be routed to any queue.
    - Connection/channel failures reset broker resources cleanly for retry safety.
    - Credentials in URLs are sanitized in error logs and audit records.
    """

    def __init__(
        self,
        settings: NotificationSettings | None = None,
        *,
        exchange: aio_pika.abc.AbstractExchange | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._settings = settings or NotificationSettings()
        self._exchange = exchange
        self._connection_factory = connection_factory or default_amqp_connection_factory
        self._connection: aio_pika.abc.AbstractConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    def compute_routing_key(self, command: NotificationCommand) -> str:
        """Derive canonical routing key: {tenant_id}.notification.{trigger_kind}."""
        tenant_id = command.organization_scope_reference
        if command.trigger.source_event_name:
            trigger_kind = command.trigger.source_event_name.replace(".", "_")
        elif command.trigger.business_condition:
            trigger_kind = command.trigger.business_condition.lower()
        else:
            trigger_kind = command.trigger.trigger_kind.value.lower()
        return f"{tenant_id}.notification.{trigger_kind}"

    async def start(self) -> None:
        """Initialize connection, confirm channel, and declare durable topic exchange."""
        if self._exchange is not None:
            return

        url = self._settings.rabbitmq_url.get_secret_value().strip()
        if not url:
            raise NotificationDispatchError(
                "RabbitMQ rabbitmq_url is not configured. Dedicated E-8 broker URL must be "
                "explicitly set via E7_NOTIFICATION_RABBITMQ_URL or constructor."
            )

        try:
            conn = await self._connection_factory(
                url,
                self._settings.timeout_seconds,
            )
            self._connection = conn
            chan = await conn.channel(publisher_confirms=True, on_return_raises=True)
            self._channel = chan
            self._exchange = await chan.declare_exchange(
                name=self._settings.exchange,
                type=aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
        except Exception as exc:
            await self.close()
            sanitized_err = _sanitize_error_message(str(exc))
            error_msg = f"Failed to connect to RabbitMQ notification exchange: {sanitized_err}"
            raise NotificationDispatchError(error_msg) from exc

    async def close(self) -> None:
        """Release broker connection and channel resources safely."""
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

    async def dispatch(
        self,
        command: NotificationCommand,
        session: AsyncSession | None = None,
    ) -> bool:
        """Publish command to RabbitMQ topic exchange."""
        if self._exchange is None:
            await self.start()

        if self._exchange is None:
            error_msg = "RabbitMQ notification exchange is not initialized."
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.FAILURE,
                    transport_type="AMQP",
                    error_msg=error_msg,
                )
            raise NotificationDispatchError(error_msg)

        routing_key = self.compute_routing_key(command)
        payload_bytes = command.model_dump_json().encode("utf-8")

        message = aio_pika.Message(
            body=payload_bytes,
            content_type="application/json",
            content_encoding="utf-8",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=command.command_id,
            correlation_id=command.correlation_reference,
            type=f"notification.{routing_key.split('.')[-1]}",
            headers={
                "x-command-id": command.command_id,
                "x-correlation-id": command.correlation_reference,
                "x-tenant-id": command.organization_scope_reference,
                "x-contract-version": command.contract_version,
            },
        )

        try:
            await self._exchange.publish(
                message=message,
                routing_key=routing_key,
                mandatory=self._settings.mandatory_routing,
            )
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.SUCCESS,
                    transport_type="AMQP",
                )
            return True
        except Exception as exc:
            # Reset exchange/channel state on publish error to ensure retry cleanliness
            await self.close()
            sanitized_err = _sanitize_error_message(str(exc))
            error_msg = f"Failed to publish notification command to broker: {sanitized_err}"
            if session is not None:
                await _record_dispatch_audit(
                    session=session,
                    command=command,
                    outcome=AuditEventOutcome.FAILURE,
                    transport_type="AMQP",
                    error_msg=error_msg,
                )
            raise NotificationDispatchError(error_msg) from exc


def get_notification_dispatcher(
    settings: NotificationSettings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    exchange: aio_pika.abc.AbstractExchange | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> NotificationDispatcherProtocol:
    """Factory producing appropriate NotificationDispatcherProtocol implementation."""
    st = settings or NotificationSettings()
    if st.transport == "broker":
        return AmqpNotificationDispatcher(
            settings=st,
            exchange=exchange,
            connection_factory=connection_factory,
        )
    if st.transport == "http":
        return HttpNotificationDispatcher(
            settings=st,
            http_client=http_client,
        )
    raise NotificationDispatchError(f"Unsupported notification transport: '{st.transport}'")


__all__ = [
    "AmqpNotificationDispatcher",
    "HttpNotificationDispatcher",
    "NotificationDispatchError",
    "NotificationDispatcherProtocol",
    "get_notification_dispatcher",
]
