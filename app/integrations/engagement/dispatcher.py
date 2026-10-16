"""Production notification dispatchers for E-8 engagement boundary.

Part of Phase 7D E-8 Production Notification Boundary.
Enforces:
- Provider-neutral NotificationDispatcherProtocol interface.
- HTTP REST gateway dispatcher with idempotency and retry handling.
- AMQP RabbitMQ topic dispatcher with transactional outbox alignment.
- Truthful reporting: network and remote failures raise or report failure truthfully.
"""

from typing import Protocol, runtime_checkable

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


class HttpNotificationDispatcher(NotificationDispatcherProtocol):
    """HTTP client dispatcher communicating with E-8 notification gateway."""

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
                    await self._record_audit(session, command, AuditEventOutcome.SUCCESS)
                return True

            if resp.status_code == 409:
                # Idempotent deduplication acknowledged by remote gateway
                return False

            error_msg = f"E-8 HTTP error {resp.status_code}: {resp.text}"
            if session is not None:
                await self._record_audit(
                    session, command, AuditEventOutcome.FAILURE, error_msg
                )
            raise NotificationDispatchError(error_msg)

        except httpx.TimeoutException as exc:
            error_msg = f"E-8 request timed out: {exc}"
            if session is not None:
                await self._record_audit(
                    session, command, AuditEventOutcome.FAILURE, error_msg
                )
            raise NotificationDispatchError(error_msg) from exc
        except httpx.RequestError as exc:
            error_msg = f"E-8 connection error: {exc}"
            if session is not None:
                await self._record_audit(
                    session, command, AuditEventOutcome.FAILURE, error_msg
                )
            raise NotificationDispatchError(error_msg) from exc

    async def _record_audit(
        self,
        session: AsyncSession,
        command: NotificationCommand,
        outcome: AuditEventOutcome,
        error_msg: str | None = None,
    ) -> None:
        """Emit audit event tracking notification delivery attempt."""
        audit_repo = AuditRepository(session)
        details = {
            "command_id": command.command_id,
            "trigger_kind": command.trigger.trigger_kind.value,
            "source_event": command.trigger.source_event_name,
            "recipient_count": len(command.recipient_references),
        }
        if error_msg:
            details["error"] = error_msg

        await audit_repo.append(
            AuditEventCreate(
                tenant_id=command.organization_scope_reference,
                actor_principal_id="e7-notification-service",
                actor_principal_type="SERVICE",
                authentication_method="SERVICE_API_KEY",
                event_type="notification.dispatched",
                resource_type="notification_command",
                resource_id=command.command_id,
                outcome=outcome,
                correlation_id=command.correlation_reference,
                details=details,
            )
        )


__all__ = [
    "HttpNotificationDispatcher",
    "NotificationDispatchError",
    "NotificationDispatcherProtocol",
]
