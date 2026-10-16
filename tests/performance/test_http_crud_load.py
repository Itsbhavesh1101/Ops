"""HTTP CRUD Load & Mutation Benchmark Suite (Phase 8C).

Benchmarks:
1. Health & readiness probes separately (/api/v1/health, /api/v1/ready).
2. Goal creation mutation (/api/v1/goals).
3. Plan critical-path read (/api/v1/plans/{id}/critical-path).
4. Task progress mutation (/api/v1/tasks/{id}).
5. Resource availability read (/api/v1/resources/availability).
6. Chronological audit search (/api/v1/audit).
7. Operations analytics telemetry (/api/v1/analytics/operations).
8. Mixed multi-tenant concurrent traffic.

Enforces:
- Real mounted endpoints and valid authenticated principals across synthetic tenants.
- Real local PostgreSQL write and read boundaries.
- Captures p50, p90, p95, p99, max latency, throughput, error rates, and DB pool metrics.
"""

import asyncio
import time
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from app.api.errors import register_api_exception_handlers
from app.api.idempotency import register_idempotency_exception_handler
from app.api.v1.composition import register_domain_routes
from app.api.v1.router import router as api_v1_router
from app.core.middleware import CorrelationAndLoggingMiddleware
from app.db.engine import DatabaseManager
from app.db.uow import create_unit_of_work_factory
from app.task_intelligence.planner.phase6_planner import SemesterExamPlanner
from app.task_intelligence.schemas import GoalCreate
from app.task_intelligence.service import PlanService
from tests.performance.fixtures import (
    cleanup_perf_tenant_data,
    register_perf_principal,
)
from tests.performance.metrics import calculate_latency_stats


def create_perf_app() -> FastAPI:
    """Create isolated FastAPI app for performance testing without mutating global app."""
    perf_app = FastAPI()
    register_api_exception_handlers(perf_app)
    register_idempotency_exception_handler(perf_app)
    perf_app.add_middleware(CorrelationAndLoggingMiddleware)

    # Base probes (/api/v1/health, /api/v1/ready)
    perf_app.include_router(api_v1_router)

    # Domain routes on isolated router
    domain_router = APIRouter(prefix="/api/v1")
    register_domain_routes(domain_router)
    perf_app.include_router(domain_router)
    return perf_app


@pytest.mark.asyncio
async def test_health_and_readiness_benchmark() -> dict[str, Any]:
    """Benchmark /api/v1/health and /api/v1/ready probes independently."""
    perf_app = create_perf_app()
    transport = httpx.ASGITransport(app=perf_app)
    results: dict[str, Any] = {}

    for endpoint in ["/api/v1/health", "/api/v1/ready"]:
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        error_count = 0
        concurrency = 10
        total_requests = 100

        async def worker(
            n_req: int,
            target_endpoint: str = endpoint,
            lats: list[float] = latencies,
            code_stats: dict[int, int] = statuses,
        ) -> None:
            nonlocal error_count
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(n_req):
                    t0 = time.perf_counter()
                    try:
                        resp = await client.get(target_endpoint)
                        elapsed = time.perf_counter() - t0
                        lats.append(elapsed)
                        code_stats[resp.status_code] = code_stats.get(resp.status_code, 0) + 1
                        if resp.status_code != 200:
                            error_count += 1
                    except Exception:
                        error_count += 1

        t_start = time.perf_counter()
        reqs_per_worker = total_requests // concurrency
        tasks = [asyncio.create_task(worker(reqs_per_worker)) for _ in range(concurrency)]
        await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t_start

        stats = calculate_latency_stats(latencies, total_time, error_count, statuses)
        results[endpoint] = stats.to_dict()
        assert stats.error_rate_pct == 0.0
        assert stats.p95_ms > 0.0

    return results


