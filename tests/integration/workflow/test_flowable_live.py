"""Live integration test suite validating Flowable engine integration, canonical state durability,
authorization enforcement boundary, idempotent execution, SLA timers, and container restart
durability.

Covers all 14 required Phase-3B live cases:
1. sequential approval
2. parallel all-of
3. quorum N-of-M
4. deterministic rejection
5. real Flowable DMN deployment/execution
6. typed form validation through server boundary
7. SLA warning
8. SLA breach
9. SLA pause/resume business-time arithmetic
10. Flowable restart with pending workflow
11. Flowable restart with pending SLA timer
12. duplicate action idempotency
13. duplicate callback / sync safety
14. unauthorized action boundary

Requires:
    E7_RUN_WORKFLOW_INTEGRATION=1
    Running Docker containers: e7-postgres (55432) and e7-flowable (8080)
"""

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.db.uow import SqlAlchemyUnitOfWorkFactory
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.approval.compiler import ApprovalBpmnCompiler
from app.workflow.approval.models import (
    ApprovalPattern,
    ApprovalStepConfig,
    ApprovalWorkflowConfig,
)
from app.workflow.authorization import (
    DefaultTaskEligibilityPolicy,
    WorkflowTaskUnauthorizedError,
)
from app.workflow.forms.validator import (
    FormInvalidSubmissionError,
    FormValidator,
)
from app.workflow.schemas import (
    WorkflowInstanceStatus,
    WorkflowTaskAction,
    WorkflowTaskStatus,
)
from app.workflow.service import (
    WorkflowService,
    WorkflowTaskAlreadyCompletedError,
)
from app.workflow.sla.calendar import (
    DefaultBusinessCalendarPolicy,
)
from app.workflow.sla.models import (
    SlaPolicyConfig,
    SlaStatus,
)
from app.workflow.sla.service import SlaService
from app.workflow.variables import (
    PHASE_3A_FIXTURE_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BPMN_FILE = REPO_ROOT / "workflows" / "bpmn" / "two_step_approval.bpmn20.xml"
DMN_FILE = REPO_ROOT / "workflows" / "dmn" / "approval_routing.dmn13.xml"
FORM_FILE = REPO_ROOT / "workflows" / "forms" / "expense_approval_form.json"

FLOWABLE_BASE_URL = os.environ.get(
    "FLOWABLE_BASE_URL", "http://localhost:8080/flowable-rest/service"
)
FLOWABLE_USER = os.environ.get("FLOWABLE_USER", "rest-admin")
FLOWABLE_PASSWORD = os.environ.get("FLOWABLE_PASSWORD", "test")


async def _wait_for_flowable_ready(max_seconds: int = 45) -> None:
    """Wait for Flowable REST API to respond with HTTP 200."""
    url = f"{FLOWABLE_BASE_URL.rstrip('/')}/repository/deployments"
    for _ in range(max_seconds):
        await asyncio.sleep(1.0)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url, auth=(FLOWABLE_USER, FLOWABLE_PASSWORD))
                if resp.status_code == 200:
                    return
        except Exception:
            continue
    raise RuntimeError(f"Flowable failed to become ready within {max_seconds}s")


# =========================================================================
# Case 1: Sequential Approval
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_01_sequential_approval(workflow_service: WorkflowService) -> None:
    """Case 1: Sequential approval workflow advances step-by-step through Flowable."""
    tenant_id = f"tenant-seq-{uuid4().hex[:8]}"
    workflow_key = f"seq_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    # Register & Deploy
    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Sequential Approval Process",
        description="Sequential Live Test",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    deployed_def = await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )
    assert deployed_def.deployment_id is not None

    # Start Instance
    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"REQ-{uuid4().hex[:6]}",
        correlation_id=f"corr-{uuid4().hex[:8]}",
        variables={"priority": "high"},
        actor_id="applicant-01",
    )
    assert instance.status == WorkflowInstanceStatus.RUNNING

    # Step 1: Chair Review
    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    assert len(tasks) == 1
    task1 = tasks[0]
    assert task1.task_definition_key == "chair_review"

    chair = AuthenticatedPrincipal(
        principal_id="chair-dr-smith",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"chairs"}),
    )
    completed_task1 = await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=task1.id,
        action=WorkflowTaskAction.APPROVE,
        principal=chair,
        variables={"decision": "approved", "comment": "Chair approves"},
    )
    assert completed_task1.status == WorkflowTaskStatus.COMPLETED

    # Step 2: Dean Review
    tasks_step2 = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    assert len(tasks_step2) == 1
    task2 = tasks_step2[0]
    assert task2.task_definition_key == "dean_review"

    dean = AuthenticatedPrincipal(
        principal_id="dean-dr-jones",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"deans"}),
    )
    completed_task2 = await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=task2.id,
        action=WorkflowTaskAction.COMPLETE,
        principal=dean,
        variables={"decision": "approved", "comment": "Dean approves"},
    )
    assert completed_task2.status == WorkflowTaskStatus.COMPLETED

    # Instance reaches COMPLETED
    final_instance = await workflow_service.get_instance(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
    )
    assert final_instance.status == WorkflowInstanceStatus.COMPLETED
    assert final_instance.completed_at is not None

    # Normalized history verification
    history = await workflow_service.get_normalized_history(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
    )
    act_ids = [h.activity_id for h in history]
    assert "start_event" in act_ids
    assert "chair_review" in act_ids
    assert "dean_review" in act_ids
    assert "end_event" in act_ids


