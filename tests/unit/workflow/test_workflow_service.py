"""Unit tests for WorkflowService application orchestration.

Verifies:
- Registration of workflow definitions with BPMN validation
- Rejection of invalid/insecure BPMN artifacts
- Deployment to workflow engine and canonical state transition
- Instance start and initial task synchronization
- Actor eligibility enforcement (unauthorized actor never reaches engine)
- Idempotent action execution (duplicate action does not re-invoke engine)
- Idempotent service callback handling
- Normalization of engine history
- UnitOfWork transaction management and audit event generation
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.workflow.adapters.dto import (
    EngineDeploymentResult,
    EngineHistoricActivity,
    EngineProcessInstance,
    EngineTask,
)
from app.workflow.adapters.protocol import (
    WorkflowAdapterProtocol,
)
from app.workflow.artifacts.validator import BpmnValidationError
from app.workflow.authorization import (
    DefaultTaskEligibilityPolicy,
    WorkflowTaskUnauthorizedError,
)
from app.workflow.models import (
    WorkflowDefinitionRecord,
    WorkflowInstanceRecord,
    WorkflowTaskMappingRecord,
)
from app.workflow.schemas import (
    WorkflowCallbackPayload,
    WorkflowInstanceStatus,
    WorkflowTaskAction,
    WorkflowTaskStatus,
)
from app.workflow.service import (
    WorkflowService,
)
from app.workflow.variables import (
    PHASE_3A_FIXTURE_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
)

VALID_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             targetNamespace="http://e7.irinfotech.com/workflows">
  <process id="purchase_order_approval" name="Purchase Order Approval" isExecutable="true">
    <startEvent id="start" />
    <userTask id="manager_review" name="Manager Review" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="manager_review" />
    <endEvent id="end" />
    <sequenceFlow id="f2" sourceRef="manager_review" targetRef="end" />
  </process>
</definitions>
"""


class FakeUnitOfWork:
    """Mock Unit of Work providing commit/rollback tracking and an AsyncMock session."""

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
    def _factory():
        return FakeUnitOfWork()

    return _factory


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    return adapter


@pytest.fixture
def workflow_service(fake_uow_factory, mock_adapter):
    return WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_3A_FIXTURE_ALLOWED_VARIABLES),
    )


