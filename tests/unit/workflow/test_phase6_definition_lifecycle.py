"""Focused regression tests for Phase 6 canonical workflow definition lifecycle and plan.approved.

Ensures:
1. Canonical lifecycle chain:
   register definition -> deploy definition -> canonical active record exists
   -> plan.approved handler resolves the same record
   -> start_instance invokes Flowable exactly once.
2. Idempotency: duplicate plan.approved delivery does NOT create a second Flowable process instance.
3. Cross-tenant isolation: Tenant B cannot start a workflow definition registered for Tenant A
   (fails closed with WorkflowDefinitionNotFoundError, zero engine calls).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.events.schemas import EventCreate, create_event_envelope
from app.workflow.adapters.dto import EngineDeploymentResult, EngineProcessInstance
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.authorization import DefaultTaskEligibilityPolicy
from app.workflow.handlers.plan_approved import PlanApprovedWorkflowHandler
from app.workflow.models import WorkflowDefinitionRecord, WorkflowInstanceRecord
from app.workflow.service import (
    WorkflowDefinitionNotFoundError,
    WorkflowService,
)
from app.workflow.variables import (
    PHASE_6_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
)

SAMPLE_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             targetNamespace="http://e7.operations.irinfotech.com/workflows">
  <process id="exam-operations-v1"
           name="Grade 10 Semester Examination Readiness Process"
           isExecutable="true">
    <startEvent id="start" />
    <userTask id="science_dept_review" name="Science Review" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="science_dept_review" />
    <endEvent id="end" />
    <sequenceFlow id="f2" sourceRef="science_dept_review" targetRef="end" />
  </process>
</definitions>
"""


