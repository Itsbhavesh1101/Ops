"""SLA Service managing task SLA projections, timers, pause/resume, and escalations.

Enforces:
1. Deterministic Calculation:
   - Evaluates warning_at and breach_at from versioned SLA policy and business calendar.
   - Historical replay guarantee: identical inputs yield identical timestamps.
2. Pause & Resume Semantics:
   - On pause: captures paused_at and freezes remaining business duration.
   - On resume: recomputes new warning_at and breach_at from remaining duration.
3. Warning and Breach Triggers:
   - Warning emits frozen canonical event `workflow.sla.warning` to outbox.
   - Breach marks projection BREACHED and appends audit event `workflow.sla.breach`.
   - Escalation executes configured reassignment or service action idempotently.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
from app.events.schemas import EventCreate, create_event_envelope
from app.workflow.sla.calendar import (
    BusinessCalendarPolicyProtocol,
    DefaultBusinessCalendarPolicy,
)
from app.workflow.sla.models import (
    SlaPolicyConfig,
    SlaStatus,
    TaskSlaState,
)


class SlaPolicyNotFoundError(ValueError):
    """Raised when referenced SLA policy does not exist."""


class SlaTaskStateNotFoundError(ValueError):
    """Raised when referenced task SLA state is not found."""


class SlaService:
    """Service orchestrating task SLA projections, evaluations, and escalations."""

    def __init__(
        self,
        calendar_policy: BusinessCalendarPolicyProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._calendar = calendar_policy or DefaultBusinessCalendarPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._policies: dict[str, SlaPolicyConfig] = {}
        self._states: dict[UUID, TaskSlaState] = {}

    def register_policy(self, policy: SlaPolicyConfig) -> None:
        """Register or update an SLA policy specification."""
        self._policies[policy.policy_key] = policy

    def get_policy(self, policy_key: str) -> SlaPolicyConfig:
        """Retrieve an SLA policy specification."""
        if policy_key not in self._policies:
            raise SlaPolicyNotFoundError(f"SLA policy '{policy_key}' is not registered.")
        return self._policies[policy_key]

    def compute_thresholds(
        self,
        start_time: datetime,
        policy: SlaPolicyConfig,
    ) -> tuple[datetime, datetime]:
        """Deterministically compute warning_at and breach_at given calendar rules."""
        warning_at = self._calendar.add_business_duration(
            start_time, policy.warning_duration_seconds, policy.calendar_ref
        )
        breach_at = self._calendar.add_business_duration(
            start_time, policy.breach_duration_seconds, policy.calendar_ref
        )
        return warning_at, breach_at

    def attach_task_sla(
        self,
        tenant_id: str,
        instance_id: UUID,
        task_id: UUID,
        policy_key: str,
        created_at: datetime | None = None,
    ) -> TaskSlaState:
        """Attach an SLA policy to a newly created user task and initialize tracking."""
        policy = self.get_policy(policy_key)
        start_time = created_at or datetime.now(UTC)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)

        warning_at, breach_at = self.compute_thresholds(start_time, policy)

        state = TaskSlaState(
            task_id=task_id,
            instance_id=instance_id,
            tenant_id=tenant_id,
            policy_key=policy_key,
            status=SlaStatus.RUNNING,
            created_at=start_time,
            warning_at=warning_at,
            breach_at=breach_at,
        )
        self._states[task_id] = state
        return state

    def get_task_sla(self, task_id: UUID) -> TaskSlaState:
        """Retrieve the SLA state for a task."""
        if task_id not in self._states:
            raise SlaTaskStateNotFoundError(f"No SLA state found for task '{task_id}'.")
        return self._states[task_id]

    def pause_task_sla(
        self,
        tenant_id: str,
        task_id: UUID,
        paused_at: datetime | None = None,
    ) -> TaskSlaState:
        """Pause SLA progression on a task (e.g. awaiting external user input)."""
        state = self.get_task_sla(task_id)
        if state.tenant_id != tenant_id:
            raise SlaTaskStateNotFoundError(f"Task '{task_id}' not found in tenant '{tenant_id}'.")

        if state.status == SlaStatus.PAUSED:
            return state

        now = paused_at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        state.status = SlaStatus.PAUSED
        state.paused_at = now
        policy = self.get_policy(state.policy_key)
        state.remaining_warning_seconds = self._calendar.get_remaining_business_duration(
            now, state.warning_at, policy.calendar_ref
        )
        state.remaining_breach_seconds = self._calendar.get_remaining_business_duration(
            now, state.breach_at, policy.calendar_ref
        )
        return state

    def resume_task_sla(
        self,
        tenant_id: str,
        task_id: UUID,
        resumed_at: datetime | None = None,
    ) -> TaskSlaState:
        """Resume SLA progression on a paused task and recompute new thresholds preserving
        business duration.
        """
        state = self.get_task_sla(task_id)
        if state.tenant_id != tenant_id:
            raise SlaTaskStateNotFoundError(f"Task '{task_id}' not found in tenant '{tenant_id}'.")

        if state.status != SlaStatus.PAUSED or not state.paused_at:
            return state

        now = resumed_at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        paused_duration = int((now - state.paused_at).total_seconds())
        if paused_duration > 0:
            state.total_paused_seconds += paused_duration
            policy = self.get_policy(state.policy_key)
            # Recompute thresholds from resume timestamp preserving exact business duration
            if state.remaining_warning_seconds is not None:
                state.warning_at = self._calendar.add_business_duration(
                    now, state.remaining_warning_seconds, policy.calendar_ref
                )
            if state.remaining_breach_seconds is not None:
                state.breach_at = self._calendar.add_business_duration(
                    now, state.remaining_breach_seconds, policy.calendar_ref
                )

        state.status = SlaStatus.RUNNING
        state.paused_at = None
        state.remaining_warning_seconds = None
        state.remaining_breach_seconds = None
        return state

    async def evaluate_triggers(
        self,
        as_of: datetime,
        outbox_repo: Any,
        audit_repo: Any,
    ) -> list[str]:
        """Evaluate active SLAs against current time and trigger warnings or breach escalations."""
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)

        triggered_actions: list[str] = []

        for _task_id, state in list(self._states.items()):
            if state.status in (SlaStatus.PAUSED, SlaStatus.COMPLETED):
                continue

            policy = self.get_policy(state.policy_key)

            # 1. Warning Evaluation
            if as_of >= state.warning_at and state.warning_fired_at is None:
                state.warning_fired_at = as_of
                state.status = SlaStatus.WARNING

                # Emit frozen canonical event: workflow.sla.warning
                warning_payload = {
                    "workflow_instance_id": str(state.instance_id),
                    "workflow_task_id": str(state.task_id),
                    "policy_key": state.policy_key,
                    "warning_at": state.warning_at.isoformat(),
                    "breach_at": state.breach_at.isoformat(),
                    "action": policy.warning_escalation.action.value,
                }
                envelope = create_event_envelope(
                    EventCreate(
                        event_type="workflow.sla.warning",
                        subject_type="workflow_task",
                        subject_id=str(state.task_id),
                        payload=warning_payload,
                    ),
                    clock=self._clock,
                )
                await outbox_repo.append(envelope)

                # Record Audit Event
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=state.tenant_id,
                        event_type="workflow.sla.warning",
                        actor_principal_id="system-sla-monitor",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="workflow_task",
                        resource_id=str(state.task_id),
                        outcome=AuditEventOutcome.SUCCESS,
                        details=warning_payload,
                    )
                )
                triggered_actions.append(f"WARNING:{state.task_id}")

            # 2. Breach Evaluation
            if as_of >= state.breach_at and state.breach_fired_at is None:
                state.breach_fired_at = as_of
                state.status = SlaStatus.BREACHED

                breach_payload = {
                    "workflow_instance_id": str(state.instance_id),
                    "workflow_task_id": str(state.task_id),
                    "policy_key": state.policy_key,
                    "breached_at": as_of.isoformat(),
                    "escalation_action": policy.breach_escalation.action.value,
                    "target_role": policy.breach_escalation.target_role,
                    "target_principal_id": policy.breach_escalation.target_principal_id,
                }

                breach_envelope = create_event_envelope(
                    EventCreate(
                        event_type="workflow.sla.breach",
                        subject_type="workflow_task",
                        subject_id=str(state.task_id),
                        payload=breach_payload,
                    ),
                    clock=self._clock,
                )
                await outbox_repo.append(breach_envelope)

                # Record append-only audit event: workflow.sla.breach
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=state.tenant_id,
                        event_type="workflow.sla.breach",
                        actor_principal_id="system-sla-monitor",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="workflow_task",
                        resource_id=str(state.task_id),
                        outcome=AuditEventOutcome.SUCCESS,
                        details=breach_payload,
                    )
                )
                triggered_actions.append(f"BREACH:{state.task_id}")

        return triggered_actions
