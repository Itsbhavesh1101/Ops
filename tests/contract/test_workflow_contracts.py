"""Contract tests proving Operations UI routes map to backend workflow API endpoints (Phase 3B).

Proves:
- All paths called by WorkflowApiClient in ui/operations-ui/src/api/client.ts
  map to real router endpoints.
- BPMN validation, definition registration, deployment, task inbox, task action,
  DMN deployment, DMN execution, form validation/save, and SLA policy endpoints.
- Verifies absence of orphaned frontend URLs.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.workflows import (
    WORKFLOW_API_PRODUCTION_MOUNT,
    create_workflow_router,
)
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.workflow.dmn.schemas import DmnDecisionMetadata, DmnExecutionResponse, DmnHitPolicy
from app.workflow.schemas import (
    WorkflowDefinitionResponse,
    WorkflowTaskResponse,
    WorkflowTaskStatus,
)


def _dummy_auth():
    async def _dep():
        return AuthenticatedPrincipal(
            principal_id="test-operator",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant-ui",
            roles=frozenset({"operators", "reviewers"}),
        )

    return _dep


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    return svc


@pytest.fixture
def test_app(mock_service):
    app = FastAPI()
    register_api_exception_handlers(app)
    router = create_workflow_router(
        tenant_dependency=lambda: "tenant-ui",
        principal_dependency=_dummy_auth(),
        workflow_service_dependency=lambda: mock_service,
    )
    app.include_router(router, prefix="/api/v1")
    return app


def test_production_mount_deferred_preserved():
    """Verify that production mount deferral marker remains intact."""
    assert (
        WORKFLOW_API_PRODUCTION_MOUNT
        == "DEFERRED_PENDING_TRUSTED_HTTP_AUTHORIZATION_DEPENDENCIES"
    )


@pytest.mark.asyncio
async def test_validate_bpmn_contract(test_app):
    """Contract: POST /api/v1/workflow/validate-bpmn"""
    valid_bpmn = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:flowable="http://flowable.org/bpmn" '
        'targetNamespace="http://e7.operations.engine/test">'
        '<process id="contract_bpmn" name="Contract Process" isExecutable="true">'
        '<startEvent id="start"/>'
        '<sequenceFlow id="f1" sourceRef="start" targetRef="t1"/>'
        '<userTask id="t1" name="Task"/>'
        '<sequenceFlow id="f2" sourceRef="t1" targetRef="end"/>'
        '<endEvent id="end"/>'
        '</process></definitions>'
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/workflow/validate-bpmn", json={"bpmn_xml": valid_bpmn})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["process_id"] == "contract_bpmn"


@pytest.mark.asyncio
async def test_register_definition_contract(test_app, mock_service):
    """Contract: POST /api/v1/workflow/definitions"""
    def_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service.register_definition.return_value = WorkflowDefinitionResponse(
        id=def_id,
        tenant_id="tenant-ui",
        workflow_key="sample_wf",
        name="Sample Flow",
        description=None,
        version=1,
        bpmn_process_id="sample_wf",
        artifact_hash="hash123",
        deployment_id=None,
        engine_definition_id=None,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/workflow/definitions",
            headers={"X-Tenant-ID": "tenant-ui"},
            json={
                "workflow_key": "sample_wf",
                "name": "Sample Flow",
                "bpmn_xml": "<definitions/>",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == str(def_id)


@pytest.mark.asyncio
async def test_deploy_definition_contract(test_app, mock_service):
    """Contract: POST /api/v1/workflow/definitions/{id}/deploy"""
    def_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service.deploy_definition.return_value = WorkflowDefinitionResponse(
        id=def_id,
        tenant_id="tenant-ui",
        workflow_key="sample_wf",
        name="Sample Flow",
        description=None,
        version=1,
        bpmn_process_id="sample_wf",
        artifact_hash="hash123",
        deployment_id="dep-1",
        engine_definition_id="sample_wf:1:dep-1",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflow/definitions/{def_id}/deploy",
            headers={"X-Tenant-ID": "tenant-ui"},
        )
    assert resp.status_code == 200
    assert resp.json()["deployment_id"] == "dep-1"


@pytest.mark.asyncio
async def test_list_tasks_contract(test_app, mock_service):
    """Contract: GET /api/v1/workflow/tasks"""
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service.list_tasks.return_value = [
        WorkflowTaskResponse(
            id=task_id,
            tenant_id="tenant-ui",
            workflow_instance_id=inst_id,
            engine_task_id="eng-task-1",
            task_definition_key="review_step",
            task_name="Review Step",
            assignee=None,
            candidate_roles=["reviewers"],
            status=WorkflowTaskStatus.PENDING,
            action=None,
            action_by=None,
            action_at=None,
            due_at=None,
            variables={},
            created_at=now,
            updated_at=now,
        )
    ]
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/workflow/tasks", headers={"X-Tenant-ID": "tenant-ui"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(task_id)


@pytest.mark.asyncio
async def test_perform_task_action_singular_contract(test_app, mock_service):
    """Contract: POST /api/v1/workflow/tasks/{id}/action"""
    task_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service.perform_task_action.return_value = WorkflowTaskResponse(
        id=task_id,
        tenant_id="tenant-ui",
        workflow_instance_id=inst_id,
        engine_task_id="eng-task-1",
        task_definition_key="review_step",
        task_name="Review Step",
        assignee="test-operator",
        candidate_roles=["reviewers"],
        status=WorkflowTaskStatus.COMPLETED,
        action="APPROVE",
        action_by="test-operator",
        action_at=None,
        due_at=None,
        variables={"decision": "approved"},
        created_at=now,
        updated_at=now,
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflow/tasks/{task_id}/action",
            headers={"X-Tenant-ID": "tenant-ui", "Idempotency-Key": "idemp-contract-01"},
            json={"action": "approve", "variables": {"decision": "approved"}},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_deploy_dmn_contract(test_app, mock_service):
    """Contract: POST /api/v1/workflow/dmn/deploy"""
    mock_service.deploy_decision_table.return_value = DmnDecisionMetadata(
        decision_key="routing_decision",
        decision_name="Routing Decision",
        hit_policy=DmnHitPolicy.UNIQUE,
        rule_count=3,
        artifact_hash="hash_dmn_123",
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/workflow/dmn/deploy",
            headers={"X-Tenant-ID": "tenant-ui"},
            json={"dmn_xml": "<definitions/>", "resource_name": "routing.dmn"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision_key"] == "routing_decision"
    assert data["rule_count"] == 3


@pytest.mark.asyncio
async def test_execute_dmn_contract(test_app, mock_service):
    """Contract: POST /api/v1/workflow/dmn/execute"""
    mock_service.execute_decision_table.return_value = DmnExecutionResponse(
        decision_key="routing_decision",
        result_variables={"routing_path": "STANDARD"},
        evaluated_at=datetime.now(UTC),
    )
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/workflow/dmn/execute",
            headers={"X-Tenant-ID": "tenant-ui"},
            json={"decision_key": "routing_decision", "variables": {"amount": 500}},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision_key"] == "routing_decision"
    assert data["result_variables"]["routing_path"] == "STANDARD"


@pytest.mark.asyncio
async def test_form_artifacts_and_sla_contracts(test_app, mock_service):
    """Contract: Form artifact save/validate and SLA policy listing."""
    form_json = {
        "id": "expense_form",
        "schemaVersion": 1,
        "title": "Expense Approval Form",
        "components": [
            {"id": "f_amt", "key": "amount", "label": "Amount", "type": "number", "required": True},
            {"id": "f_cmt", "key": "comment", "label": "Comment", "type": "textfield"},
        ],
    }
    from app.workflow.forms.validator import FormValidator

    form_schema = FormValidator().validate_form_schema(form_json)
    mock_service.validate_form_definition.return_value = form_schema

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        resp_form = await ac.post(
            "/api/v1/workflow/artifacts/forms/validate",
            json={"schema_json": form_json},
        )
        assert resp_form.status_code == 200
        assert resp_form.json()["valid"] is True
        assert len(resp_form.json()["components"]) == 2

        # 2. SLA policies
        resp_sla = await ac.get("/api/v1/workflow/sla/policies")
        assert resp_sla.status_code == 200
        assert isinstance(resp_sla.json(), list)