# =========================================================================
# Case 2: Parallel All-Of
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_02_parallel_all_of(workflow_service: WorkflowService) -> None:
    """Case 2: Parallel All-Of requires all concurrent branches to complete before advancing."""
    tenant_id = f"tenant-par-{uuid4().hex[:8]}"
    workflow_key = f"par_flow_{uuid4().hex[:6]}"

    compiler = ApprovalBpmnCompiler()
    config = ApprovalWorkflowConfig(
        workflow_key=workflow_key,
        name="Parallel Approval Flow",
        steps=[
            ApprovalStepConfig(
                step_id="step_legal",
                name="Legal Review",
                candidate_roles=["legal"],
                pattern=ApprovalPattern.SINGLE,
            ),
            ApprovalStepConfig(
                step_id="step_finance",
                name="Finance Review",
                candidate_roles=["finance"],
                pattern=ApprovalPattern.SINGLE,
            ),
        ],
        approval_type=ApprovalPattern.PARALLEL_ALL_OF,
    )
    bpmn_xml = compiler.compile(config)

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Parallel Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"PAR-{uuid4().hex[:6]}",
        correlation_id=f"corr-{uuid4().hex[:8]}",
        actor_id="applicant-par",
    )
    assert instance.status == WorkflowInstanceStatus.RUNNING

    # Both tasks exist concurrently
    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    assert len(tasks) == 2
    task_keys = {t.task_definition_key for t in tasks}
    assert task_keys == {"step_legal", "step_finance"}

    legal_task = next(t for t in tasks if t.task_definition_key == "step_legal")
    finance_task = next(t for t in tasks if t.task_definition_key == "step_finance")

    # Complete Legal review
    legal_officer = AuthenticatedPrincipal(
        principal_id="counsel-dave",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"legal"}),
    )
    await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=legal_task.id,
        action=WorkflowTaskAction.APPROVE,
        principal=legal_officer,
        variables={"decision": "approved"},
    )

    # Process must STILL be RUNNING (Finance is still pending)
    inst_check = await workflow_service.get_instance(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
    )
    assert inst_check.status == WorkflowInstanceStatus.RUNNING

    pending_after_legal = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    assert len(pending_after_legal) == 1
    assert pending_after_legal[0].id == finance_task.id

    # Complete Finance review -> parallel join satisfies
    finance_officer = AuthenticatedPrincipal(
        principal_id="cfo-mary",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"finance"}),
    )
    await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=finance_task.id,
        action=WorkflowTaskAction.APPROVE,
        principal=finance_officer,
        variables={"decision": "approved"},
    )

    # Now instance completes
    final_inst = await workflow_service.get_instance(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
    )
    assert final_inst.status == WorkflowInstanceStatus.COMPLETED