@pytest.mark.asyncio
async def test_register_definition_success(workflow_service):
    with (
        patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
    ):
        mock_def_repo = AsyncMock()
        mock_def_repo.get_next_version.return_value = 1
        mock_def_repo_cls.return_value = mock_def_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        result = await workflow_service.register_definition(
            tenant_id="tenant-1",
            workflow_key="po_approval",
            name="Purchase Order Approval",
            description="PO Approval Process",
            bpmn_xml=VALID_BPMN,
            actor_id="admin-1",
        )

        assert result.workflow_key == "po_approval"
        assert result.version == 1
        assert result.is_active is True
        assert len(result.artifact_hash) == 64
        mock_audit_repo.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_definition_rejects_insecure_bpmn(workflow_service):
    insecure_bpmn = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE definitions [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">
      <process id="evil" isExecutable="true">
        <startEvent id="s" /><endEvent id="e" />
      </process>
    </definitions>
    """
    with pytest.raises(BpmnValidationError):
        await workflow_service.register_definition(
            tenant_id="tenant-1",
            workflow_key="insecure_def",
            name="Insecure",
            description=None,
            bpmn_xml=insecure_bpmn,
            actor_id="user-1",
        )


@pytest.mark.asyncio
async def test_deploy_definition_success(workflow_service, mock_adapter):
    def_id = uuid.uuid4()

    existing_def = WorkflowDefinitionRecord(
        id=def_id,
        tenant_id="tenant-1",
        workflow_key="po_approval",
        name="Purchase Order Approval",
        version=1,
        bpmn_process_id="purchase_order_approval",
        artifact_hash="abc123hash",
        artifact_xml=VALID_BPMN,
        deployment_id=None,
        engine_definition_id=None,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_adapter.deploy_definition.return_value = EngineDeploymentResult(
        deployment_id="flowable-dep-999",
        name="po_approval_v1",
        engine_definition_id="purchase_order_approval:1:flowable-dep-999",
        engine_definition_key="purchase_order_approval",
    )

    with (
        patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
    ):
        mock_def_repo = AsyncMock()
        mock_def_repo.get_by_id.return_value = existing_def
        mock_def_repo_cls.return_value = mock_def_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        deployed = await workflow_service.deploy_definition(
            tenant_id="tenant-1",
            definition_id=def_id,
            actor_id="admin-1",
        )

        assert deployed.deployment_id == "flowable-dep-999"
        assert deployed.engine_definition_id == "purchase_order_approval:1:flowable-dep-999"
        mock_adapter.deploy_definition.assert_awaited_once()
        mock_audit_repo.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_instance_success(workflow_service, mock_adapter):
    def_id = uuid.uuid4()

    active_def = WorkflowDefinitionRecord(
        id=def_id,
        tenant_id="tenant-1",
        workflow_key="po_approval",
        name="Purchase Order Approval",
        version=1,
        bpmn_process_id="purchase_order_approval",
        artifact_hash="abc123hash",
        artifact_xml=VALID_BPMN,
        deployment_id="flowable-dep-999",
        engine_definition_id="purchase_order_approval:1:flowable-dep-999",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    proc_instance = EngineProcessInstance(
        engine_instance_id="flowable-proc-100",
        engine_definition_id="purchase_order_approval:1:flowable-dep-999",
        engine_definition_key="purchase_order_approval",
        business_key="order-555",
        is_ended=False,
        is_suspended=False,
        start_time=datetime.now(UTC),
    )
    mock_adapter.start_instance.return_value = proc_instance
    mock_adapter.get_instance.return_value = proc_instance
    mock_adapter.list_user_tasks.return_value = [
        EngineTask(
            engine_task_id="engine-task-01",
            task_definition_key="manager_review",
            name="Manager Review",
            engine_instance_id="flowable-proc-100",
            assignee="manager-bob",
            candidate_roles=[],
            due_date=None,
            variables={},
        )
    ]

    with (
        patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls,
        patch("app.workflow.service.WorkflowInstanceRepository") as mock_inst_repo_cls,
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.workflow.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_def_repo = AsyncMock()
        mock_def_repo.get_latest_active_by_key.return_value = active_def
        mock_def_repo_cls.return_value = mock_def_repo

        mock_inst_repo = AsyncMock()
        created_instance = None

        async def fake_inst_create(rec):
            nonlocal created_instance
            created_instance = rec
            return rec

        mock_inst_repo.create.side_effect = fake_inst_create
        mock_inst_repo.get_by_id.side_effect = lambda t, i: created_instance
        mock_inst_repo_cls.return_value = mock_inst_repo

        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_engine_task_id.return_value = None
        mock_task_repo_cls.return_value = mock_task_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        mock_outbox_repo = AsyncMock()
        mock_outbox_repo_cls.return_value = mock_outbox_repo

        instance = await workflow_service.start_instance(
            tenant_id="tenant-1",
            workflow_key="po_approval",
            business_reference="order-555",
            variables={"amount": 1000},
            actor_id="starter",
        )

        assert instance.engine_instance_id == "flowable-proc-100"
        assert instance.status == WorkflowInstanceStatus.RUNNING
        mock_adapter.start_instance.assert_awaited_once()
        mock_audit_repo.append.assert_awaited_once()
        mock_outbox_repo.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_perform_task_action_unauthorized_does_not_call_engine(
    workflow_service, mock_adapter
):
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()

    # Task is assigned to manager-bob, but principal is rogue-actor
    task_mapping = WorkflowTaskMappingRecord(
        id=task_id,
        workflow_instance_id=inst_id,
        tenant_id="tenant-1",
        engine_task_id="engine-task-01",
        task_definition_key="manager_review",
        task_name="Manager Review",
        assignee="manager-bob",
        candidate_roles=[],
        status=WorkflowTaskStatus.PENDING.value,
        action=None,
        action_by=None,
        action_at=None,
        due_at=None,
        variables={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    unauthorized_principal = AuthenticatedPrincipal(
        principal_id="rogue-actor",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-1",
        roles=frozenset({"standard_user"}),
    )

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = task_mapping
        mock_task_repo_cls.return_value = mock_task_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        with pytest.raises(WorkflowTaskUnauthorizedError):
            await workflow_service.perform_task_action(
                tenant_id="tenant-1",
                workflow_task_id=task_id,
                action=WorkflowTaskAction.APPROVE,
                principal=unauthorized_principal,
                idempotency_key="idem-key-123",
                variables={"decision": "approved"},
            )

        # Flowable adapter MUST NOT have been called!
        mock_adapter.complete_user_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_perform_task_action_idempotent_duplicate_does_not_reinvoke_engine(
    workflow_service, mock_adapter
):
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()

    # Task is already completed with action APPROVE
    completed_task = WorkflowTaskMappingRecord(
        id=task_id,
        workflow_instance_id=inst_id,
        tenant_id="tenant-1",
        engine_task_id="engine-task-01",
        task_definition_key="manager_review",
        task_name="Manager Review",
        assignee="manager-bob",
        candidate_roles=[],
        status=WorkflowTaskStatus.COMPLETED.value,
        action=WorkflowTaskAction.APPROVE.value,
        action_by="manager-bob",
        action_at=datetime.now(UTC),
        due_at=None,
        variables={"decision": "approved"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    principal = AuthenticatedPrincipal(
        principal_id="manager-bob",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-1",
        roles=frozenset({"manager"}),
    )

    with (
        patch("app.workflow.service.WorkflowTaskRepository") as mock_task_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
    ):
        mock_task_repo = AsyncMock()
        mock_task_repo.get_by_id.return_value = completed_task
        mock_task_repo_cls.return_value = mock_task_repo

        mock_idem_repo = AsyncMock()
        mock_idem_decision = AsyncMock()
        mock_idem_decision.decision = "EXISTING_MATCH"
        mock_idem_repo.acquire_or_observe.return_value = mock_idem_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        res = await workflow_service.perform_task_action(
            tenant_id="tenant-1",
            workflow_task_id=task_id,
            action=WorkflowTaskAction.APPROVE,
            principal=principal,
            idempotency_key="idem-key-123",
            variables={"decision": "approved"},
        )

        assert res.id == task_id
        assert res.status == WorkflowTaskStatus.COMPLETED
        # Engine was NOT called again on duplicate replay
        mock_adapter.complete_user_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_service_callback_idempotency(workflow_service):
    inst_id = uuid.uuid4()

    instance_rec = WorkflowInstanceRecord(
        id=inst_id,
        tenant_id="tenant-1",
        definition_id=uuid.uuid4(),
        workflow_key="sample_flow",
        version=1,
        engine_instance_id="flowable-proc-100",
        status=WorkflowInstanceStatus.RUNNING.value,
        input_variables={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with (
        patch("app.workflow.service.WorkflowInstanceRepository") as mock_inst_repo_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.workflow.service.IdempotencyRepository") as mock_idem_repo_cls,
    ):
        mock_inst_repo = AsyncMock()
        mock_inst_repo.get_by_id.return_value = instance_rec
        mock_inst_repo_cls.return_value = mock_inst_repo

        mock_audit_repo = AsyncMock()
        mock_audit_repo_cls.return_value = mock_audit_repo

        mock_idem_repo = AsyncMock()
        mock_idem_decision = AsyncMock()
        mock_idem_decision.decision = "ACQUIRED"
        mock_idem_repo.acquire_or_observe.return_value = mock_idem_decision
        mock_idem_repo_cls.return_value = mock_idem_repo

        cb_payload = WorkflowCallbackPayload(
            idempotency_key="cb-idem-1",
            workflow_instance_id=inst_id,
            activity_id="service_erp_sync",
            correlation_id="corr-99",
            result_status="SUCCESS",
            output_variables={"erp_doc_id": "DOC-999"},
        )

        res = await workflow_service.process_callback(
            tenant_id="tenant-1",
            payload=cb_payload,
        )
        assert res["status"] == "ACCEPTED"
        mock_audit_repo.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_history_normalization(workflow_service, mock_adapter):
    inst_id = uuid.uuid4()

    instance_rec = WorkflowInstanceRecord(
        id=inst_id,
        tenant_id="tenant-1",
        definition_id=uuid.uuid4(),
        workflow_key="sample_flow",
        version=1,
        engine_instance_id="flowable-proc-100",
        business_reference="ref-100",
        status=WorkflowInstanceStatus.RUNNING.value,
        input_variables={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_adapter.get_history.return_value = [
        EngineHistoricActivity(
            activity_id="start",
            activity_name="Start Event",
            activity_type="startEvent",
            engine_instance_id="flowable-proc-100",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            assignee=None,
        ),
        EngineHistoricActivity(
            activity_id="manager_review",
            activity_name="Manager Review",
            activity_type="userTask",
            engine_instance_id="flowable-proc-100",
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            assignee="manager-bob",
        ),
    ]

    with patch("app.workflow.service.WorkflowInstanceRepository") as mock_inst_repo_cls:
        mock_inst_repo = AsyncMock()
        mock_inst_repo.get_by_id.return_value = instance_rec
        mock_inst_repo_cls.return_value = mock_inst_repo

        history = await workflow_service.get_normalized_history(
            tenant_id="tenant-1",
            workflow_instance_id=inst_id,
        )

        assert len(history) == 2
        assert history[0].activity_id == "start"
        assert history[1].activity_id == "manager_review"
        assert history[1].assignee == "manager-bob"


def test_workflow_service_without_variable_policy_fails_closed(fake_uow_factory, mock_adapter):
    """Verify that WorkflowService defaults to an empty allowlist (fails closed)
    preventing any silent production default variable leak.
    """
    default_service = WorkflowService(
        uow_factory=fake_uow_factory,
        adapter=mock_adapter,
    )
    # The default variable policy must have an empty allowlist
    assert default_service._variable_policy.allowed_keys == frozenset()
    # Any variable payload filtered by default policy results in empty dict
    assert default_service._variable_policy.filter({"decision": "approved", "comment": "ok"}) == {}


@pytest.mark.asyncio
async def test_evaluate_sla_triggers_isolates_tenants(workflow_service):
    """WorkflowService.evaluate_sla_triggers evaluates ONLY the specified tenant."""
    from app.workflow.sla.models import (
        EscalationAction,
        SlaEscalationRule,
        SlaPolicyConfig,
        SlaStatus,
    )

    policy = SlaPolicyConfig(
        policy_key="test_sla_policy",
        name="Test Policy",
        warning_duration_seconds=10 * 3600,
        breach_duration_seconds=20 * 3600,
        calendar_ref="CALENDAR_24X7",
        warning_escalation=SlaEscalationRule(action=EscalationAction.REMINDER),
        breach_escalation=SlaEscalationRule(action=EscalationAction.ESCALATE),
    )
    workflow_service._sla_service.register_policy(policy)

    base_time = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    task_alpha = uuid.uuid4()
    inst_alpha = uuid.uuid4()
    state_alpha = workflow_service._sla_service.attach_task_sla(
        tenant_id="tenant_alpha",
        instance_id=inst_alpha,
        task_id=task_alpha,
        policy_key="test_sla_policy",
        created_at=base_time,
    )

    task_beta = uuid.uuid4()
    inst_beta = uuid.uuid4()
    state_beta = workflow_service._sla_service.attach_task_sla(
        tenant_id="tenant_beta",
        instance_id=inst_beta,
        task_id=task_beta,
        policy_key="test_sla_policy",
        created_at=base_time,
    )

    with (
        patch("app.workflow.service.OutboxRepository") as mock_outbox_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_cls,
    ):
        mock_outbox = AsyncMock()
        mock_outbox_cls.return_value = mock_outbox
        mock_audit = AsyncMock()
        mock_audit_cls.return_value = mock_audit

        # Evaluate triggers as of +12h (warning threshold reached for both tasks)
        eval_time = base_time + timedelta(hours=12)
        actions = await workflow_service.evaluate_sla_triggers(
            tenant_id="tenant_alpha",
            as_of=eval_time,
        )

        # Tenant alpha is evaluated
        assert len(actions) == 1
        assert actions[0] == f"WARNING:{task_alpha}"
        assert state_alpha.warning_fired_at == eval_time
        assert state_alpha.status == SlaStatus.WARNING

        # Tenant beta is strictly unaffected!
        assert state_beta.warning_fired_at is None
        assert state_beta.status == SlaStatus.RUNNING

        # Outbox & audit records belong ONLY to tenant_alpha
        assert mock_outbox.enqueue.await_count == 1
        outbox_event = mock_outbox.enqueue.await_args[0][0]
        assert outbox_event.tenant_id == "tenant_alpha"

        assert mock_audit.append.await_count == 1
        audit_event = mock_audit.append.await_args[0][0]
        assert audit_event.tenant_id == "tenant_alpha"


@pytest.mark.asyncio
async def test_evaluate_sla_triggers_fails_on_missing_or_whitespace_tenant_id(workflow_service):
    """WorkflowService.evaluate_sla_triggers rejects empty or whitespace tenant_id."""
    with pytest.raises(ValueError, match="tenant_id must not be empty or whitespace-only"):
        await workflow_service.evaluate_sla_triggers(tenant_id="")

    with pytest.raises(ValueError, match="tenant_id must not be empty or whitespace-only"):
        await workflow_service.evaluate_sla_triggers(tenant_id="   ")


@pytest.mark.asyncio
async def test_evaluate_sla_triggers_transaction_rollback_on_commit_failure(mock_adapter):
    """If DB commit fails, SLA mutations are rolled back and retry succeeds."""
    from app.workflow.sla.models import (
        EscalationAction,
        SlaEscalationRule,
        SlaPolicyConfig,
        SlaStatus,
    )

    class FailingUnitOfWork:
        def __init__(self, should_fail: bool):
            self.session = AsyncMock()
            self.should_fail = should_fail
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if exc_type is not None:
                self.rolled_back = True
            return None

        async def commit(self):
            if self.should_fail:
                raise RuntimeError("PostgreSQL connection lost during commit")
            self.committed = True

    failing_uow_state = {"should_fail": True}

    def uow_factory():
        return FailingUnitOfWork(should_fail=failing_uow_state["should_fail"])

    service = WorkflowService(
        uow_factory=uow_factory,
        adapter=mock_adapter,
    )

    policy = SlaPolicyConfig(
        policy_key="rollback_test_policy",
        name="Rollback Policy",
        warning_duration_seconds=5 * 3600,
        breach_duration_seconds=10 * 3600,
        calendar_ref="CALENDAR_24X7",
        warning_escalation=SlaEscalationRule(action=EscalationAction.REMINDER),
        breach_escalation=SlaEscalationRule(action=EscalationAction.ESCALATE),
    )
    service._sla_service.register_policy(policy)

    base_time = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    service._sla_service.attach_task_sla(
        tenant_id="tenant_gamma",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="rollback_test_policy",
        created_at=base_time,
    )

    with (
        patch("app.workflow.service.OutboxRepository") as mock_outbox_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_cls,
    ):
        mock_outbox_cls.return_value = AsyncMock()
        mock_audit_cls.return_value = AsyncMock()
        # 1. Attempt evaluation where commit fails
        eval_time = base_time + timedelta(hours=6)
        with pytest.raises(RuntimeError, match="PostgreSQL connection lost during commit"):
            await service.evaluate_sla_triggers(
                tenant_id="tenant_gamma",
                as_of=eval_time,
            )

        # Verify state was rolled back!
        refetched = service._sla_service.get_task_sla(task_id)
        assert refetched.warning_fired_at is None
        assert refetched.status == SlaStatus.RUNNING

        # 2. Retry with healed DB commit
        failing_uow_state["should_fail"] = False
        actions = await service.evaluate_sla_triggers(
            tenant_id="tenant_gamma",
            as_of=eval_time,
        )
        assert len(actions) == 1
        assert actions[0] == f"WARNING:{task_id}"

        # Now warning_fired_at is committed and state is WARNING
        refetched_success = service._sla_service.get_task_sla(task_id)
        assert refetched_success.warning_fired_at == eval_time
        assert refetched_success.status == SlaStatus.WARNING


@pytest.mark.asyncio
async def test_evaluate_sla_triggers_breach_rollback_on_commit_failure(mock_adapter):
    """If DB commit fails during breach evaluation, breach state rolls back and retries cleanly."""
    from app.workflow.sla.models import (
        EscalationAction,
        SlaEscalationRule,
        SlaPolicyConfig,
        SlaStatus,
    )

    class FailingUnitOfWork:
        def __init__(self, should_fail: bool):
            self.session = AsyncMock()
            self.should_fail = should_fail

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def commit(self):
            if self.should_fail:
                raise RuntimeError("Disk full error on commit")

    failing_uow_state = {"should_fail": True}

    def uow_factory():
        return FailingUnitOfWork(should_fail=failing_uow_state["should_fail"])

    service = WorkflowService(
        uow_factory=uow_factory,
        adapter=mock_adapter,
    )

    policy = SlaPolicyConfig(
        policy_key="breach_rollback_policy",
        name="Breach Rollback Policy",
        warning_duration_seconds=5 * 3600,
        breach_duration_seconds=10 * 3600,
        calendar_ref="CALENDAR_24X7",
        warning_escalation=SlaEscalationRule(action=EscalationAction.REMINDER),
        breach_escalation=SlaEscalationRule(action=EscalationAction.ESCALATE),
    )
    service._sla_service.register_policy(policy)

    base_time = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    state = service._sla_service.attach_task_sla(
        tenant_id="tenant_delta",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="breach_rollback_policy",
        created_at=base_time,
    )
    # Pre-fire warning so task is at breach threshold
    state.warning_fired_at = base_time + timedelta(hours=6)
    state.status = SlaStatus.WARNING

    with (
        patch("app.workflow.service.OutboxRepository") as mock_outbox_cls,
        patch("app.workflow.service.AuditRepository") as mock_audit_cls,
    ):
        mock_outbox_cls.return_value = AsyncMock()
        mock_audit_cls.return_value = AsyncMock()
        # 1. Breach evaluation commit fails
        eval_time = base_time + timedelta(hours=12)
        with pytest.raises(RuntimeError, match="Disk full error on commit"):
            await service.evaluate_sla_triggers(
                tenant_id="tenant_delta",
                as_of=eval_time,
            )

        # State rolled back to pre-breach state
        refetched = service._sla_service.get_task_sla(task_id)
        assert refetched.breach_fired_at is None
        assert refetched.status == SlaStatus.WARNING

        # 2. Retry succeeds
        failing_uow_state["should_fail"] = False
        actions = await service.evaluate_sla_triggers(
            tenant_id="tenant_delta",
            as_of=eval_time,
        )
        assert len(actions) == 1
        assert actions[0] == f"BREACH:{task_id}"

        refetched_success = service._sla_service.get_task_sla(task_id)
        assert refetched_success.breach_fired_at == eval_time
        assert refetched_success.status == SlaStatus.BREACHED
