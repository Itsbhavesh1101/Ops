"""Unit tests for FlowableAdapter using httpx.MockTransport.

Covers:
- Deploy definition success and error handling
- Start process instance
- Get instance (running and completed)
- List user tasks with filtering
- Complete user task with variables
- History retrieval and DTO normalization
- WorkflowResourceNotFoundError (404) and WorkflowEngineResponseError (500)
"""

import httpx
import pytest
from pydantic import SecretStr

from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.adapters.protocol import (
    WorkflowEngineResponseError,
    WorkflowResourceNotFoundError,
)

SETTINGS = FlowableSettings(
    base_url="http://flowable-test:8080/flowable-rest/service",
    username="testuser",
    password=SecretStr("testpass"),
    timeout_seconds=5.0,
    max_retries=1,
)


@pytest.mark.asyncio
async def test_deploy_definition_success():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/repository/deployments" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "id": "dep-123",
                    "name": "sample_workflow",
                    "deploymentTime": "2026-10-02T12:00:00Z",
                    "category": None,
                    "url": "http://flowable-test:8080/repository/deployments/dep-123",
                    "tenantId": "tenant-alpha",
                },
            )
        if request.method == "GET" and "/repository/process-definitions" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "sample_proc:1:dep-123",
                            "key": "sample_proc",
                            "version": 1,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    result = await adapter.deploy_definition(
        bpmn_xml="<definitions/>",
        resource_name="sample_workflow.bpmn20.xml",
        tenant_id="tenant-alpha",
    )
    assert result.deployment_id == "dep-123"
    assert result.name == "sample_workflow"
    assert result.engine_definition_id == "sample_proc:1:dep-123"
    assert result.engine_definition_key == "sample_proc"
    await client.aclose()


@pytest.mark.asyncio
async def test_start_instance_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/runtime/process-instances" in str(request.url)
        return httpx.Response(
            201,
            json={
                "id": "proc-456",
                "processDefinitionId": "sample_proc:1:dep-123",
                "processDefinitionKey": "sample_proc",
                "businessKey": "po-9999",
                "suspended": False,
                "ended": False,
                "tenantId": "tenant-alpha",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    inst = await adapter.start_instance(
        process_definition_key="sample_proc",
        business_key="po-9999",
        variables={"amount": 5000},
        tenant_id="tenant-alpha",
    )
    assert inst.engine_instance_id == "proc-456"
    assert inst.business_key == "po-9999"
    assert inst.is_ended is False
    assert inst.is_suspended is False
    await client.aclose()


@pytest.mark.asyncio
async def test_get_instance_running():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if "/runtime/process-instances/proc-456" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "proc-456",
                    "processDefinitionId": "sample_proc:1:dep-123",
                    "processDefinitionKey": "sample_proc",
                    "businessKey": "po-9999",
                    "suspended": False,
                    "ended": False,
                    "tenantId": "tenant-alpha",
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    inst = await adapter.get_instance("proc-456", tenant_id="tenant-alpha")
    assert inst is not None
    assert inst.engine_instance_id == "proc-456"
    assert inst.is_ended is False
    await client.aclose()


@pytest.mark.asyncio
async def test_get_instance_completed_falls_back_to_history():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/runtime/process-instances/proc-ended" in str(request.url):
            return httpx.Response(404)
        if "/history/historic-process-instances/proc-ended" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "proc-ended",
                    "processDefinitionId": "sample_proc:1:dep-123",
                    "processDefinitionKey": "sample_proc",
                    "businessKey": "po-9999",
                    "endTime": "2026-10-02T12:05:00Z",
                    "tenantId": "tenant-alpha",
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    inst = await adapter.get_instance("proc-ended", tenant_id="tenant-alpha")
    assert inst is not None
    assert inst.engine_instance_id == "proc-ended"
    assert inst.is_ended is True
    await client.aclose()


@pytest.mark.asyncio
async def test_get_instance_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    inst = await adapter.get_instance("proc-missing", tenant_id="tenant-alpha")
    assert inst is None
    await client.aclose()


@pytest.mark.asyncio
async def test_list_user_tasks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/runtime/tasks" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "task-001",
                        "processInstanceId": "proc-456",
                        "taskDefinitionKey": "manager_review",
                        "name": "Manager Review",
                        "assignee": "alice",
                        "createTime": "2026-10-02T12:01:00Z",
                        "dueDate": None,
                        "tenantId": "tenant-alpha",
                    }
                ],
                "total": 1,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    tasks = await adapter.list_user_tasks(engine_instance_id="proc-456", tenant_id="tenant-alpha")
    assert len(tasks) == 1
    assert tasks[0].engine_task_id == "task-001"
    assert tasks[0].name == "Manager Review"
    assert tasks[0].assignee == "alice"
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_user_task():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/runtime/tasks/task-001" in str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    res = await adapter.complete_user_task(
        engine_task_id="task-001",
        action="complete",
        variables={"comment": "Looks good"},
        tenant_id="tenant-alpha",
    )
    assert res.engine_task_id == "task-001"
    assert res.status == "COMPLETED"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_history():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/history/historic-activity-instances" in str(request.url)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "activityId": "start",
                        "activityName": "Start Event",
                        "activityType": "startEvent",
                        "processInstanceId": "proc-456",
                        "startTime": "2026-10-02T12:00:00Z",
                        "endTime": "2026-10-02T12:00:01Z",
                        "assignee": None,
                    },
                    {
                        "activityId": "manager_review",
                        "activityName": "Manager Review",
                        "activityType": "userTask",
                        "processInstanceId": "proc-456",
                        "startTime": "2026-10-02T12:00:01Z",
                        "endTime": "2026-10-02T12:05:00Z",
                        "assignee": "alice",
                    },
                ],
                "total": 2,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    history = await adapter.get_history(engine_instance_id="proc-456", tenant_id="tenant-alpha")
    assert len(history) == 2
    assert history[0].activity_id == "start"
    assert history[1].activity_id == "manager_review"
    assert history[1].assignee == "alice"
    await client.aclose()


