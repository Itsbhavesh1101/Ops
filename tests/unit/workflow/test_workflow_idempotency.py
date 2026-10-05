"""Unit tests for Workflow Human-Task Action Idempotency using Phase-1 Contracts.

Verifies:
A. Same key + same action + same variables = replay = zero second Flowable call
B. Same key + same action + changed variable = conflict = zero second Flowable call
C. Same key + different action = conflict = zero second Flowable call
D. Different key on completed task = NOT treated as idempotency replay
E. Unauthorized principal using a valid replay key = denied before replay disclosure
F. Raw idempotency key absent from logs/models/database (only key_digest retained)
G. Audit/outbox count unchanged on replay
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.idempotency.contracts import (
    RequestFingerprint,
    create_idempotency_identity,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.core.idempotency.hashing import digest_idempotency_key, fingerprint_bytes
from app.core.idempotency.repository import (
    IdempotencyDecision,
    IdempotencyDecisionType,
)
from app.workflow.authorization import (
    WorkflowTaskUnauthorizedError,
)
from app.workflow.models import (
    WorkflowTaskMappingRecord,
)
from app.workflow.schemas import (
    WorkflowTaskAction,
    WorkflowTaskStatus,
)
from app.workflow.service import (
    WorkflowService,
    WorkflowTaskAlreadyCompletedError,
)
from app.workflow.variables import (
    WorkflowVariablePolicy,
)


class FakeUnitOfWork:
    def __init__(self):
        self.session = AsyncMock()
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rolled_back = True
        return None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.fixture
def fake_uow_factory():
    return lambda: FakeUnitOfWork()


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.complete_user_task = AsyncMock()
    return adapter


@pytest.fixture
def authorized_chair():
    return AuthenticatedPrincipal(
        principal_id="chair-1",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"chairs"}),
    )


@pytest.fixture
def unauthorized_rogue():
    return AuthenticatedPrincipal(
        principal_id="rogue-1",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"students"}),
    )


def _make_pending_task(task_id: uuid.UUID, inst_id: uuid.UUID) -> WorkflowTaskMappingRecord:
    return WorkflowTaskMappingRecord(
        id=task_id,
        workflow_instance_id=inst_id,
        tenant_id="tenant-alpha",
        engine_task_id="engine-task-99",
        task_definition_key="chair_review",
        task_name="Department Chair Review",
        assignee="chair-1",
        candidate_roles=["chairs"],
        status=WorkflowTaskStatus.PENDING.value,
        action=None,
        action_by=None,
        action_at=None,
        due_at=None,
        variables={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_case_a_same_key_same_action_same_variables_is_replay(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case A: same key + same action + same variables = replay = zero second Flowable call."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=frozenset({"decision", "comment"})),
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)
    task.status = WorkflowTaskStatus.COMPLETED.value
    task.action = WorkflowTaskAction.APPROVE.value
    task.action_by = "chair-1"

    # Simulate existing match in idempotency repository
    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key="key-replay-1",
        )
        payload_str = (
            f'{{"action":"APPROVE","variables":{{"decision":"approved"}},'
            f'"workflow_task_id":"{task_id}"}}'
        )
        fp = fingerprint_bytes(payload_str.encode("utf-8"))
        match_decision = IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = match_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        res = await service.perform_task_action(
            tenant_id="tenant-alpha",
            workflow_task_id=task_id,
            action=WorkflowTaskAction.APPROVE,
            principal=authorized_chair,
            idempotency_key="key-replay-1",
            variables={"decision": "approved"},
        )

        assert res.id == task_id
        assert res.status == WorkflowTaskStatus.COMPLETED
        # Flowable adapter MUST NOT be invoked on replay
        mock_adapter.complete_user_task.assert_not_awaited()
        # Audit append MUST NOT be called on replay
        mock_audit_repo.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_b_same_key_same_action_changed_variable_is_conflict(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case B: same key + same action + changed variable = conflict = zero second Flowable call."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=frozenset({"decision", "comment"})),
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key="key-conflict-1",
        )
        fp_new = RequestFingerprint("1" * 64)
        fp_old = RequestFingerprint("2" * 64)

        conflict_decision = IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=fp_new,
            existing_fingerprint=fp_old,
        )
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = conflict_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        with pytest.raises(IdempotencyConflictError) as exc_info:
            await service.perform_task_action(
                tenant_id="tenant-alpha",
                workflow_task_id=task_id,
                action=WorkflowTaskAction.APPROVE,
                principal=authorized_chair,
                idempotency_key="key-conflict-1",
                variables={"decision": "different_decision"},
            )

        assert exc_info.value.identity == identity
        # Flowable MUST NOT be called on conflict
        mock_adapter.complete_user_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_c_same_key_different_action_is_conflict(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case C: same key + different action = conflict = zero second Flowable call."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=frozenset({"decision", "comment"})),
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key="key-conflict-2",
        )
        fp_new = RequestFingerprint("a" * 64)
        fp_old = RequestFingerprint("b" * 64)

        conflict_decision = IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=fp_new,
            existing_fingerprint=fp_old,
        )
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = conflict_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        with pytest.raises(IdempotencyConflictError):
            await service.perform_task_action(
                tenant_id="tenant-alpha",
                workflow_task_id=task_id,
                action=WorkflowTaskAction.REJECT,  # Different action
                principal=authorized_chair,
                idempotency_key="key-conflict-2",
                variables={"decision": "rejected"},
            )

        mock_adapter.complete_user_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_d_different_key_on_completed_task_not_treated_as_replay(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case D: different key on completed task = NOT treated as idempotency replay."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)
    task.status = WorkflowTaskStatus.COMPLETED.value
    task.action = WorkflowTaskAction.APPROVE.value

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        # Different key provisionally acquires (not an existing match)
        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key="new-key-on-completed-task",
        )
        fp = RequestFingerprint("c" * 64)
        acquired_decision = IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=None,
        )
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = acquired_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        # Must raise WorkflowTaskAlreadyCompletedError, NOT treated as replay!
        with pytest.raises(WorkflowTaskAlreadyCompletedError) as exc_info:
            await service.perform_task_action(
                tenant_id="tenant-alpha",
                workflow_task_id=task_id,
                action=WorkflowTaskAction.APPROVE,
                principal=authorized_chair,
                idempotency_key="new-key-on-completed-task",
                variables={"decision": "approved"},
            )

        assert "already completed" in str(exc_info.value)
        mock_adapter.complete_user_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_e_unauthorized_principal_using_valid_replay_key_denied(
    fake_uow_factory, mock_adapter, unauthorized_rogue
):
    """Case E: unauthorized principal using a valid replay key = denied before replay disclosure."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        mock_idem_repo = AsyncMock()
        mock_idem_repo_cls.return_value = mock_idem_repo

        with pytest.raises(WorkflowTaskUnauthorizedError) as exc_info:
            await service.perform_task_action(
                tenant_id="tenant-alpha",
                workflow_task_id=task_id,
                action=WorkflowTaskAction.APPROVE,
                principal=unauthorized_rogue,  # Rogue student trying to approve
                idempotency_key="valid-replay-key-from-chair",
                variables={"decision": "approved"},
            )

        assert exc_info.value.principal_id == "rogue-1"
        # Idempotency repository was NOT even consulted for unauthorized actor!
        mock_idem_repo.acquire_or_observe.assert_not_awaited()
        # Engine was NOT called
        mock_adapter.complete_user_task.assert_not_awaited()
        # Audit was written with outcome DENIED
        mock_audit_repo.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_case_f_raw_idempotency_key_absent_from_audit_and_models(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case F: raw idempotency key absent from logs/models/database (only key_digest retained)."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=frozenset({"decision", "comment"})),
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    task = _make_pending_task(task_id, inst_id)

    raw_key = "sensitive-client-key-xyz-789"
    expected_digest = str(digest_idempotency_key(raw_key))

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
        patch.object(service, "sync_instance_tasks"),
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task
        mock_task_repo_cls.return_value = mock_task_repo

        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key=raw_key,
        )
        fp = RequestFingerprint("d" * 64)
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=None,
        )
        mock_idem_repo_cls.return_value = mock_idem_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        await service.perform_task_action(
            tenant_id="tenant-alpha",
            workflow_task_id=task_id,
            action=WorkflowTaskAction.APPROVE,
            principal=authorized_chair,
            idempotency_key=raw_key,
            variables={"decision": "approved"},
        )

        # Verify audit details contains key_digest, NEVER raw_key
        call_args = mock_audit_repo.append.call_args[0][0]
        details = call_args.details
        assert raw_key not in str(details)
        assert details.get("key_digest") == expected_digest

        # Verify outbound engine call does NOT contain raw_key
        engine_call_vars = mock_adapter.complete_user_task.call_args[1]["variables"]
        assert raw_key not in str(engine_call_vars)
        assert engine_call_vars.get("_key_digest") == expected_digest