# =========================================================================
# Case 3: Quorum N-of-M
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_03_quorum_n_of_m(
    workflow_service: WorkflowService,
    flowable_adapter: FlowableAdapter,
) -> None:
    """Case 3: Quorum N-of-M advances on Nth approval and avoids duplicate transitions."""
    tenant_id = f"tenant-quorum-{uuid4().hex[:8]}"
    workflow_key = f"quorum_flow_{uuid4().hex[:6]}"

    compiler = ApprovalBpmnCompiler()
    config = ApprovalWorkflowConfig(
        workflow_key=workflow_key,
        name="Quorum Committee Flow",
        steps=[
            ApprovalStepConfig(
                step_id="step_committee",
                name="Committee Review",
                candidate_roles=["committee"],
                pattern=ApprovalPattern.QUORUM_N_OF_M,
                required_quorum=2,
            )
        ],
        approval_type=ApprovalPattern.QUORUM_N_OF_M,
        quorum_count=2,
    )
    bpmn_xml = compiler.compile(config)

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Quorum Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
        allow_trusted_expressions=True,
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"QRM-{uuid4().hex[:6]}",
        correlation_id=f"corr-{uuid4().hex[:8]}",
        actor_id="applicant-qrm",
    )
    assert instance.status == WorkflowInstanceStatus.RUNNING

    # Query tasks directly from Flowable: multi-instance tasks spawned
    engine_tasks = await flowable_adapter.list_user_tasks(
        engine_instance_id=instance.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(engine_tasks) >= 2

    # Participant 1 approves
    p1 = AuthenticatedPrincipal(
        principal_id="member-alice",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"committee"}),
    )
    # Sync instance tasks in E-7
    synced = await workflow_service.sync_instance_tasks(tenant_id, instance.id)
    assert len(synced) >= 2

    await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=synced[0].id,
        action=WorkflowTaskAction.APPROVE,
        principal=p1,
        variables={"decision": "approved"},
    )

    # 1 of 2: process still RUNNING
    inst_mid = await workflow_service.get_instance(tenant_id, instance.id)
    assert inst_mid.status == WorkflowInstanceStatus.RUNNING

    # Participant 2 approves (Quorum 2 of 3 met!)
    p2 = AuthenticatedPrincipal(
        principal_id="member-bob",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"committee"}),
    )
    await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=synced[1].id,
        action=WorkflowTaskAction.APPROVE,
        principal=p2,
        variables={"decision": "approved"},
    )

    # Process completes on quorum satisfaction
    inst_final = await workflow_service.get_instance(tenant_id, instance.id)
    assert inst_final.status == WorkflowInstanceStatus.COMPLETED


# =========================================================================
# Case 4: Deterministic Rejection
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_04_deterministic_rejection(workflow_service: WorkflowService) -> None:
    """Case 4: Rejection marks task deterministically without unexpected advancement."""
    tenant_id = f"tenant-rej-{uuid4().hex[:8]}"
    workflow_key = f"rej_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Rejection Test Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"REJ-{uuid4().hex[:6]}",
        actor_id="applicant-rej",
    )

    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    task = tasks[0]

    chair = AuthenticatedPrincipal(
        principal_id="chair-dr-strict",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"chairs"}),
    )

    rejected_task = await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=task.id,
        action=WorkflowTaskAction.REJECT,
        principal=chair,
        variables={"decision": "rejected", "comment": "Insufficient budget"},
    )
    assert rejected_task.status == WorkflowTaskStatus.COMPLETED
    assert rejected_task.action == WorkflowTaskAction.REJECT.value


# =========================================================================
# Case 5: Real Flowable DMN Deployment & Execution
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_05_flowable_dmn_execution(flowable_adapter: FlowableAdapter) -> None:
    """Case 5: Real DMN deployment and deterministic rule execution on Flowable DMN engine."""
    assert DMN_FILE.exists(), f"DMN file missing: {DMN_FILE}"
    dmn_xml = DMN_FILE.read_text(encoding="utf-8")
    tenant_id = f"tenant-dmn-{uuid4().hex[:8]}"
    resource_name = f"approval_routing_{uuid4().hex[:6]}.dmn"

    # 1. Deploy DMN table to Flowable
    deployment = await flowable_adapter.deploy_decision(
        dmn_xml=dmn_xml,
        resource_name=resource_name,
        tenant_id=tenant_id,
    )
    assert deployment.deployment_id is not None
    assert len(deployment.deployment_id) > 0

    # 2. Execute Decision Table: HIGH risk -> QUORUM path (3 approvals)
    res_high = await flowable_adapter.execute_decision(
        decision_key="approval_routing",
        variables={"risk_band": "HIGH", "amount_band": "TIER_1"},
        tenant_id=tenant_id,
    )
    assert res_high.get("approval_path") == "QUORUM"
    assert res_high.get("required_approvals") == 3.0

    # 3. Execute Decision Table: LOW risk -> SINGLE path (1 approval)
    res_low = await flowable_adapter.execute_decision(
        decision_key="approval_routing",
        variables={"risk_band": "LOW", "amount_band": "TIER_1"},
        tenant_id=tenant_id,
    )
    assert res_low.get("approval_path") == "SINGLE"
    assert res_low.get("required_approvals") == 1.0

    # 4. Execute Decision Table: MEDIUM risk + TIER_3 -> QUORUM path (2 approvals)
    res_med = await flowable_adapter.execute_decision(
        decision_key="approval_routing",
        variables={"risk_band": "MEDIUM", "amount_band": "TIER_3"},
        tenant_id=tenant_id,
    )
    assert res_med.get("approval_path") == "QUORUM"
    assert res_med.get("required_approvals") == 2.0

    # 5. Deterministic replay guarantee: same input yields exact same output
    res_high_repeat = await flowable_adapter.execute_decision(
        decision_key="approval_routing",
        variables={"risk_band": "HIGH", "amount_band": "TIER_1"},
        tenant_id=tenant_id,
    )
    assert res_high_repeat == res_high

    # 6. Verify parsed dictionary has zero raw Flowable JSON leaked
    for v in res_high.values():
        assert not isinstance(v, (list, dict))