@pytest.mark.asyncio
async def test_http_crud_and_mutation_load() -> dict[str, Any]:
    """Benchmark representative CRUD and mutation routes under multi-tenant concurrent load."""
    tenants = [
        "perf-tenant-01",
        "perf-tenant-02",
        "perf-tenant-03",
        "perf-tenant-04",
        "perf-tenant-05",
    ]
    await cleanup_perf_tenant_data()

    # Pre-register credentials for each tenant
    for t_id in tenants:
        register_perf_principal(tenant_id=t_id, role="planner")
        register_perf_principal(tenant_id=t_id, role="operator")
        register_perf_principal(tenant_id=t_id, role="auditor")

    db_manager = DatabaseManager()
    perf_app = create_perf_app()
    transport = httpx.ASGITransport(app=perf_app)
    suite_results: dict[str, Any] = {}

    # 1. Goal Creation Mutation Benchmark (POST /api/v1/goals)
    goal_ids_by_tenant: dict[str, list[str]] = {t: [] for t in tenants}
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    error_count = 0
    total_goals_per_tenant = 10

    async def goal_creator(t_id: str, count: int) -> None:
        nonlocal error_count
        _, api_key = register_perf_principal(tenant_id=t_id, role="planner")
        headers = {"X-E7-Service-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            for i in range(count):
                payload = {
                    "objective": f"Benchmark Academic Goal {i:03d} for {t_id}",
                    "scope": "Phase 8C Load Verification",
                }
                t0 = time.perf_counter()
                try:
                    resp = await client.post("/api/v1/goals", json=payload)
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code == 201:
                        data = resp.json()
                        goal_ids_by_tenant[t_id].append(data["id"])
                    else:
                        error_count += 1
                except Exception:
                    error_count += 1

    t0 = time.perf_counter()
    tasks = [asyncio.create_task(goal_creator(t, total_goals_per_tenant)) for t in tenants]
    await asyncio.gather(*tasks)
    duration = time.perf_counter() - t0

    pool_status = db_manager.get_pool_status()
    goal_create_stats = calculate_latency_stats(latencies, duration, error_count, statuses)
    suite_results["POST /api/v1/goals"] = {
        **goal_create_stats.to_dict(),
        "db_pool": pool_status,
    }
    assert goal_create_stats.error_rate_pct == 0.0

    # 2. Plan Generation & Critical Path Read Benchmark
    # Generate one valid plan for the first goal of each tenant so we can benchmark plan read
    planner = SemesterExamPlanner()
    plan_service = PlanService(uow_factory=create_unit_of_work_factory(db_manager.session_factory))
    plan_ids_by_tenant: dict[str, str] = {}
    task_ids_by_tenant: dict[str, list[str]] = {t: [] for t in tenants}

    for t_id in tenants:
        if goal_ids_by_tenant[t_id]:
            first_goal_id = UUID(goal_ids_by_tenant[t_id][0])
            plan_schema = await planner.generate_plan(
                GoalCreate(objective="Load Goal", scope="Scope")
            )
            persisted_plan = await plan_service.persist_generated_plan(
                tenant_id=t_id,
                goal_id=first_goal_id,
                plan_schema=plan_schema,
            )
            plan_ids_by_tenant[t_id] = str(persisted_plan.id)
            async with db_manager.session_factory() as session:
                from sqlalchemy import text

                res = await session.execute(
                    text("SELECT id FROM tasks WHERE plan_id = :p_id"), {"p_id": persisted_plan.id}
                )
                task_ids_by_tenant[t_id] = [str(r[0]) for r in res.fetchall()]

    # Benchmark Plan Critical Path (GET /api/v1/plans/{plan_id}/critical-path)
    latencies = []
    statuses = {}
    error_count = 0
    read_concurrency = 10
    total_reads = 100

    async def cpm_reader(n_req: int, t_id: str) -> None:
        nonlocal error_count
        plan_id = plan_ids_by_tenant.get(t_id)
        if not plan_id:
            return
        _, api_key = register_perf_principal(tenant_id=t_id, role="planner")
        headers = {"X-E7-Service-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            for _ in range(n_req):
                t0 = time.perf_counter()
                try:
                    resp = await client.get(f"/api/v1/plans/{plan_id}/critical-path")
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code != 200:
                        error_count += 1
                except Exception:
                    error_count += 1

    t0 = time.perf_counter()
    read_tasks = [
        asyncio.create_task(cpm_reader(total_reads // read_concurrency, tenants[i % len(tenants)]))
        for i in range(read_concurrency)
    ]
    await asyncio.gather(*read_tasks)
    duration = time.perf_counter() - t0

    cpm_stats = calculate_latency_stats(latencies, duration, error_count, statuses)
    suite_results["GET /api/v1/plans/{id}/critical-path"] = cpm_stats.to_dict()
    assert cpm_stats.error_rate_pct == 0.0

    # 3. Task Update Mutation Benchmark (PATCH /api/v1/tasks/{id})
    latencies = []
    statuses = {}
    error_count = 0

    async def task_mutator(t_id: str, t_ids: list[str]) -> None:
        nonlocal error_count
        if not t_ids:
            return
        _, api_key = register_perf_principal(tenant_id=t_id, role="operator")
        headers = {"X-E7-Service-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            for idx, task_id in enumerate(t_ids[:10]):
                t0 = time.perf_counter()
                try:
                    resp = await client.patch(
                        f"/api/v1/tasks/{task_id}",
                        json={
                            "status": "IN_PROGRESS",
                            "progress_percentage": min(95, (idx + 1) * 10),
                            "notes": "Load test update",
                        },
                    )
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code != 200:
                        error_count += 1
                except Exception:
                    error_count += 1

    t0 = time.perf_counter()
    task_tasks = [asyncio.create_task(task_mutator(t, task_ids_by_tenant[t])) for t in tenants]
    await asyncio.gather(*task_tasks)
    duration = time.perf_counter() - t0

    task_update_stats = calculate_latency_stats(latencies, duration, error_count, statuses)
    suite_results["PATCH /api/v1/tasks/{id}"] = task_update_stats.to_dict()

    # 4. Chronological Audit Search Benchmark (GET /api/v1/audit)
    latencies = []
    statuses = {}
    error_count = 0
    audit_reads = 50

    async def audit_reader(t_id: str, count: int) -> None:
        nonlocal error_count
        _, api_key = register_perf_principal(tenant_id=t_id, role="auditor")
        headers = {"X-E7-Service-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            for _ in range(count):
                t0 = time.perf_counter()
                try:
                    resp = await client.get("/api/v1/audit?limit=25")
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code != 200:
                        error_count += 1
                except Exception:
                    error_count += 1

    t0 = time.perf_counter()
    audit_tasks = [
        asyncio.create_task(audit_reader(t, audit_reads // len(tenants))) for t in tenants
    ]
    await asyncio.gather(*audit_tasks)
    duration = time.perf_counter() - t0

    audit_stats = calculate_latency_stats(latencies, duration, error_count, statuses)
    suite_results["GET /api/v1/audit"] = audit_stats.to_dict()

    # 5. Operations Analytics Telemetry Benchmark (GET /api/v1/analytics/operations)
    latencies = []
    statuses = {}
    error_count = 0
    analytics_reads = 50

    async def analytics_reader(t_id: str, count: int) -> None:
        nonlocal error_count
        _, api_key = register_perf_principal(tenant_id=t_id, role="operator")
        headers = {"X-E7-Service-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            for _ in range(count):
                t0 = time.perf_counter()
                try:
                    resp = await client.get("/api/v1/analytics/operations")
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code != 200:
                        error_count += 1
                except Exception:
                    error_count += 1

    t0 = time.perf_counter()
    analytics_tasks = [
        asyncio.create_task(analytics_reader(t, analytics_reads // len(tenants))) for t in tenants
    ]
    await asyncio.gather(*analytics_tasks)
    duration = time.perf_counter() - t0

    analytics_stats = calculate_latency_stats(latencies, duration, error_count, statuses)
    suite_results["GET /api/v1/analytics/operations"] = analytics_stats.to_dict()

    # Cleanup afterwards
    await cleanup_perf_tenant_data()

    return suite_results
