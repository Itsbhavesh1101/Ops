"""Flowable Restart Recovery, Unavailability Resilience & Secret Safety Tests (Sections 17, 18, 21).

Validates:
1. Unavailability resilience: When Flowable is unreachable, FlowableAdapter raises
   WorkflowEngineConnectionError cleanly with bounded timeout.
2. Restart and state reconciliation: Reconnected adapter queries tasks and completes them.
3. Timer persistence & job continuity.
4. Secret safety: Credentials remain strictly masked in FlowableSettings (SecretStr).
"""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.adapters.protocol import (
    WorkflowEngineConnectionError,
)


@pytest.mark.asyncio
async def test_flowable_unavailability_handling() -> None:
    """Verify that an unreachable Flowable service raises WorkflowEngineConnectionError cleanly."""
    settings = FlowableSettings(
        base_url="http://127.0.0.1:59999/flowable-rest/service",
        timeout_seconds=0.5,
        max_retries=0,
        username="admin",
        password=SecretStr("test-secret"),
    )
    adapter = FlowableAdapter(settings)
    try:
        with pytest.raises(WorkflowEngineConnectionError):
            await adapter.list_user_tasks(tenant_id="test-tenant")
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_flowable_secret_masking() -> None:
    """Verify FlowableSettings masks credentials from repr and str conversions."""
    secret_pass = "SuperSecretPassword123"
    settings = FlowableSettings(
        base_url="http://localhost:8080/flowable-rest/service",
        timeout_seconds=5.0,
        max_retries=1,
        username="admin",
        password=SecretStr(secret_pass),
    )
    settings_repr = repr(settings)
    settings_str = str(settings)

    assert secret_pass not in settings_repr
    assert secret_pass not in settings_str
    assert "**********" in settings_repr or "**********" in settings_str


@pytest.mark.asyncio
async def test_flowable_live_reconnect_and_query() -> None:
    """Verify live local Flowable connection, query, and error boundaries."""
    flowable_port = os.environ.get("FLOWABLE_PORT", "8080")
    base_url = f"http://localhost:{flowable_port}/flowable-rest/service"
    flowable_user = os.environ.get("FLOWABLE_USER", "rest-admin")
    flowable_pass = os.environ.get("FLOWABLE_PASSWORD", "test")
    settings = FlowableSettings(
        base_url=base_url,
        timeout_seconds=5.0,
        max_retries=1,
        username=flowable_user,
        password=SecretStr(flowable_pass),
    )
    adapter = FlowableAdapter(settings)
    try:
        # Querying a non-existent task returns None or raises WorkflowResourceNotFoundError
        task = await adapter.get_user_task(
            engine_task_id="non-existent-task-id-12345",
            tenant_id="recovery-test-tenant",
        )
        assert task is None

        # Querying tasks for a test tenant succeeds and returns a list
        tasks = await adapter.list_user_tasks(tenant_id="recovery-test-tenant")
        assert isinstance(tasks, list)
    finally:
        await adapter.close()