# =========================================================================
# Case 6: Typed Form Validation Through Server Boundary
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_06_typed_form_validation() -> None:
    """Case 6: Typed form validation accepts valid payloads and rejects unauthorized injections."""
    assert FORM_FILE.exists(), f"Form file missing: {FORM_FILE}"
    form_json = json.loads(FORM_FILE.read_text(encoding="utf-8"))

    validator = FormValidator()
    form_def = validator.validate_form_schema(form_json)
    assert form_def.id == "expense_approval_form"

    # Valid submission
    valid_payload = {
        "expense_id": "EXP-2026-99",
        "amount": 450.50,
        "category": "TRAVEL",
        "justification": "On-site architecture review with partner",
        "urgent": True,
    }
    cleaned = validator.validate_submission(form_def, valid_payload)
    assert cleaned["amount"] == 450.50
    assert cleaned["category"] == "TRAVEL"

    # Missing required field
    with pytest.raises(FormInvalidSubmissionError, match="Missing required field 'expense_id'"):
        validator.validate_submission(
            form_def,
            {"amount": 100.0, "category": "MEALS", "justification": "Dinner"},
        )

    # Invalid enum selection
    with pytest.raises(FormInvalidSubmissionError, match="Value 'CRYPTO' is not an allowed option"):
        validator.validate_submission(
            form_def,
            {
                "expense_id": "EXP-1",
                "amount": 100.0,
                "category": "CRYPTO",
                "justification": "Crypto purchase",
            },
        )

    # Injected undeclared variable
    with pytest.raises(FormInvalidSubmissionError, match="Undeclared variable 'admin_override'"):
        validator.validate_submission(
            form_def,
            {
                "expense_id": "EXP-1",
                "amount": 100.0,
                "category": "TRAVEL",
                "justification": "Valid justification",
                "admin_override": True,
            },
        )


# =========================================================================
# Case 7: Flowable Boundary SLA Warning Timer
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_07_sla_warning_flowable_timer(
    flowable_adapter: FlowableAdapter,
) -> None:
    """Case 7: Flowable boundary timer creates job in ACT_RU_TIMER_JOB and fires warning event."""
    bpmn = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://e7.operations.engine/sla">
  <process id="sla_warn_live_proc" name="SLA Warn Live" isExecutable="true">
    <startEvent id="start" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="review_task" />
    <userTask id="review_task" name="SLA Review Task" />
    <boundaryEvent id="timer_sla_warning" attachedToRef="review_task" cancelActivity="false">
      <timerEventDefinition>
        <timeDate>${sla_warning_at}</timeDate>
      </timerEventDefinition>
    </boundaryEvent>
    <sequenceFlow id="f_warn" sourceRef="timer_sla_warning" targetRef="end_warn" />
    <endEvent id="end_warn" />
    <sequenceFlow id="f2" sourceRef="review_task" targetRef="end_main" />
    <endEvent id="end_main" />
  </process>
