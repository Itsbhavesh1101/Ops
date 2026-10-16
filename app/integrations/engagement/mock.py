"""Deterministic E-8 Notification Mock Dispatcher for Phase-6 vertical slice.

Enforces:
- Receipt and in-memory logging of canonical NotificationCommand objects.
- Idempotency verification: deduplicates repeated commands on command_id.
- Audit emission: appends audit events for notification dispatches.
- Strict isolation: zero real external network or push/email calls (deferred to Phase 7).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.integrations.engagement.models import NotificationCommand


class MockNotificationDispatcher:
    """Mock dispatcher recording and verifying canonical notification commands."""

    def __init__(self) -> None:
        self._received_commands: list[NotificationCommand] = []
        self._command_ids: set[str] = set()

    @property
    def received_commands(self) -> list[NotificationCommand]:
        """All unique notification commands received by this mock."""
        return list(self._received_commands)

    @property
    def receipts(self) -> list[NotificationCommand]:
        """Convenience alias for received_commands."""
        return list(self._received_commands)

    def clear(self) -> None:
        """Reset internal command memory."""
        self._received_commands.clear()
        self._command_ids.clear()

    async def dispatch(
        self,
        command: NotificationCommand,
        session: AsyncSession | None = None,
    ) -> bool:
        """Process an outbound notification command.

        Returns True if command is newly processed, or False if duplicate.
        """
        if command.command_id in self._command_ids:
            # Duplicate command detected: idempotent no-op
            return False

        self._command_ids.add(command.command_id)
        self._received_commands.append(command)

        if session is not None:
            audit_repo = AuditRepository(session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=command.organization_scope_reference,
                    actor_principal_id="e7-notification-service",
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="notification.dispatched",
                    resource_type="notification_command",
                    resource_id=command.command_id,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=command.correlation_reference,
                    details={
                        "command_id": command.command_id,
                        "trigger_kind": command.trigger.trigger_kind.value,
                        "source_event": command.trigger.source_event_name,
                        "business_condition": command.trigger.business_condition,
                        "recipient_count": len(command.recipient_references),
                        "notification_context": command.notification_context,
                    },
                )
            )

        return True

    def has_received(self, command_id: str) -> bool:
        """Check if command with given command_id was received."""
        return command_id in self._command_ids

    def get_receipt(self, command_id: str) -> NotificationCommand | None:
        """Get received command by command_id."""
        for cmd in self._received_commands:
            if cmd.command_id == command_id:
                return cmd
        return None

    def get_receipts_by_correlation(self, correlation_id: str) -> list[NotificationCommand]:
        """Get all received commands matching the correlation reference."""
        return [
            cmd
            for cmd in self._received_commands
            if cmd.correlation_reference == correlation_id
        ]


__all__ = ["MockNotificationDispatcher"]
