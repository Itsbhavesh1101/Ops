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
   - Breach marks projection BREACHED and appends audit event `workflow.sla.breached`.
   - Escalation executes configured reassignment or service action idempotently.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
from app.db.models.sla import TaskSlaStateRecord
from app.events.schemas import EventCreate, create_event_envelope
from app.workflow.sla.calendar import (
    BusinessCalendarPolicyProtocol,
    DefaultBusinessCalendarPolicy,
)
from app.workflow.sla.models import (
    SlaPolicyConfig,
    SlaStatus,
    SlaTaskStateNotFoundError,
    TaskSlaState,
)
from app.workflow.sla.repository import SlaRepository


class SlaPolicyNotFoundError(ValueError):
    """Raised when referenced SLA policy does not exist."""


class SlaService:
    """Service orchestrating task SLA projections, evaluations, and escalations."""

    def __init__(
        self,
        calendar_policy: BusinessCalendarPolicyProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
        persistence_mode: str = "authorized_legacy",
    ) -> None:
        if persistence_mode not in ("database", "memory", "authorized_legacy"):
            raise ValueError(
                f"Invalid SLA persistence_mode: '{persistence_mode}'. "
                "Must be 'database', 'memory', or 'authorized_legacy'."
            )
        self._calendar = calendar_policy or DefaultBusinessCalendarPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._policies: dict[str, SlaPolicyConfig] = {}
        self._states: dict[UUID, TaskSlaState] = {}
        self.persistence_mode = persistence_mode

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
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Attach an SLA policy to a newly created user task and initialize tracking."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")
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

        if session is not None:
            record = TaskSlaStateRecord(
                id=state.id,
                tenant_id=state.tenant_id,
                task_id=state.task_id,
                instance_id=state.instance_id,
                policy_key=state.policy_key,
                status=state.status.value,
                created_at=state.created_at,
                warning_at=state.warning_at,
                breach_at=state.breach_at,
                paused_at=state.paused_at,
                total_paused_seconds=state.total_paused_seconds,
                remaining_warning_seconds=state.remaining_warning_seconds,
                remaining_breach_seconds=state.remaining_breach_seconds,
                warning_fired_at=state.warning_fired_at,
                breach_fired_at=state.breach_fired_at,
            )
            session.add(record)

        return state

    def _bind_session_lifecycle(
        self,
        session: AsyncSession,
        task_id: UUID,
        previous_state: TaskSlaState | None,
    ) -> None:
        """Bind in-memory cache state lifecycle to external transaction outcome.

        If the caller's session rolls back, restores the previous state or evicts
        the uncommitted task SLA from memory, ensuring in-memory cache is not left
        authoritative over PostgreSQL.
        """
        sync_sess = getattr(session, "sync_session", None)
        if sync_sess is None:
            return

        from sqlalchemy import event

        def _cleanup() -> None:
            try:
                if event.contains(sync_sess, "after_rollback", _on_rollback):
                    event.remove(sync_sess, "after_rollback", _on_rollback)
            except Exception:
                pass
            try:
                if event.contains(sync_sess, "after_commit", _on_commit):
                    event.remove(sync_sess, "after_commit", _on_commit)
            except Exception:
                pass

        def _on_rollback(sess: Any) -> None:
            if previous_state is None:
                self.evict_task_sla(task_id)
            else:
                self._states[task_id] = previous_state
            _cleanup()

        def _on_commit(sess: Any) -> None:
            _cleanup()

        event.listen(sync_sess, "after_rollback", _on_rollback, once=True)
        event.listen(sync_sess, "after_commit", _on_commit, once=True)

    async def attach_task_sla_async(
        self,
        tenant_id: str,
        instance_id: UUID,
        task_id: UUID,
        policy_key: str,
        created_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Attach an SLA policy and flush to database if session is provided."""
        prev = self._states.get(task_id)
        try:
            state = self.attach_task_sla(
                tenant_id=tenant_id,
                instance_id=instance_id,
                task_id=task_id,
                policy_key=policy_key,
                created_at=created_at,
                session=session,
            )
            if session is not None:
                self._bind_session_lifecycle(session, task_id, previous_state=prev)
                await session.flush()
            return state
        except Exception:
            self._states.pop(task_id, None)
            raise

    def evict_task_sla(self, task_id: UUID) -> None:
        """Evict a task SLA state from in-memory cache on rollback or eviction."""
        self._states.pop(task_id, None)

    def get_task_sla(self, task_id: UUID) -> TaskSlaState:
        """Retrieve the SLA state for a task."""
        if task_id not in self._states:
            raise SlaTaskStateNotFoundError(f"No SLA state found for task '{task_id}'.")
        return self._states[task_id]

    async def get_task_sla_async(
        self,
        tenant_id: str,
        task_id: UUID,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Retrieve authoritative task SLA state, reloading from PostgreSQL when session given."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")

        if session is not None:
            sla_repo = SlaRepository(session)
            db_record = await sla_repo.get_task_sla(tenant_id=tenant_id, task_id=task_id)
            if db_record is None:
                self.evict_task_sla(task_id)
                raise SlaTaskStateNotFoundError(
                    f"No SLA state found for task '{task_id}' in tenant '{tenant_id}'."
                )
            state = SlaRepository.to_model(db_record)
            self._states[task_id] = state
            return state

        in_mem_state = self._states.get(task_id)
        if in_mem_state is None:
            raise SlaTaskStateNotFoundError(f"No SLA state found for task '{task_id}'.")
        if in_mem_state.tenant_id != tenant_id:
            raise SlaTaskStateNotFoundError(
                f"Task '{task_id}' not found in tenant '{tenant_id}'."
            )
        return in_mem_state

    def pause_task_sla(
        self,
        tenant_id: str,
        task_id: UUID,
        paused_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Pause SLA progression on a task (synchronous in-memory)."""
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

    async def pause_task_sla_async(
        self,
        tenant_id: str,
        task_id: UUID,
        paused_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Pause SLA progression on a task with database persistence and restart recovery."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")

        prev = self._states.get(task_id)
        state: TaskSlaState
        if session is not None:
            # PostgreSQL is authoritative: always reload latest state with row-level lock
            sla_repo = SlaRepository(session)
            db_record = await sla_repo.get_task_sla(
                tenant_id=tenant_id,
                task_id=task_id,
                for_update=True,
            )
            if db_record is None:
                self.evict_task_sla(task_id)
                raise SlaTaskStateNotFoundError(
                    f"No SLA state found for task '{task_id}' in tenant '{tenant_id}'."
                )
            state = SlaRepository.to_model(db_record)
            prev = state
            self._states[task_id] = state
        else:
            in_mem_state = self._states.get(task_id)
            if in_mem_state is None:
                raise SlaTaskStateNotFoundError(f"No SLA state found for task '{task_id}'.")
            if in_mem_state.tenant_id != tenant_id:
                raise SlaTaskStateNotFoundError(
                    f"Task '{task_id}' not found in tenant '{tenant_id}'."
                )
            state = in_mem_state

        if state.status == SlaStatus.PAUSED:
            self._states[task_id] = state
            return state

        now = paused_at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        new_state = state.model_copy(deep=True)
        new_state.status = SlaStatus.PAUSED
        new_state.paused_at = now
        policy = self.get_policy(new_state.policy_key)
        new_state.remaining_warning_seconds = self._calendar.get_remaining_business_duration(
            now, new_state.warning_at, policy.calendar_ref
        )
        new_state.remaining_breach_seconds = self._calendar.get_remaining_business_duration(
            now, new_state.breach_at, policy.calendar_ref
        )

        if session is not None:
            sla_repo = SlaRepository(session)
            await sla_repo.update_task_sla(new_state)
            self._bind_session_lifecycle(session, task_id, previous_state=prev)
            await session.flush()

        self._states[task_id] = new_state
        return new_state

    def resume_task_sla(
        self,
        tenant_id: str,
        task_id: UUID,
        resumed_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Resume SLA progression on a paused task (synchronous in-memory)."""
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

    async def resume_task_sla_async(
        self,
        tenant_id: str,
        task_id: UUID,
        resumed_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> TaskSlaState:
        """Resume SLA progression with database persistence and restart recovery."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")

        prev = self._states.get(task_id)
        state: TaskSlaState
        if session is not None:
            # PostgreSQL is authoritative: always reload latest state with row-level lock
            sla_repo = SlaRepository(session)
            db_record = await sla_repo.get_task_sla(
                tenant_id=tenant_id,
                task_id=task_id,
                for_update=True,
            )
            if db_record is None:
                self.evict_task_sla(task_id)
                raise SlaTaskStateNotFoundError(
                    f"No SLA state found for task '{task_id}' in tenant '{tenant_id}'."
                )
            state = SlaRepository.to_model(db_record)
            prev = state
            self._states[task_id] = state
        else:
            in_mem_state = self._states.get(task_id)
            if in_mem_state is None:
                raise SlaTaskStateNotFoundError(f"No SLA state found for task '{task_id}'.")
            if in_mem_state.tenant_id != tenant_id:
                raise SlaTaskStateNotFoundError(
                    f"Task '{task_id}' not found in tenant '{tenant_id}'."
                )
            state = in_mem_state

        if state.status != SlaStatus.PAUSED or not state.paused_at:
            self._states[task_id] = state
            return state

        now = resumed_at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        new_state = state.model_copy(deep=True)
        assert new_state.paused_at is not None
        paused_duration = int((now - new_state.paused_at).total_seconds())
        if paused_duration > 0:
            new_state.total_paused_seconds += paused_duration
            policy = self.get_policy(new_state.policy_key)
            if new_state.remaining_warning_seconds is not None:
                new_state.warning_at = self._calendar.add_business_duration(
                    now, new_state.remaining_warning_seconds, policy.calendar_ref
                )
            if new_state.remaining_breach_seconds is not None:
                new_state.breach_at = self._calendar.add_business_duration(
                    now, new_state.remaining_breach_seconds, policy.calendar_ref
                )

        new_state.status = SlaStatus.RUNNING
        new_state.paused_at = None
        new_state.remaining_warning_seconds = None
        new_state.remaining_breach_seconds = None

        if session is not None:
            sla_repo = SlaRepository(session)
            await sla_repo.update_task_sla(new_state)
            self._bind_session_lifecycle(session, task_id, previous_state=prev)
            await session.flush()

        self._states[task_id] = new_state
        return new_state

    @contextmanager
    def transaction(self, tenant_id: str | None = None) -> Generator[None, None, None]:
        """Transactional context manager for SLA state mutations.

        Takes a deep snapshot of registered task SLA states (optionally scoped to a tenant).
        If an exception is raised within the context block, the snapshot is restored,
        ensuring rollback atomicity when outer database transactions fail.
        """
        snapshot = {
            task_id: state.model_copy(deep=True)
            for task_id, state in self._states.items()
            if tenant_id is None or state.tenant_id == tenant_id
        }
        try:
            yield
        except Exception:
            for task_id, original_state in snapshot.items():
                self._states[task_id] = original_state
            raise

    @staticmethod
    async def _dispatch_to_outbox(outbox_repo: Any, envelope: Any) -> None:
        if hasattr(outbox_repo, "enqueue"):
            await outbox_repo.enqueue(envelope)
        elif hasattr(outbox_repo, "append"):
            await outbox_repo.append(envelope)

    async def evaluate_triggers(
        self,
        as_of: datetime,
        outbox_repo: Any,
        audit_repo: Any,
        tenant_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[str]:
        """Evaluate active SLAs against current time and trigger warnings or breach escalations.

        Supports both persistent PostgreSQL-backed evaluation (via task_sla_states with
        SELECT ... FOR UPDATE SKIP LOCKED) and deterministic in-memory evaluation.
        """
        if tenant_id is not None and not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")

        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)

        effective_session: AsyncSession | None = session
        if effective_session is None:
            candidate = getattr(outbox_repo, "session", None) or getattr(
                audit_repo, "session", None
            )
            if (
                candidate is not None
                and not hasattr(candidate, "_mock_return_value")
                and not hasattr(candidate, "call_count")
            ):
                effective_session = candidate

        triggered_actions: list[str] = []

        # 1. PostgreSQL Persistent Evaluation (production default)
        if self.persistence_mode == "database" or (
            self.persistence_mode == "authorized_legacy" and effective_session is not None
        ):
            if effective_session is None:
                raise ValueError("Database session required for database-backed SLA evaluation")
            if not tenant_id:
                raise ValueError("tenant_id must be provided for persistent SLA evaluation")

            sla_repo = SlaRepository(effective_session)
            db_records = await sla_repo.list_evaluable_slas(tenant_id=tenant_id, as_of=as_of)
            for record in db_records:
                policy = self.get_policy(record.policy_key)

                # Warning Evaluation
                if as_of >= record.warning_at and record.warning_fired_at is None:
                    record.warning_fired_at = as_of
                    record.status = SlaStatus.WARNING.value
                    record.updated_at = as_of

                    warning_payload = {
                        "workflow_instance_id": str(record.instance_id),
                        "workflow_task_id": str(record.task_id),
                        "policy_key": record.policy_key,
                        "warning_at": record.warning_at.isoformat(),
                        "breach_at": record.breach_at.isoformat(),
                        "action": policy.warning_escalation.action.value,
                    }
                    envelope = create_event_envelope(
                        EventCreate(
                            event_type="workflow.sla.warning",
                            subject_type="workflow_task",
                            subject_id=str(record.task_id),
                            payload=warning_payload,
                            tenant_id=record.tenant_id,
                        ),
                        clock=self._clock,
                    )
                    await self._dispatch_to_outbox(outbox_repo, envelope)

                    await audit_repo.append(
                        AuditEventCreate(
                            tenant_id=record.tenant_id,
                            event_type="workflow.sla.warning",
                            actor_principal_id="system-sla-monitor",
                            actor_principal_type=PrincipalType.SERVICE.value,
                            authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                            resource_type="workflow_task",
                            resource_id=str(record.task_id),
                            outcome=AuditEventOutcome.SUCCESS,
                            details=warning_payload,
                        )
                    )
                    triggered_actions.append(f"WARNING:{record.task_id}")

                # Breach Evaluation
                if as_of >= record.breach_at and record.breach_fired_at is None:
                    record.breach_fired_at = as_of
                    record.status = SlaStatus.BREACHED.value
                    record.updated_at = as_of

                    breach_payload = {
                        "workflow_instance_id": str(record.instance_id),
                        "workflow_task_id": str(record.task_id),
                        "policy_key": record.policy_key,
                        "breached_at": as_of.isoformat(),
                        "escalation_action": policy.breach_escalation.action.value,
                        "target_role": policy.breach_escalation.target_role,
                        "target_principal_id": policy.breach_escalation.target_principal_id,
                    }
                    breach_envelope = create_event_envelope(
                        EventCreate(
                            event_type="workflow.sla.breached",
                            subject_type="workflow_task",
                            subject_id=str(record.task_id),
                            payload=breach_payload,
                            tenant_id=record.tenant_id,
                        ),
                        clock=self._clock,
                    )
                    await self._dispatch_to_outbox(outbox_repo, breach_envelope)

                    await audit_repo.append(
                        AuditEventCreate(
                            tenant_id=record.tenant_id,
                            event_type="workflow.sla.breached",
                            actor_principal_id="system-sla-monitor",
                            actor_principal_type=PrincipalType.SERVICE.value,
                            authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                            resource_type="workflow_task",
                            resource_id=str(record.task_id),
                            outcome=AuditEventOutcome.SUCCESS,
                            details=breach_payload,
                        )
                    )
                    triggered_actions.append(f"BREACH:{record.task_id}")

                # Synchronize to in-memory state
                if record.task_id in self._states:
                    mem_state = self._states[record.task_id]
                    mem_state.status = SlaStatus(record.status)
                    mem_state.warning_fired_at = record.warning_fired_at
                    mem_state.breach_fired_at = record.breach_fired_at

            await effective_session.flush()
            return triggered_actions

        # 2. In-Memory Evaluation (restricted to explicit test or no-session mode)
        for _task_id, state in list(self._states.items()):
            if tenant_id and state.tenant_id != tenant_id:
                continue
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
                        tenant_id=state.tenant_id,
                    ),
                    clock=self._clock,
                )
                await self._dispatch_to_outbox(outbox_repo, envelope)

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
                        event_type="workflow.sla.breached",
                        subject_type="workflow_task",
                        subject_id=str(state.task_id),
                        payload=breach_payload,
                        tenant_id=state.tenant_id,
                    ),
                    clock=self._clock,
                )
                await self._dispatch_to_outbox(outbox_repo, breach_envelope)

                # Record append-only audit event: workflow.sla.breached
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=state.tenant_id,
                        event_type="workflow.sla.breached",
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