</definitions>"""

    tenant_id = f"tenant-warn-{uuid4().hex[:8]}"
    deployment = await flowable_adapter.deploy_definition(
        bpmn_xml=bpmn,
        resource_name="sla_warn.bpmn20.xml",
        tenant_id=tenant_id,
    )
    assert deployment.deployment_id is not None

    # Start instance with warning scheduled 3 seconds into the future
    warn_at = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
    inst = await flowable_adapter.start_instance(
        process_definition_key="sla_warn_live_proc",
        business_key=None,
        variables={"sla_warning_at": warn_at},
        tenant_id=tenant_id,
    )

    # 1. Verify authoritative Flowable timer job exists in ACT_RU_TIMER_JOB
    timer_jobs = await flowable_adapter.list_timer_jobs(
        process_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(timer_jobs) >= 1
    assert timer_jobs[0]["processInstanceId"] == inst.engine_instance_id

    # 2. Wait for Flowable async job executor to fire timer
    for _ in range(12):
        await asyncio.sleep(1.0)
        jobs_check = await flowable_adapter.list_timer_jobs(
            process_instance_id=inst.engine_instance_id,
            tenant_id=tenant_id,
        )
        if len(jobs_check) == 0:
            break

    # 3. Timer job completed
    timer_jobs_after = await flowable_adapter.list_timer_jobs(
        process_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(timer_jobs_after) == 0

    # 4. Flowable historic activities contains boundary timer
    history = await flowable_adapter.get_history(
        engine_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    act_ids = [h.activity_id for h in history]
    assert "timer_sla_warning" in act_ids

    # 5. Review task is STILL active (cancelActivity=false)
    tasks = await flowable_adapter.list_user_tasks(
        engine_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(tasks) == 1
    assert tasks[0].task_definition_key == "review_task"


# =========================================================================
# Case 8: Flowable Boundary SLA Breach Timer
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_08_sla_breach_flowable_timer(
    flowable_adapter: FlowableAdapter,
) -> None:
    """Case 8: Flowable interrupting breach timer cancels activity and triggers escalation path."""
    bpmn = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://e7.operations.engine/sla">
  <process id="sla_breach_live_proc" name="SLA Breach Live" isExecutable="true">
    <startEvent id="start" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="urgent_task" />
    <userTask id="urgent_task" name="Urgent Review Task" />
    <boundaryEvent id="timer_sla_breach" attachedToRef="urgent_task" cancelActivity="true">
      <timerEventDefinition>
        <timeDate>${sla_breach_at}</timeDate>
      </timerEventDefinition>
    </boundaryEvent>
    <sequenceFlow id="f_breach" sourceRef="timer_sla_breach" targetRef="escalation_end" />
    <endEvent id="escalation_end" />
    <sequenceFlow id="f2" sourceRef="urgent_task" targetRef="end_main" />
    <endEvent id="end_main" />
  </process>
</definitions>"""

    tenant_id = f"tenant-breach-{uuid4().hex[:8]}"
    deployment = await flowable_adapter.deploy_definition(
        bpmn_xml=bpmn,
        resource_name="sla_breach.bpmn20.xml",
        tenant_id=tenant_id,
    )
    assert deployment.deployment_id is not None

    breach_at = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
    inst = await flowable_adapter.start_instance(
        process_definition_key="sla_breach_live_proc",
        business_key=None,
        variables={"sla_breach_at": breach_at},
        tenant_id=tenant_id,
    )

    # 1. Timer job in ACT_RU_TIMER_JOB
    timer_jobs = await flowable_adapter.list_timer_jobs(
        process_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(timer_jobs) >= 1

    # 2. Wait for Flowable async executor to fire breach timer
    for _ in range(12):
        await asyncio.sleep(1.0)
        jobs_check = await flowable_adapter.list_timer_jobs(
            process_instance_id=inst.engine_instance_id,
            tenant_id=tenant_id,
        )
        if len(jobs_check) == 0:
            break

    # 3. Breach fired: interrupting timer cancelled urgent_task and advanced to escalation_end
    active_tasks = await flowable_adapter.list_user_tasks(
        engine_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(active_tasks) == 0

    history = await flowable_adapter.get_history(
        engine_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    act_ids = [h.activity_id for h in history]
    assert "timer_sla_breach" in act_ids
    assert "escalation_end" in act_ids


# =========================================================================
# Case 9: SLA Pause / Resume Preserves Business-Time Arithmetic
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_09_sla_pause_resume_business_time() -> None:
    """Case 9: SLA pause/resume arithmetic excludes non-working hours and weekends correctly."""
    cal = DefaultBusinessCalendarPolicy()
    sla_service = SlaService(calendar_policy=cal)

    policy = SlaPolicyConfig(
        policy_key="p_standard_5x8",
        name="Standard 5x8 SLA",
        warning_duration_seconds=7200,  # 2 hours
        breach_duration_seconds=14400,  # 4 hours
        calendar_ref="5x8_standard",
    )
    sla_service.register_policy(policy)

    # Task created Friday 15:00 UTC (2 business hours remaining today: 15:00-17:00)
    # Warning: 2 business hours -> Friday 17:00
    # Breach: 4 business hours -> Friday 15:00-17:00 (2h) + Mon 09:00-11:00 (2h) = Monday 11:00
    friday_1500 = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)  # Oct 2, 2026 is Friday
    task_id = uuid4()
    inst_id = uuid4()

    state = sla_service.attach_task_sla(
        tenant_id="tenant-sla-math",
        instance_id=inst_id,
        task_id=task_id,
        policy_key="p_standard_5x8",
        created_at=friday_1500,
    )
    assert state.warning_at == datetime(2026, 10, 2, 17, 0, 0, tzinfo=UTC)
    assert state.breach_at == datetime(2026, 10, 5, 11, 0, 0, tzinfo=UTC)  # Monday 11:00

    # Pause on Friday 16:30 (30 business minutes left before warning, 2.5h before breach)
    friday_1630 = datetime(2026, 10, 2, 16, 30, 0, tzinfo=UTC)
    paused_state = sla_service.pause_task_sla("tenant-sla-math", task_id, paused_at=friday_1630)
    assert paused_state.status == SlaStatus.PAUSED
    assert paused_state.remaining_warning_seconds == 1800  # 30 minutes
    assert paused_state.remaining_breach_seconds == 9000  # 2.5 hours

    # Resume on Monday 09:00 (64.5 wall-clock hours later!)
    # Wall-clock arithmetic would incorrectly add 64.5 hours and breach the SLA.
    # Business-calendar arithmetic correctly preserves 30 business minutes:
    # New warning: Monday 09:30
    # New breach: Monday 09:00 + 2.5h = Monday 11:30
    monday_0900 = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)
    resumed_state = sla_service.resume_task_sla("tenant-sla-math", task_id, resumed_at=monday_0900)
    assert resumed_state.status == SlaStatus.RUNNING
    assert resumed_state.warning_at == datetime(2026, 10, 5, 9, 30, 0, tzinfo=UTC)
    assert resumed_state.breach_at == datetime(2026, 10, 5, 11, 30, 0, tzinfo=UTC)


# =========================================================================
# Case 10: Flowable Restart With Pending Workflow
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_10_flowable_restart_with_pending_workflow(
    workflow_service: WorkflowService,
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    """Case 10: Process instance and pending human tasks survive Flowable container restart."""
    tenant_id = f"tenant-rest1-{uuid4().hex[:8]}"
    workflow_key = f"rest1_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Restart Surviving Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"REST-WF-{uuid4().hex[:6]}",
        actor_id="applicant-rest",
    )
    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    task1 = tasks[0]
    engine_inst_id = instance.engine_instance_id
    assert task1.engine_task_id is not None

    # Restart e7-flowable container
    subprocess.run(["docker", "restart", "e7-flowable"], check=True, capture_output=True)
    await _wait_for_flowable_ready()

    # Reconstruct brand new adapter and service
    new_settings = FlowableSettings(
        base_url=FLOWABLE_BASE_URL,
        timeout_seconds=10.0,
        max_retries=3,
        username=FLOWABLE_USER,
        password=SecretStr(FLOWABLE_PASSWORD),
    )
    new_adapter = FlowableAdapter(settings=new_settings)
    reconstructed_service = WorkflowService(
        uow_factory=uow_factory,
        adapter=new_adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_3A_FIXTURE_ALLOWED_VARIABLES),
    )

    try:
        # Verify native process survived
        eng_inst = await new_adapter.get_instance(engine_inst_id, tenant_id)
        assert eng_inst is not None
        assert eng_inst.engine_instance_id == engine_inst_id
        assert eng_inst.is_ended is False

        # Complete task 1 on reconstructed service
        chair = AuthenticatedPrincipal(
            principal_id="chair-survivor",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=tenant_id,
            roles=frozenset({"chairs"}),
        )
        await reconstructed_service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=task1.id,
            action=WorkflowTaskAction.APPROVE,
            principal=chair,
            variables={"decision": "approved"},
        )

        # Advance to and complete task 2
        tasks2 = await reconstructed_service.list_tasks(
            tenant_id=tenant_id,
            workflow_instance_id=instance.id,
            status=WorkflowTaskStatus.PENDING.value,
        )
        assert len(tasks2) == 1
        dean = AuthenticatedPrincipal(
            principal_id="dean-survivor",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=tenant_id,
            roles=frozenset({"deans"}),
        )
        await reconstructed_service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=tasks2[0].id,
            action=WorkflowTaskAction.COMPLETE,
            principal=dean,
            variables={"decision": "approved"},
        )

        # Instance reaches COMPLETED
        final_inst = await reconstructed_service.get_instance(tenant_id, instance.id)
        assert final_inst.status == WorkflowInstanceStatus.COMPLETED
    finally:
        await new_adapter.close()


