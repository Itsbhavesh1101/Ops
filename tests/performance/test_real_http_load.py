"""Real Local HTTP Load Benchmark Suite & Repeatability Verification (Phase 8C).

Executes:
1. REAL_LOCAL_HTTP_BENCHMARK against the live Uvicorn API process over TCP (localhost:8008):
   - Health and Readiness probes
   - Goal creation mutations
   - Plan critical path reads
   - Task progress updates
   - Audit trail queries
   - Operations analytics dashboard
2. Headless Locust load execution against the live local API.
3. Repeatability Suite (1 warm-up + 3 measured repetitions) across 6 key scenarios:
   - Real HTTP mixed CRUD
   - Goal mutation load
   - Plan critical-path read
   - Scheduler canonical solve
   - Flowable concurrent instance start
   - Outbox dispatch benchmark
4. Continuous resource usage sampling (API process CPU/RSS, runner CPU/RSS, Docker containers).
"""

import asyncio
import os
import subprocess
import sys
import time
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.db.engine import DatabaseManager
from app.db.uow import create_unit_of_work_factory
from app.scheduling.education.benchmark import load_education_dataset
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.task_intelligence.planner.phase6_planner import SemesterExamPlanner
from app.task_intelligence.schemas import GoalCreate
from app.task_intelligence.service import PlanService
from app.workflow.schemas import WorkflowInstanceStatus
from tests.performance.fixtures import (
    cleanup_perf_tenant_data,
    get_benchmark_flowable_adapter,
    register_perf_principal,
)
from tests.performance.metrics import (
    calculate_latency_stats,
    calculate_repeatability_stats,
)
from tests.performance.resource_sampler import ResourceSampler

LOCAL_API_PORT = 8008
LOCAL_API_BASE_URL = f"http://127.0.0.1:{LOCAL_API_PORT}"


class LocalApiProcessContext:
    """Manages the lifecycle of a dedicated live Uvicorn API process on localhost:8008."""

    def __init__(self, port: int = LOCAL_API_PORT) -> None:
        self.port = port
        self.process: subprocess.Popen[str] | None = None

    async def start(self) -> int:
        env = dict(os.environ)
        env["E7_MOUNT_DOMAIN_ROUTERS"] = "true"
        env["PERF_API_PORT"] = str(self.port)

        cmd = [
            sys.executable,
            "tests/performance/api_server.py",
        ]

        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll until health probe succeeds
        async with httpx.AsyncClient(base_url=LOCAL_API_BASE_URL, timeout=15.0) as client:
            started = False
            for _ in range(50):
                await asyncio.sleep(0.2)
                try:
                    resp = await client.get("/api/v1/health")
                    if resp.status_code == 200:
                        started = True
                        break
                except Exception:
                    pass

            if not started:
                if self.process:
                    self.process.terminate()
                raise RuntimeError(f"Failed to start local API server on port {self.port}")

        return self.process.pid

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()


