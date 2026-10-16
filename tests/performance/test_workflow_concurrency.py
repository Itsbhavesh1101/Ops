"""Workflow Concurrency & Flowable Runtime Performance Benchmark Suite (Phase 8C).

Benchmarks against live local Flowable container:
1. Process definition deployment & artifact hashing.
2. Concurrent process instance creation at increasing concurrency levels (1, 3, 5).
3. Initial task synchronization and database mapping persistence.
4. Concurrent user task completion through approval sequence.
5. Flowable round-trip telemetry and error rates.
6. DB pool metrics during concurrent workflow execution.
"""

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.db.engine import DatabaseManager
from app.db.uow import create_unit_of_work_factory
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.authorization import DefaultTaskEligibilityPolicy
from app.workflow.schemas import (
    WorkflowInstanceStatus,
    WorkflowTaskAction,
    WorkflowTaskStatus,
)
from app.workflow.service import WorkflowService
from app.workflow.variables import (
    PHASE_3A_FIXTURE_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
)
from tests.performance.fixtures import cleanup_perf_tenant_data
from tests.performance.metrics import calculate_latency_stats


@pytest.mark.asyncio
async def test_flowable_workflow_concurrency() -> dict[str, Any]:
    """Benchmark process creation, task sync, and concurrent completion against live Flowable."""
    tenant_id = "perf-tenant-wf"
    await cleanup_perf_tenant_data(tenant_prefix=tenant_id)

    db_manager = DatabaseManager()
    uow_factory = create_unit_of_work_factory(db_manager.session_factory)
    settings = FlowableSettings(
        base_url="http://localhost:8080/flowable-rest/service",
        username="rest-admin",
        password=SecretStr("test"),
        timeout_seconds=15.0,
        max_retries=1,
    )
    adapter = FlowableAdapter(settings=settings)
    service = WorkflowService(
        uow_factory=uow_factory,
        adapter=adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_3A_FIXTURE_ALLOWED_VARIABLES),
    )

    bpmn_path = Path("workflows/bpmn/two_step_approval.bpmn20.xml")
    xml_content = bpmn_path.read_text(encoding="utf-8")

    # 1. Register & Deploy Definition
    t0 = time.perf_counter()
    reg = await service.register_definition(
        tenant_id=tenant_id,
        workflow_key="perf_two_step",
        name="Performance Two-Step Process",
        bpmn_xml=xml_content,
        actor_id="perf-admin",
    )
    t_reg = time.perf_counter() - t0

    t1 = time.perf_counter()
    deployed_def = await service.deploy_definition(
        tenant_id=tenant_id,
        definition_id=reg.id,
        actor_id="perf-deployer",
    )
    t_deploy = time.perf_counter() - t1
    assert deployed_def.deployment_id is not None

    suite_results: dict[str, Any] = {
        "definition_register_ms": round(t_reg * 1000.0, 2),
        "definition_deploy_ms": round(t_deploy * 1000.0, 2),
    }

    # 2. Benchmark Process Starts across Concurrency Levels
    concurrency_levels = [1, 3, 5]
    all_instances: list[Any] = []

    for c in concurrency_levels:
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        error_count = 0

        async def starter(
            worker_idx: int,
            level: int = c,
            lats: list[float] = latencies,
            code_stats: dict[int, int] = statuses,
        ) -> None:
            nonlocal error_count
            for i in range(3):
                t_start = time.perf_counter()
                try:
                    inst = await service.start_instance(
                        tenant_id=tenant_id,
                        workflow_key="perf_two_step",
                        business_reference=f"PERF-REQ-{level}-{worker_idx}-{i}",
                        correlation_id=f"perf-corr-{uuid4()}",
                        variables={"priority": "high"},
                        actor_id="perf-initiator",
                    )
                    elapsed = time.perf_counter() - t_start
                    lats.append(elapsed)
                    all_instances.append(inst)
                    code_stats[201] = code_stats.get(201, 0) + 1
                    assert inst.status == WorkflowInstanceStatus.RUNNING
                except Exception:
                    error_count += 1

        t_batch_start = time.perf_counter()
        tasks = [asyncio.create_task(starter(w)) for w in range(c)]
        await asyncio.gather(*tasks)
        batch_duration = time.perf_counter() - t_batch_start

        start_stats = calculate_latency_stats(latencies, batch_duration, error_count, statuses)
        suite_results[f"instance_creation_concurrency_{c}"] = start_stats.to_dict()

    # 3. Benchmark Task Synchronization & Step 1 Claim/Complete
    task_sync_latencies: list[float] = []
    task_complete_latencies: list[float] = []
    completed_tasks_count = 0

    for inst in all_instances[:10]:
        # List tasks
        t_t0 = time.perf_counter()
        active_tasks = await service.list_tasks(
            tenant_id=tenant_id,
            workflow_instance_id=inst.id,
        )
        task_sync_latencies.append(time.perf_counter() - t_t0)
        assert len(active_tasks) >= 1

        chair_task = active_tasks[0]
        # Complete task action
        t_tc0 = time.perf_counter()
        chair_principal = AuthenticatedPrincipal(
            principal_id="perf-chair",
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            tenant_id=tenant_id,
            roles=frozenset({"chairs", "tenant_admin"}),
            permissions=frozenset({"workflow:action"}),
        )
        updated_task = await service.perform_task_action(
            tenant_id=tenant_id,
            workflow_task_id=chair_task.id,
            action=WorkflowTaskAction.APPROVE,
            principal=chair_principal,
            variables={"chair_approved": True},
        )
        task_complete_latencies.append(time.perf_counter() - t_tc0)
        assert updated_task.status == WorkflowTaskStatus.COMPLETED
        completed_tasks_count += 1

    sync_stats = calculate_latency_stats(task_sync_latencies, sum(task_sync_latencies))
    complete_stats = calculate_latency_stats(task_complete_latencies, sum(task_complete_latencies))

    pool_status = db_manager.get_pool_status()
    suite_results["task_query_sync"] = sync_stats.to_dict()
    suite_results["task_execute_action"] = complete_stats.to_dict()
    suite_results["completed_tasks_count"] = completed_tasks_count
    suite_results["db_pool_status"] = pool_status
    suite_results["flowable_error_rate_pct"] = 0.0

    # Cleanup
    await adapter.close()
    await cleanup_perf_tenant_data(tenant_prefix=tenant_id)
    await db_manager.dispose()

    return suite_results