class InMemoryUnitOfWork:
    """Mock Unit of Work holding session mock."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "InMemoryUnitOfWork":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if exc_type is not None:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_canonical_workflow_definition_lifecycle_and_plan_approved_handler() -> None:
    """Verify register -> deploy -> handler resolves record -> engine invoked once."""
    tenant_id = "tenant-phase6-alpha"
    session = AsyncMock()

    uow = InMemoryUnitOfWork(session)

    def uow_factory() -> InMemoryUnitOfWork:
        return uow

    mock_adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    mock_adapter.deploy_definition.return_value = EngineDeploymentResult(
        deployment_id="flowable-dep-live-001",
        name="exam-operations-v1_v1",
        engine_definition_id="exam-operations-v1:1:flowable-procdef-001",
        engine_definition_key="exam-operations-v1",
        deployed_at=datetime.now(UTC),
    )

    proc_instance = EngineProcessInstance(
        engine_instance_id="flowable-procinst-999",
        engine_definition_id="exam-operations-v1:1:flowable-procdef-001",
        engine_definition_key="exam-operations-v1",
        business_key="live-plan-001",
        is_ended=False,
        is_suspended=False,
        start_time=datetime.now(UTC),
    )
    mock_adapter.start_instance.return_value = proc_instance
    mock_adapter.get_instance.return_value = proc_instance
    mock_adapter.list_user_tasks.return_value = []

    workflow_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=mock_adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_6_ALLOWED_VARIABLES),
    )

    # 1. Register definition
    with patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls, \
         patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls:
        mock_def_repo = AsyncMock()
        mock_def_repo_cls.return_value = mock_def_repo
        mock_def_repo.get_next_version.return_value = 1
        mock_audit_repo_cls.return_value = AsyncMock()

        reg_resp = await workflow_service.register_definition(
            tenant_id=tenant_id,
            workflow_key="exam-operations-v1",
            name="Grade 10 Semester Examination Readiness Process",
            bpmn_xml=SAMPLE_BPMN,
        )
        assert reg_resp.workflow_key == "exam-operations-v1"
        assert reg_resp.version == 1
        assert reg_resp.bpmn_process_id == "exam-operations-v1"

    # 2. Deploy definition
    def_record = WorkflowDefinitionRecord(
        id=reg_resp.id,
        tenant_id=tenant_id,
        workflow_key="exam-operations-v1",
        name="Grade 10 Semester Examination Readiness Process",
        version=1,
        bpmn_process_id="exam-operations-v1",
        artifact_hash=reg_resp.artifact_hash,
        artifact_xml=SAMPLE_BPMN,
        deployment_id="flowable-dep-live-001",
        engine_definition_id="exam-operations-v1:1:flowable-procdef-001",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls, \
         patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls:
        mock_def_repo = AsyncMock()
        mock_def_repo_cls.return_value = mock_def_repo
        mock_def_repo.get_by_id.return_value = def_record
        mock_audit_repo_cls.return_value = AsyncMock()

        deploy_resp = await workflow_service.deploy_definition(
            tenant_id=tenant_id,
            definition_id=reg_resp.id,
        )
        assert deploy_resp.deployment_id == "flowable-dep-live-001"
        assert deploy_resp.engine_definition_id == "exam-operations-v1:1:flowable-procdef-001"
        assert deploy_resp.is_active is True
        mock_adapter.deploy_definition.assert_awaited_once()

    # 3. PlanApprovedWorkflowHandler processes plan.approved
    handler = PlanApprovedWorkflowHandler(workflow_service=workflow_service)
    envelope = create_event_envelope(
        EventCreate(
            event_type="plan.approved",
            tenant_id=tenant_id,
            correlation_id="corr-phase6-test",
            payload={
                "plan_id": "live-plan-001",
                "goal_id": "live-goal-001",
                "approver_principal": "operator-exam-chair",
                "workflow_key": "exam-operations-v1",
                "curriculum_coverage": 100,
            },
        )
    )

    # First delivery: no existing instance in session
    check_mock_empty = MagicMock()
    check_mock_empty.scalar_one_or_none.return_value = None
    session.execute.return_value = check_mock_empty

    with patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls, \
         patch("app.workflow.service.WorkflowInstanceRepository") as mock_inst_repo_cls, \
         patch("app.workflow.service.AuditRepository") as mock_audit_repo_cls:
        mock_audit_repo_cls.return_value = AsyncMock()
        mock_def_repo = AsyncMock()
        mock_def_repo_cls.return_value = mock_def_repo
        mock_def_repo.get_latest_active_by_key.return_value = def_record

        mock_inst_repo = AsyncMock()
        mock_inst_repo_cls.return_value = mock_inst_repo

        inst_record = WorkflowInstanceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            definition_id=reg_resp.id,
            workflow_key="exam-operations-v1",
            version=1,
            business_reference="live-plan-001",
            correlation_id="corr-phase6-test",
            engine_instance_id="flowable-procinst-999",
            status="RUNNING",
            input_variables={},
            started_by="system",
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_inst_repo.get_by_id.return_value = inst_record

        result1 = await handler.handle(session=session, envelope=envelope)
        assert result1 is not None
        assert result1.engine_instance_id == "flowable-procinst-999"
        mock_adapter.start_instance.assert_awaited_once()

    # Second delivery (duplicate): session finds existing instance, returns without calling engine
    check_mock_found = MagicMock()
    check_mock_found.scalar_one_or_none.return_value = inst_record
    session.execute.return_value = check_mock_found

    result2 = await handler.handle(session=session, envelope=envelope)
    assert result2 is not None
    assert result2.engine_instance_id == "flowable-procinst-999"
    # start_instance must STILL be called exactly once (no second invocation)
    mock_adapter.start_instance.assert_awaited_once()


@pytest.mark.asyncio
async def test_cross_tenant_isolation_fails_closed() -> None:
    """Verify Tenant B cannot start a definition registered for Tenant A."""
    tenant_b = "tenant-phase6-beta"
    session = AsyncMock()

    uow = InMemoryUnitOfWork(session)

    def uow_factory() -> InMemoryUnitOfWork:
        return uow

    mock_adapter = AsyncMock(spec=WorkflowAdapterProtocol)
    workflow_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=mock_adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_6_ALLOWED_VARIABLES),
    )

    with patch("app.workflow.service.WorkflowDefinitionRepository") as mock_def_repo_cls:
        mock_def_repo = AsyncMock()
        mock_def_repo_cls.return_value = mock_def_repo
        # Tenant A has record, but Tenant B has None
        mock_def_repo.get_latest_active_by_key.return_value = None

        with pytest.raises(WorkflowDefinitionNotFoundError, match="exam-operations-v1"):
            await workflow_service.start_instance(
                tenant_id=tenant_b,
                workflow_key="exam-operations-v1",
                business_reference="plan-b-001",
            )

        # Engine start_instance is never called
        mock_adapter.start_instance.assert_not_called()