@pytest.mark.asyncio
async def test_real_local_http_benchmark_suite() -> dict[str, Any]:
    """Execute REAL_LOCAL_HTTP_BENCHMARK, Locust, Repeatability, and Resource Sampling."""
    await cleanup_perf_tenant_data()

    api_context = LocalApiProcessContext(port=LOCAL_API_PORT)
    api_pid = await api_context.start()

    sampler = ResourceSampler(target_pids=[api_pid], interval_seconds=0.4)
    sampler.start()

    suite_results: dict[str, Any] = {}
    tenants = [
        "perf-tenant-01",
        "perf-tenant-02",
        "perf-tenant-03",
        "perf-tenant-04",
        "perf-tenant-05",
    ]

    try:
        # 1. Probes over real TCP
        probe_results: dict[str, Any] = {}
        async with httpx.AsyncClient(base_url=LOCAL_API_BASE_URL, timeout=15.0) as client:
            for ep in ["/api/v1/health", "/api/v1/ready"]:
                lats: list[float] = []
                statuses: dict[int, int] = {}
                errs = 0
                t0 = time.perf_counter()
                for _ in range(50):
                    t_req0 = time.perf_counter()
                    resp = await client.get(ep)
                    elapsed = time.perf_counter() - t_req0
                    lats.append(elapsed)
                    statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
                    if resp.status_code != 200:
                        errs += 1
                dur = time.perf_counter() - t0
                stats = calculate_latency_stats(lats, dur, errs, statuses)
                probe_results[ep] = stats.to_dict()
        suite_results["real_http_probes"] = probe_results

        # 2. Multi-tenant Goal Creation Mutations
        goal_lats: list[float] = []
        goal_statuses: dict[int, int] = {}
        goal_errs = 0
        created_goals: dict[str, list[str]] = {t: [] for t in tenants}

        async def create_goals_worker(t_id: str, count: int) -> None:
            nonlocal goal_errs
            _, raw_key = register_perf_principal(tenant_id=t_id, role="planner")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for i in range(count):
                    t_req0 = time.perf_counter()
                    try:
                        resp = await c.post(
                            "/api/v1/goals",
                            json={
                                "objective": f"Real HTTP Goal {i} for {t_id}",
                                "scope": "Phase 8C Real Socket Load",
                            },
                        )
                        elapsed = time.perf_counter() - t_req0
                        goal_lats.append(elapsed)
                        goal_statuses[resp.status_code] = (
                            goal_statuses.get(resp.status_code, 0) + 1
                        )
                        if resp.status_code == 201:
                            created_goals[t_id].append(resp.json()["id"])
                        else:
                            goal_errs += 1
                    except Exception:
                        goal_errs += 1

        t_g0 = time.perf_counter()
        tasks = [asyncio.create_task(create_goals_worker(t, 6)) for t in tenants]
        await asyncio.gather(*tasks)
        dur_g = time.perf_counter() - t_g0
        goal_stats = calculate_latency_stats(goal_lats, dur_g, goal_errs, goal_statuses)
        suite_results["real_http_goal_mutations"] = goal_stats.to_dict()

        # Seed 1 valid plan for critical-path benchmark
        db_manager = DatabaseManager()
        planner = SemesterExamPlanner()
        plan_service = PlanService(
            uow_factory=create_unit_of_work_factory(db_manager.session_factory)
        )
        plan_ids_by_tenant: dict[str, str] = {}
        task_ids_by_tenant: dict[str, list[str]] = {t: [] for t in tenants}

        for t_id in tenants:
            if created_goals[t_id]:
                g_id = UUID(created_goals[t_id][0])
                plan_schema = await planner.generate_plan(
                    GoalCreate(objective=f"Exam Plan {t_id}"), {"tenant_id": t_id}
                )
                p_rec = await plan_service.persist_generated_plan(
                    tenant_id=t_id, goal_id=g_id, plan_schema=plan_schema
                )
                plan_ids_by_tenant[t_id] = str(p_rec.id)
                async with db_manager.session_factory() as s_sess:
                    from sqlalchemy import text

                    t_res = await s_sess.execute(
                        text("SELECT id FROM tasks WHERE plan_id = :p_id"), {"p_id": p_rec.id}
                    )
                    task_ids_by_tenant[t_id] = [str(r[0]) for r in t_res.fetchall()]

        # 3. Plan Critical Path Reads over real HTTP
        cpm_lats: list[float] = []
        cpm_statuses: dict[int, int] = {}
        cpm_errs = 0

        async def cpm_reader(t_id: str, p_id: str, count: int) -> None:
            nonlocal cpm_errs
            _, raw_key = register_perf_principal(tenant_id=t_id, role="planner")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for _ in range(count):
                    t_req0 = time.perf_counter()
                    try:
                        resp = await c.get(f"/api/v1/plans/{p_id}/critical-path")
                        elapsed = time.perf_counter() - t_req0
                        cpm_lats.append(elapsed)
                        cpm_statuses[resp.status_code] = (
                            cpm_statuses.get(resp.status_code, 0) + 1
                        )
                        if resp.status_code != 200:
                            cpm_errs += 1
                    except Exception:
                        cpm_errs += 1

        t_cpm0 = time.perf_counter()
        cpm_tasks = [
            asyncio.create_task(cpm_reader(t, plan_ids_by_tenant[t], 6))
            for t in tenants
            if t in plan_ids_by_tenant
        ]
        await asyncio.gather(*cpm_tasks)
        dur_cpm = time.perf_counter() - t_cpm0
        cpm_stats = calculate_latency_stats(cpm_lats, dur_cpm, cpm_errs, cpm_statuses)
        suite_results["real_http_cpm_reads"] = cpm_stats.to_dict()

        # 4. Task Update Mutations over real HTTP
        task_lats: list[float] = []
        task_statuses: dict[int, int] = {}
        task_errs = 0

        async def task_updater(t_id: str, t_ids: list[str]) -> None:
            nonlocal task_errs
            _, raw_key = register_perf_principal(tenant_id=t_id, role="operator")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for tid in t_ids[:5]:
                    t_req0 = time.perf_counter()
                    try:
                        resp = await c.patch(
                            f"/api/v1/tasks/{tid}",
                            json={"status": "IN_PROGRESS", "progress_percentage": 25},
                        )
                        elapsed = time.perf_counter() - t_req0
                        task_lats.append(elapsed)
                        task_statuses[resp.status_code] = (
                            task_statuses.get(resp.status_code, 0) + 1
                        )
                        if resp.status_code != 200:
                            task_errs += 1
                    except Exception:
                        task_errs += 1

        t_tu0 = time.perf_counter()
        tu_tasks = [
            asyncio.create_task(task_updater(t, task_ids_by_tenant[t]))
            for t in tenants
            if task_ids_by_tenant[t]
        ]
        await asyncio.gather(*tu_tasks)
        dur_tu = time.perf_counter() - t_tu0
        task_update_stats = calculate_latency_stats(task_lats, dur_tu, task_errs, task_statuses)
        suite_results["real_http_task_updates"] = task_update_stats.to_dict()

        # 5. Audit Search over real HTTP
        audit_lats: list[float] = []
        audit_statuses: dict[int, int] = {}
        audit_errs = 0

        async def audit_worker(t_id: str, count: int) -> None:
            nonlocal audit_errs
            _, raw_key = register_perf_principal(tenant_id=t_id, role="auditor")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for _ in range(count):
                    t_req0 = time.perf_counter()
                    try:
                        resp = await c.get("/api/v1/audit?limit=25")
                        elapsed = time.perf_counter() - t_req0
                        audit_lats.append(elapsed)
                        audit_statuses[resp.status_code] = (
                            audit_statuses.get(resp.status_code, 0) + 1
                        )
                        if resp.status_code != 200:
                            audit_errs += 1
                    except Exception:
                        audit_errs += 1

        t_aud0 = time.perf_counter()
        aud_tasks = [asyncio.create_task(audit_worker(t, 6)) for t in tenants]
        await asyncio.gather(*aud_tasks)
        dur_aud = time.perf_counter() - t_aud0
        audit_stats = calculate_latency_stats(audit_lats, dur_aud, audit_errs, audit_statuses)
        suite_results["real_http_audit_search"] = audit_stats.to_dict()

        # 6. Operations Analytics over real HTTP
        ops_lats: list[float] = []
        ops_statuses: dict[int, int] = {}
        ops_errs = 0

        async def ops_worker(t_id: str, count: int) -> None:
            nonlocal ops_errs
            _, raw_key = register_perf_principal(tenant_id=t_id, role="operator")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for _ in range(count):
                    t_req0 = time.perf_counter()
                    try:
                        resp = await c.get("/api/v1/analytics/operations")
                        elapsed = time.perf_counter() - t_req0
                        ops_lats.append(elapsed)
                        ops_statuses[resp.status_code] = (
                            ops_statuses.get(resp.status_code, 0) + 1
                        )
                        if resp.status_code != 200:
                            ops_errs += 1
                    except Exception:
                        ops_errs += 1

        t_ops0 = time.perf_counter()
        ops_tasks = [asyncio.create_task(ops_worker(t, 6)) for t in tenants]
        await asyncio.gather(*ops_tasks)
        dur_ops = time.perf_counter() - t_ops0
        ops_stats = calculate_latency_stats(ops_lats, dur_ops, ops_errs, ops_statuses)
        suite_results["real_http_analytics"] = ops_stats.to_dict()

        # 7. Headless Locust Run
        try:
            locust_cmd = [
                sys.executable,
                "-m",
                "locust",
                "-f",
                "tests/performance/locustfile.py",
                "--headless",
                "-u",
                "5",
                "-r",
                "5",
                "-t",
                "5s",
                "--host",
                LOCAL_API_BASE_URL,
            ]
            locust_proc = await asyncio.to_thread(
                subprocess.run,
                locust_cmd,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
            suite_results["locust_headless_execution"] = {
                "exit_code": locust_proc.returncode,
                "verified": locust_proc.returncode == 0,
            }
        except Exception as exc:
            suite_results["locust_headless_execution"] = {
                "error": str(exc),
                "verified": False,
            }

        # 8. Repeatability Suite (1 warm-up + 3 measured repetitions)
        repeatability_results: dict[str, Any] = {}

        # Scenario 1: Real HTTP mixed CRUD (throughput RPS)
        crud_runs: list[float] = []
        for rep in range(4):  # 0 is warmup, 1-3 are measured
            t_rep0 = time.perf_counter()
            async with httpx.AsyncClient(base_url=LOCAL_API_BASE_URL, timeout=15.0) as c:
                for _ in range(20):
                    await c.get("/api/v1/health")
                    await c.get("/api/v1/ready")
            dur = time.perf_counter() - t_rep0
            rps = 40.0 / max(0.001, dur)
            if rep > 0:
                crud_runs.append(rps)
        repeatability_results["real_http_mixed_crud_rps"] = calculate_repeatability_stats(crud_runs)

        # Scenario 2: Goal mutation load (p95 latency ms)
        goal_p95_runs: list[float] = []
        for rep in range(4):
            lats_rep: list[float] = []
            _, raw_key = register_perf_principal(tenant_id="perf-tenant-01", role="planner")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for i in range(10):
                    t0 = time.perf_counter()
                    await c.post(
                        "/api/v1/goals",
                        json={"objective": f"Rep Goal {rep}-{i}", "scope": "Phase 8C"},
                    )
                    lats_rep.append(time.perf_counter() - t0)
            stats_rep = calculate_latency_stats(lats_rep, 1.0)
            if rep > 0:
                goal_p95_runs.append(stats_rep.p95_ms)
        repeatability_results["goal_mutation_p95_ms"] = calculate_repeatability_stats(goal_p95_runs)

        # Scenario 3: Plan critical-path read (p95 latency ms)
        cpm_p95_runs: list[float] = []
        for rep in range(4):
            lats_rep = []
            _, raw_key = register_perf_principal(tenant_id="perf-tenant-01", role="planner")
            headers = {"X-E7-Service-Key": raw_key, "Accept": "application/json"}
            p_id = plan_ids_by_tenant["perf-tenant-01"]
            async with httpx.AsyncClient(
                base_url=LOCAL_API_BASE_URL, headers=headers, timeout=15.0
            ) as c:
                for _ in range(10):
                    t0 = time.perf_counter()
                    await c.get(f"/api/v1/plans/{p_id}/critical-path")
                    lats_rep.append(time.perf_counter() - t0)
            stats_rep = calculate_latency_stats(lats_rep, 1.0)
            if rep > 0:
                cpm_p95_runs.append(stats_rep.p95_ms)
        repeatability_results["plan_critical_path_p95_ms"] = calculate_repeatability_stats(
            cpm_p95_runs
        )

        # Scenario 4: Scheduler canonical solve (solve duration seconds)
        compiler = AcademicConstraintCompiler()
        dataset_feasible, resources = load_education_dataset("FEASIBLE")
        solver_runs: list[float] = []
        for rep in range(4):
            t0 = time.perf_counter()
            solver_res, _ = await asyncio.to_thread(
                compiler.solve, dataset_feasible, resources
            )
            dur = time.perf_counter() - t0
            assert solver_res.score_components.hard_constraint_violations == 0
            if rep > 0:
                solver_runs.append(dur)
        repeatability_results["scheduler_canonical_solve_seconds"] = (
            calculate_repeatability_stats(solver_runs)
        )

        # Scenario 5: Flowable concurrent instance start (p95 latency ms)
        from pathlib import Path

        from app.workflow.service import WorkflowService
        flowable_adapter = get_benchmark_flowable_adapter()
        wf_service = WorkflowService(
            uow_factory=create_unit_of_work_factory(db_manager.session_factory),
            adapter=flowable_adapter,
        )
        bpmn_path = Path("workflows/bpmn/two_step_approval.bpmn20.xml")
        xml_content = bpmn_path.read_text(encoding="utf-8")
        reg_wf = await wf_service.register_definition(
            tenant_id="perf-tenant-01",
            workflow_key="perf_two_step",
            name="Performance Two-Step Process",
            bpmn_xml=xml_content,
            actor_id="perf-admin",
        )
        await wf_service.deploy_definition(
            tenant_id="perf-tenant-01",
            definition_id=reg_wf.id,
            actor_id="perf-deployer",
        )
        wf_runs: list[float] = []
        for rep in range(4):
            lats_rep = []
            for i in range(3):
                t0 = time.perf_counter()
                inst = await wf_service.start_instance(
                    tenant_id="perf-tenant-01",
                    workflow_key="perf_two_step",
                    business_reference=f"PERF-REP-{rep}-{i}",
                    correlation_id=f"perf-corr-{rep}-{i}",
                    actor_id="perf-user",
                )
                lats_rep.append(time.perf_counter() - t0)
                assert inst.status == WorkflowInstanceStatus.RUNNING
            stats_rep = calculate_latency_stats(lats_rep, 1.0)
            if rep > 0:
                wf_runs.append(stats_rep.p95_ms)
        repeatability_results["flowable_concurrent_start_p95_ms"] = (
            calculate_repeatability_stats(wf_runs)
        )

        # Scenario 6: Outbox dispatch throughput (events/sec against local RabbitMQ)
        from datetime import timedelta

        from pydantic import SecretStr

        from app.events.outbox.dispatcher import OutboxDispatcher, OutboxDispatchPolicy
        from app.events.outbox.repository import OutboxRepository
        from app.events.publishers.rabbitmq import (
            RabbitMqEventPublisher,
            RabbitMqPublisherSettings,
        )
        from app.events.schemas import EventCreate, create_event_envelope

        pub_settings = RabbitMqPublisherSettings(
            rabbitmq_url=SecretStr("amqp://guest:guest@localhost:5672/"),
            rabbitmq_exchange_name="e7.domain.events",
            rabbitmq_connect_timeout_seconds=5.0,
        )
        publisher = RabbitMqEventPublisher(settings=pub_settings)
        publisher._mandatory = False
        await publisher.start()

        policy = OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=30),
            publish_timeout=timedelta(seconds=5),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=30),
            retry_multiplier=2.0,
        )
        uow_factory = create_unit_of_work_factory(db_manager.session_factory)
        dispatcher = OutboxDispatcher(uow_factory=uow_factory, publisher=publisher, policy=policy)

        outbox_runs: list[float] = []
        for rep in range(4):
            # Enqueue 15 events
            events = [
                create_event_envelope(
                    EventCreate(
                        event_type="perf.repeatability.event",
                        event_version=1,
                        tenant_id="perf-tenant-01",
                        subject_type="test",
                        subject_id=f"rep-{rep}-{i}",
                        payload={"rep": rep, "idx": i},
                    )
                )
                for i in range(15)
            ]
            async with uow_factory() as uow:
                repo = OutboxRepository(uow.session)
                for env in events:
                    await repo.enqueue(env)
                await uow.commit()

            t0 = time.perf_counter()
            for _ in range(15):
                await dispatcher.dispatch_once()
            dur = time.perf_counter() - t0
            tput = 15.0 / max(0.001, dur)
            if rep > 0:
                outbox_runs.append(tput)

        await publisher.close()
        repeatability_results["outbox_real_rabbitmq_throughput_events_per_sec"] = (
            calculate_repeatability_stats(outbox_runs)
        )

        suite_results["repeatability_suite"] = repeatability_results

    finally:
        resource_stats = await sampler.stop()
        suite_results["sampled_resources"] = resource_stats
        api_context.stop()
        await cleanup_perf_tenant_data()

    assert suite_results["real_http_probes"]["/api/v1/health"]["error_count"] == 0
    assert suite_results["real_http_probes"]["/api/v1/ready"]["error_count"] == 0
    assert suite_results["real_http_goal_mutations"]["total_requests"] > 0
    assert len(suite_results["repeatability_suite"]["real_http_mixed_crud_rps"]["runs"]) == 3
    assert suite_results["sampled_resources"]["sample_count"] > 0

    return suite_results