@pytest.mark.asyncio
async def test_case_g_audit_and_outbox_count_unchanged_on_replay(
    fake_uow_factory, mock_adapter, authorized_chair
):
    """Case G: audit/outbox count unchanged on replay."""
    service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        variable_policy=WorkflowVariablePolicy(allowed_keys=frozenset({"decision"})),
    )

    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    completed_task = _make_pending_task(task_id, inst_id)
    completed_task.status = WorkflowTaskStatus.COMPLETED.value
    completed_task.action = WorkflowTaskAction.APPROVE.value

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.workflow.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = completed_task
        mock_task_repo_cls.return_value = mock_task_repo

        identity = create_idempotency_identity(
            tenant_id="tenant-alpha",
            operation_name="workflow.task.action",
            raw_key="key-exact-replay",
        )
        fp = RequestFingerprint("e" * 64)
        mock_idem_repo = AsyncMock()
        mock_idem_repo.acquire_or_observe.return_value = IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )
        mock_idem_repo_cls.return_value = mock_idem_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        mock_outbox_repo = AsyncMock()
        mock_outbox_repo_cls.return_value = mock_outbox_repo

        # Call replay
        res = await service.perform_task_action(
            tenant_id="tenant-alpha",
            workflow_task_id=task_id,
            action=WorkflowTaskAction.APPROVE,
            principal=authorized_chair,
            idempotency_key="key-exact-replay",
            variables={"decision": "approved"},
        )

        assert res.status == WorkflowTaskStatus.COMPLETED
        # Zero audit records appended
        mock_audit_repo.append.assert_not_awaited()
        # Zero outbox records enqueued
        mock_outbox_repo.enqueue.assert_not_awaited()