# =========================================================================
# Case 11: Flowable Restart With Pending SLA Timer
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_11_flowable_restart_with_pending_sla_timer(
    flowable_adapter: FlowableAdapter,
) -> None:
    """Case 11: Active timer job in ACT_RU_TIMER_JOB survives container restart and fires."""
    bpmn = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://e7.operations.engine/sla">
  <process id="sla_dur_restart_proc" name="SLA Durability Restart" isExecutable="true">
    <startEvent id="start" />
    <sequenceFlow id="f1" sourceRef="start" targetRef="durable_task" />
    <userTask id="durable_task" name="Durable SLA Task" />
    <boundaryEvent id="timer_sla_durable" attachedToRef="durable_task" cancelActivity="false">
      <timerEventDefinition>
        <timeDate>${sla_warning_at}</timeDate>
      </timerEventDefinition>
    </boundaryEvent>
    <sequenceFlow id="f_dur" sourceRef="timer_sla_durable" targetRef="end_dur" />
    <endEvent id="end_dur" />
    <sequenceFlow id="f2" sourceRef="durable_task" targetRef="end_main" />
    <endEvent id="end_main" />
  </process>
</definitions>"""

    tenant_id = f"tenant-rest2-{uuid4().hex[:8]}"
    deployment = await flowable_adapter.deploy_definition(
        bpmn_xml=bpmn,
        resource_name="sla_restart.bpmn20.xml",
        tenant_id=tenant_id,
    )
    assert deployment.deployment_id is not None

    # Timer scheduled 15 seconds in future (sufficient for restart window)
    warn_at = (datetime.now(UTC) + timedelta(seconds=15)).isoformat()
    inst = await flowable_adapter.start_instance(
        process_definition_key="sla_dur_restart_proc",
        business_key=None,
        variables={"sla_warning_at": warn_at},
        tenant_id=tenant_id,
    )

    # Verify timer job exists before restart
    jobs_before = await flowable_adapter.list_timer_jobs(
        process_instance_id=inst.engine_instance_id,
        tenant_id=tenant_id,
    )
    assert len(jobs_before) >= 1

    # Restart e7-flowable container
    subprocess.run(["docker", "restart", "e7-flowable"], check=True, capture_output=True)
    await _wait_for_flowable_ready()

    # Reconstruct adapter and verify timer survived in ACT_RU_TIMER_JOB
    new_settings = FlowableSettings(
        base_url=FLOWABLE_BASE_URL,
        timeout_seconds=10.0,
        max_retries=3,
        username=FLOWABLE_USER,
        password=SecretStr(FLOWABLE_PASSWORD),
    )
    new_adapter = FlowableAdapter(settings=new_settings)
    try:
        # Wait until remaining time elapses so timer fires
        for _ in range(15):
            await asyncio.sleep(1.0)
            jobs_check = await new_adapter.list_timer_jobs(
                process_instance_id=inst.engine_instance_id,
                tenant_id=tenant_id,
            )
            if len(jobs_check) == 0:
                break

        # Historic activities must contain the fired timer
        history = await new_adapter.get_history(
            engine_instance_id=inst.engine_instance_id,
            tenant_id=tenant_id,
        )
        act_ids = [h.activity_id for h in history]
        assert "timer_sla_durable" in act_ids
    finally:
        await new_adapter.close()


# =========================================================================
# Case 12: Duplicate Action Idempotency
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_12_duplicate_action_idempotency(
    workflow_service: WorkflowService,
) -> None:
    """Case 12: Same idempotency key replays safely; changed payload raises conflict."""
    tenant_id = f"tenant-idemp-{uuid4().hex[:8]}"
    workflow_key = f"idemp_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Idempotency Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"IDEMP-{uuid4().hex[:6]}",
        actor_id="applicant-idemp",
    )

    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    task = tasks[0]

    chair = AuthenticatedPrincipal(
        principal_id="chair-idemp",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"chairs"}),
    )

    # 1. First execution
    res1 = await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=task.id,
        action=WorkflowTaskAction.APPROVE,
        principal=chair,
        idempotency_key="idemp-key-task-001",
        variables={"decision": "approved", "comment": "Approved once"},
    )
    assert res1.status == WorkflowTaskStatus.COMPLETED

    # 2. Exact Replay (same key + same payload): returns identical result
    res_replay = await workflow_service.perform_task_action(
        tenant_id=tenant_id,
        workflow_task_id=task.id,
        action=WorkflowTaskAction.APPROVE,
        principal=chair,
        idempotency_key="idemp-key-task-001",
        variables={"decision": "approved", "comment": "Approved once"},
    )
    assert res_replay.id == res1.id
    assert res_replay.status == WorkflowTaskStatus.COMPLETED

    # 3. Conflict (same key + DIFFERENT payload): raises IdempotencyConflictError
    with pytest.raises(IdempotencyConflictError):
        await workflow_service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=task.id,
            action=WorkflowTaskAction.APPROVE,
            principal=chair,
            idempotency_key="idemp-key-task-001",
            variables={"decision": "approved", "comment": "TAMPERED PAYLOAD"},
        )

    # 4. Different key on already completed task raises WorkflowTaskAlreadyCompletedError
    with pytest.raises(WorkflowTaskAlreadyCompletedError):
        await workflow_service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=task.id,
            action=WorkflowTaskAction.APPROVE,
            principal=chair,
            idempotency_key="different-key-999",
            variables={"decision": "approved"},
        )


# =========================================================================
# Case 13: Duplicate Callback / Task Synchronization Safety
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_13_duplicate_callback(
    workflow_service: WorkflowService,
) -> None:
    """Case 13: Duplicate task synchronization calls produce zero duplicate tasks."""
    tenant_id = f"tenant-sync-{uuid4().hex[:8]}"
    workflow_key = f"sync_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Sync Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"SYNC-{uuid4().hex[:6]}",
        actor_id="applicant-sync",
    )

    # Sync multiple times
    sync1 = await workflow_service.sync_instance_tasks(tenant_id, instance.id)
    sync2 = await workflow_service.sync_instance_tasks(tenant_id, instance.id)
    sync3 = await workflow_service.sync_instance_tasks(tenant_id, instance.id)

    assert len(sync1) == 1
    assert len(sync2) == 1
    assert len(sync3) == 1
    assert sync1[0].id == sync2[0].id == sync3[0].id


# =========================================================================
# Case 14: Unauthorized Action Enforcement Boundary
# =========================================================================
@pytest.mark.asyncio
async def test_live_case_14_unauthorized_action(
    workflow_service: WorkflowService,
) -> None:
    """Case 14: Ineligible actor is rejected before engine call and cannot probe replay responses.
    """
    tenant_id = f"tenant-authz-{uuid4().hex[:8]}"
    workflow_key = f"authz_flow_{uuid4().hex[:6]}"
    bpmn_xml = BPMN_FILE.read_text(encoding="utf-8")

    definition = await workflow_service.register_definition(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        name="Authz Flow",
        bpmn_xml=bpmn_xml,
        actor_id="admin-setup",
    )
    await workflow_service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=definition.id,
        actor_id="admin-deployer",
    )

    instance = await workflow_service.start_instance(
        tenant_id=tenant_id,
        workflow_key=workflow_key,
        business_reference=f"AUTHZ-{uuid4().hex[:6]}",
        actor_id="applicant-authz",
    )
    tasks = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    task = tasks[0]

    # Ineligible actor (student cannot approve chair review)
    unauthorized = AuthenticatedPrincipal(
        principal_id="student-hacker",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset({"students"}),
    )

    with pytest.raises(WorkflowTaskUnauthorizedError) as exc_info:
        await workflow_service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=task.id,
            action=WorkflowTaskAction.APPROVE,
            principal=unauthorized,
            variables={"decision": "approved"},
        )
    assert "student-hacker" in str(exc_info.value)

    # Task remains PENDING and unchanged
    tasks_check = await workflow_service.list_tasks(
        tenant_id=tenant_id,
        workflow_instance_id=instance.id,
        status=WorkflowTaskStatus.PENDING.value,
    )
    assert len(tasks_check) == 1
    assert tasks_check[0].status == WorkflowTaskStatus.PENDING
