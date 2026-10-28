"""Recovery service for administratively unblocking notification intents (Phase DQ-4 AD-DQ4-01).

Enforces:
- Strict role-based access control (RBAC): only 'admin' or 'operations_manager' can unblock.
- Strict opaque recipient validation: enforces E-8 privacy boundaries
  (rejects emails, phone numbers, PII, MySQL IDs).
- Audit trail: emits tamper-evident audit events for recovery successes, denials, and failures.
- Transition state: BLOCKED -> PENDING with immediate eligibility (next_attempt_at = now).
"""

from collections.abc import Collection

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.integrations.engagement.models import NotificationCommand
from app.integrations.engagement.repository import NotificationOutboxRepository

AUTHORIZED_RECOVERY_ROLES: frozenset[str] = frozenset({
    "admin",
    "operations_manager",
    "system",
    "SYSTEM",
})


class NotificationRecoveryService:
    """Service providing authorized administrative remediation for blocked notification commands."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.outbox_repo = NotificationOutboxRepository(session)
        self.audit_repo = AuditRepository(session)

    async def recover_blocked_notification(
        self,
        *,
        actor_principal_id: str,
        actor_roles: Collection[str] | str,
        tenant_id: str,
        command_id: str,
        resolved_recipients: list[str],
    ) -> NotificationOutboxCommandRecord:
        """Unblock a blocked notification intent after institutional recipient resolution.

        Args:
            actor_principal_id: Principal ID of administrator executing recovery.
            actor_roles: Roles assigned to actor (must include 'admin' or 'operations_manager').
            tenant_id: Target tenant scope.
            command_id: Unique identifier of blocked notification command.
            resolved_recipients: Authorized list of opaque recipient references.

        Returns:
            Updated NotificationOutboxCommandRecord in PENDING state.

        Raises:
            PermissionError: If actor lacks required administrative role.
            ValueError: If recipients contain prohibited contact PII or command is not found.
        """
        # 1. RBAC authorization check
        roles_set = {actor_roles} if isinstance(actor_roles, str) else set(actor_roles)
        if not roles_set.intersection(AUTHORIZED_RECOVERY_ROLES):
            await self.audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="notification.recovery.denied",
                    actor_principal_id=actor_principal_id,
                    actor_principal_type=PrincipalType.HUMAN.value,
                    authentication_method=AuthenticationMethod.OIDC.value,
                    resource_type="notification_command",
                    resource_id=command_id,
                    outcome=AuditEventOutcome.DENIED,
                    details={
                        "reason": "UNAUTHORIZED_ROLE",
                        "command_id": command_id,
                        "provided_roles": sorted(roles_set),
                    },
                )
            )
            raise PermissionError(
                "Actor does not possess required role ('admin' or 'operations_manager') "
                "to recover blocked notification commands."
            )

        # 2. Strict recipient validation
        try:
            NotificationCommand.validate_recipients(resolved_recipients)
        except Exception as exc:
            await self.audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="notification.recovery.rejected",
                    actor_principal_id=actor_principal_id,
                    actor_principal_type=PrincipalType.HUMAN.value,
                    authentication_method=AuthenticationMethod.OIDC.value,
                    resource_type="notification_command",
                    resource_id=command_id,
                    outcome=AuditEventOutcome.DENIED,
                    details={
                        "reason": "INVALID_RECIPIENT_REFERENCES",
                        "command_id": command_id,
                        "validation_error": str(exc),
                    },
                )
            )
            raise ValueError(f"Invalid resolved recipient references: {exc}") from exc

        # 3. Transition blocked record to PENDING
        record = await self.outbox_repo.recover_blocked_command(
            tenant_id=tenant_id,
            command_id=command_id,
            resolved_recipients=resolved_recipients,
        )
        if record is None:
            await self.audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="notification.recovery.failed",
                    actor_principal_id=actor_principal_id,
                    actor_principal_type=PrincipalType.HUMAN.value,
                    authentication_method=AuthenticationMethod.OIDC.value,
                    resource_type="notification_command",
                    resource_id=command_id,
                    outcome=AuditEventOutcome.FAILURE,
                    details={
                        "reason": "COMMAND_NOT_BLOCKED_OR_NOT_FOUND",
                        "command_id": command_id,
                    },
                )
            )
            raise ValueError(
                f"Blocked notification command '{command_id}' not found for tenant '{tenant_id}'."
            )

        # 4. Record audit event for successful recovery
        await self.audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="notification.dispatch.recovered",
                actor_principal_id=actor_principal_id,
                actor_principal_type=PrincipalType.HUMAN.value,
                authentication_method=AuthenticationMethod.OIDC.value,
                resource_type="notification_command",
                resource_id=command_id,
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "command_id": command_id,
                    "resolved_recipient_count": len(resolved_recipients),
                    "new_dispatch_state": "PENDING",
                },
            )
        )

        return record


__all__ = ["NotificationRecoveryService", "AUTHORIZED_RECOVERY_ROLES"]