@pytest.mark.asyncio
async def test_engine_error_wrapping():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "Flowable internal failure"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    with pytest.raises(WorkflowEngineResponseError) as exc_info:
        await adapter.deploy_definition(
            bpmn_xml="<definitions/>",
            resource_name="broken.bpmn20.xml",
            tenant_id="tenant-alpha",
        )
    assert "status 500" in str(exc_info.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_claim_user_task_success():
    requests_received = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/runtime/tasks/task-claim-01" in str(request.url)
        requests_received.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    # Claim
    await adapter.claim_user_task(
        engine_task_id="task-claim-01",
        assignee="alice",
        tenant_id="tenant-alpha",
    )
    assert len(requests_received) == 1
    import json

    body1 = json.loads(requests_received[0].content)
    assert body1 == {"action": "claim", "assignee": "alice"}

    # Unclaim
    await adapter.claim_user_task(
        engine_task_id="task-claim-01",
        assignee=None,
        tenant_id="tenant-alpha",
    )
    assert len(requests_received) == 2
    body2 = json.loads(requests_received[1].content)
    assert body2 == {"action": "claim"}

    await client.aclose()


@pytest.mark.asyncio
async def test_claim_user_task_not_found_raises_resource_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Task not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    with pytest.raises(WorkflowResourceNotFoundError, match="Engine task 'task-404' not found"):
        await adapter.claim_user_task(
            engine_task_id="task-404",
            assignee="alice",
            tenant_id="tenant-alpha",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_claim_user_task_server_error_raises_engine_response_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "Internal error"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = FlowableAdapter(settings=SETTINGS, client=client)

    with pytest.raises(WorkflowEngineResponseError, match="status 500"):
        await adapter.claim_user_task(
            engine_task_id="task-500",
            assignee="alice",
            tenant_id="tenant-alpha",
        )
    await client.aclose()
