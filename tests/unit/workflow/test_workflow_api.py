"""Unit tests for Workflow API routes and contract validation.

Verifies:
- Router factory creation with dependency injection
- Workflow API production mount deferral marker
- Definition registration and deployment endpoints
- Instance creation and retrieval endpoints
- Task listing and action endpoints
- Mandatory Idempotency-Key header on task actions
- Conflict error mapping (409) for idempotency conflict
- Normalization of error codes (404, 403, 422) using canonical error envelope
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
from app.core.idempotency.executor import IdempotencyConflictError
from app.workflow.authorization import (
    WorkflowTaskUnauthorizedError,
)
from app.workflow.schemas import (
    WorkflowDefinitionResponse,
    WorkflowInstanceResponse,
    WorkflowInstanceStatus,
    WorkflowTaskAction,
    WorkflowTaskResponse,
    WorkflowTaskStatus,
)
from app.workflow.service import (
    WorkflowAdapterOperationUnsupportedError,
    WorkflowInstanceNotFoundError,
)


def _dummy_auth_dependency():
    async def _dep():
        return AuthenticatedPrincipal(
            principal_id="test-user",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id="tenant-alpha",
            roles=frozenset({"analyst", "manager", "tenant_admin"}),
        )

    return _dep


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def app(mock_service):
    app = FastAPI()
    register_api_exception_handlers(app)
    router = create_workflow_router(
        tenant_dependency=lambda: "tenant-alpha",
        principal_dependency=_dummy_auth_dependency(),
        workflow_service_dependency=lambda: mock_service,
    )
    app.include_router(router, prefix="/api/v1")
    return app


def test_production_mount_status_deferred():
    assert (
        WORKFLOW_API_PRODUCTION_MOUNT == "DEFERRED_PENDING_TRUSTED_HTTP_AUTHORIZATION_DEPENDENCIES"
    )


@pytest.mark.asyncio
async def test_register_definition_endpoint(app, mock_service):
    def_id = uuid.uuid4()
    mock_service.register_definition.return_value = WorkflowDefinitionResponse(
        id=def_id,
        tenant_id="tenant-alpha",
        workflow_key="sample_key",
        name="Sample Definition",
        description="Sample",
        version=1,
        bpmn_process_id="sample_process",
        artifact_hash="abc",
        deployment_id=None,
        engine_definition_id=None,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/workflows/definitions",
            json={
                "workflow_key": "sample_key",
                "name": "Sample Definition",
                "description": "Sample",
                "bpmn_xml": "<definitions id='test'/>",
            },
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(def_id)
    assert data["workflow_key"] == "sample_key"


@pytest.mark.asyncio
async def test_deploy_definition_endpoint(app, mock_service):
    def_id = uuid.uuid4()
    mock_service.deploy_definition.return_value = WorkflowDefinitionResponse(
        id=def_id,
        tenant_id="tenant-alpha",
        workflow_key="sample_key",
        name="Sample Definition",
        description="Sample",
        version=1,
        bpmn_process_id="sample_process",
        artifact_hash="abc",
        deployment_id="flowable-dep-1",
        engine_definition_id="sample_process:1:flowable-dep-1",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/workflows/definitions/{def_id}/deploy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deployment_id"] == "flowable-dep-1"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_start_instance_endpoint(app, mock_service):
    inst_id = uuid.uuid4()
    def_id = uuid.uuid4()
    mock_service.start_instance.return_value = WorkflowInstanceResponse(
        id=inst_id,
        tenant_id="tenant-alpha",
        definition_id=def_id,
        workflow_key="sample_key",
        version=1,
        business_reference="ref-999",
        correlation_id=None,
        engine_instance_id="proc-101",
        status=WorkflowInstanceStatus.RUNNING,
        input_variables={"priority": "high"},
        started_by="test-user",
        started_at=datetime.now(UTC),
        completed_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/workflows/instances",
            json={
                "workflow_key": "sample_key",
                "business_reference": "ref-999",
                "variables": {"priority": "high"},
            },
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(inst_id)
    assert data["engine_instance_id"] == "proc-101"


@pytest.mark.asyncio
async def test_task_action_missing_idempotency_key_returns_422(app):
    task_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Request WITHOUT Idempotency-Key header
        resp = await ac.post(
            f"/api/v1/workflows/tasks/{task_id}/actions",
            json={
                "action": "APPROVE",
                "variables": {"decision": "approved"},
            },
        )
    # FastApi header validation rejects missing required header with 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_task_action_success_with_idempotency_key(app, mock_service):
    task_id = uuid.uuid4()
    mock_service.perform_task_action.return_value = WorkflowTaskResponse(
        id=task_id,
        workflow_instance_id=uuid.uuid4(),
        tenant_id="tenant-alpha",
        engine_task_id="task-123",
        task_definition_key="chair_review",
        task_name="Chair Review",
        assignee="test-user",
        candidate_roles=[],
        status=WorkflowTaskStatus.COMPLETED,
        action=WorkflowTaskAction.APPROVE.value,
        action_by="test-user",
        action_at=datetime.now(UTC),
        due_at=None,
        variables={"decision": "approved"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflows/tasks/{task_id}/actions",
            headers={"Idempotency-Key": "client-key-abc"},
            json={
                "action": "APPROVE",
                "variables": {"decision": "approved"},
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(task_id)
    assert data["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_task_action_idempotency_conflict_returns_409(app, mock_service):
    task_id = uuid.uuid4()
    from app.core.idempotency.contracts import (
        RequestFingerprint,
        create_idempotency_identity,
    )

    identity = create_idempotency_identity(
        tenant_id="tenant-alpha",
        operation_name="workflow.task.action",
        raw_key="reused-key-different-payload",
    )
    mock_service.perform_task_action.side_effect = IdempotencyConflictError(
        identity=identity,
        request_fingerprint=RequestFingerprint("a" * 64),
        existing_fingerprint=RequestFingerprint("b" * 64),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflows/tasks/{task_id}/actions",
            headers={"Idempotency-Key": "reused-key-different-payload"},
            json={
                "action": "REJECT",
                "variables": {},
            },
        )
    assert resp.status_code == 409
    err_body = resp.json()["error"]
    assert err_body["code"] == "idempotency.conflict"


@pytest.mark.asyncio
async def test_task_action_unauthorized_error(app, mock_service):
    task_id = uuid.uuid4()
    mock_service.perform_task_action.side_effect = WorkflowTaskUnauthorizedError(
        task_id=str(task_id),
        principal_id="test-user",
        reason="Actor does not have role approver",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflows/tasks/{task_id}/actions",
            headers={"Idempotency-Key": "key-unauth-1"},
            json={
                "action": "APPROVE",
                "variables": {},
            },
        )
    assert resp.status_code == 403
    err_body = resp.json()["error"]
    assert err_body["code"] == "workflow.task_unauthorized"
    assert "Actor does not have role approver" in err_body["message"]


@pytest.mark.asyncio
async def test_not_found_error_mapped_to_404(app, mock_service):
    missing_id = uuid.uuid4()
    mock_service.get_instance.side_effect = WorkflowInstanceNotFoundError(
        f"Workflow instance '{missing_id}' not found."
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/workflows/instances/{missing_id}")
    assert resp.status_code == 404
    err_body = resp.json()["error"]
    assert err_body["code"] == "workflow.instance_not_found"


@pytest.mark.asyncio
async def test_task_action_unsupported_adapter_error(app, mock_service):
    task_id = uuid.uuid4()
    msg = (
        "Workflow adapter 'DummyAdapter' does not implement 'claim_user_task' "
        "required for action 'CLAIM'."
    )
    mock_service.perform_task_action.side_effect = WorkflowAdapterOperationUnsupportedError(msg)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workflows/tasks/{task_id}/actions",
            headers={"Idempotency-Key": "key-unsupported-1"},
            json={
                "action": "CLAIM",
                "variables": {},
            },
        )
    assert resp.status_code == 501
    err_body = resp.json()["error"]
    assert err_body["code"] == "workflow.action_unsupported"
    assert "does not implement 'claim_user_task'" in err_body["message"]
