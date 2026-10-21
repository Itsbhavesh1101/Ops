"""Scheduler & CP-SAT Solver Load Benchmark Suite (Phase 8C).

Benchmarks:
1. Educational Scheduling CP-SAT Solves:
   - SMALL fixture (reduced session subset)
   - AUTHORITATIVE FEASIBLE benchmark fixture (Phase 5 dataset:
     PHASE5-SC-EXAM-FEASIBLE-001)
   - REPAIR disruption benchmark fixture (PHASE5-SC-EXAM-REPAIR-001)
2. Solver Breakdown Metrics:
   - Problem translation duration
   - CP-SAT solver duration
   - Objective score & hard constraint violations (must be 0)
3. Concurrency Modes:
   - Single solve baseline
   - Sequential solves (reproducibility & variance)
   - Overlapping concurrent solves (thread & CPU pressure)
4. Solver Queue Concurrency:
   - Concurrent multi-tenant job submissions into PostgreSQL schedule_jobs
   - Worker atomic claim via SELECT FOR UPDATE SKIP LOCKED
   - Time-to-start, time-to-result, and multi-tenant fairness.
"""

import asyncio
import time
from typing import Any

import pytest

from app.db.engine import DatabaseManager
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.service import SchedulingService
from tests.fixtures.scheduling.education.dataset import load_education_dataset
from tests.performance.fixtures import cleanup_perf_tenant_data
from tests.performance.metrics import calculate_latency_stats


@pytest.mark.asyncio
async def test_academic_scheduler_dataset_benchmarks() -> dict[str, Any]:
    """Benchmark CP-SAT solver on SMALL, AUTHORITATIVE FEASIBLE, and REPAIR educational fixtures."""
    compiler = AcademicConstraintCompiler()
    dataset_feasible, resources = load_education_dataset("FEASIBLE")
    dataset_repair, resources_repair = load_education_dataset("REPAIR")

    # Construct SMALL fixture: first 3 sessions
    dataset_small = dataset_feasible.model_copy(
        update={
            "dataset_id": "SMALL-BENCHMARK-001",
            "sessions": dataset_feasible.sessions[:3],
        }
    )

    scenarios = [
        ("SMALL", dataset_small, resources, False),
        ("AUTHORITATIVE_FEASIBLE", dataset_feasible, resources, False),
        ("REPAIR", dataset_repair, resources_repair, True),
    ]

    scenario_results: dict[str, Any] = {}

    for name, ds, res, is_repair in scenarios:
        translation_times: list[float] = []
        solve_times: list[float] = []
        total_times: list[float] = []
        hard_violations: list[int] = []
        statuses: list[str] = []

        trials = 3
        for _ in range(trials):
            t0 = time.perf_counter()
            _req = compiler.compile_request(ds, res, is_repair=is_repair)
            t_trans = time.perf_counter() - t0
            translation_times.append(t_trans)

            t1 = time.perf_counter()
            solver_res, asgns = compiler.solve(ds, res, is_repair=is_repair)
            t_solve = time.perf_counter() - t1
            solve_times.append(t_solve)

            total_elapsed = time.perf_counter() - t0
            total_times.append(total_elapsed)

            hard_violations.append(solver_res.score_components.hard_constraint_violations)
            statuses.append(solver_res.status.value)
            assert solver_res.score_components.hard_constraint_violations == 0
            assert solver_res.status.value == "FEASIBLE"
            assert len(asgns) == len(ds.sessions)

        stats = calculate_latency_stats(total_times, sum(total_times))
        scenario_results[name] = {
            "stats": stats.to_dict(),
            "avg_translation_ms": round(sum(translation_times) / trials * 1000.0, 2),
            "avg_solve_ms": round(sum(solve_times) / trials * 1000.0, 2),
            "avg_total_ms": round(sum(total_times) / trials * 1000.0, 2),
            "hard_violations": hard_violations[0],
            "solution_status": statuses[0],
            "sessions_count": len(ds.sessions),
            "authoritative_target_met": (sum(total_times) / trials) < 60.0,  # ADR-010 target: < 60s
        }

    return scenario_results


@pytest.mark.asyncio
async def test_scheduler_concurrent_solves() -> dict[str, Any]:
    """Benchmark overlapping concurrent CP-SAT solves to evaluate CPU scaling."""
    compiler = AcademicConstraintCompiler()
    dataset_feasible, resources = load_education_dataset("FEASIBLE")

    concurrency_levels = [1, 2, 4]
    concurrency_results: dict[str, Any] = {}

    for c in concurrency_levels:
        latencies: list[float] = []

        async def solve_task(lats: list[float] = latencies) -> None:
            # Run CPU-bound CP-SAT solve in thread pool to measure concurrent scaling
            t0 = time.perf_counter()
            solver_res, _ = await asyncio.to_thread(compiler.solve, dataset_feasible, resources)
            elapsed = time.perf_counter() - t0
            lats.append(elapsed)
            assert solver_res.score_components.hard_constraint_violations == 0

        t_start = time.perf_counter()
        tasks = [asyncio.create_task(solve_task()) for _ in range(c)]
        await asyncio.gather(*tasks)
        total_duration = time.perf_counter() - t_start

        stats = calculate_latency_stats(latencies, total_duration)
        concurrency_results[f"concurrency_{c}"] = {
            **stats.to_dict(),
            "wall_clock_seconds": round(total_duration, 3),
        }

    return concurrency_results


@pytest.mark.asyncio
async def test_solver_queue_multi_tenant_concurrency() -> dict[str, Any]:
    """Benchmark solver job submission, atomic queue claiming, and execution across tenants."""
    tenants = ["perf-tenant-sched-01", "perf-tenant-sched-02", "perf-tenant-sched-03"]
    await cleanup_perf_tenant_data(tenant_prefix="perf-tenant-sched-")

    db_manager = DatabaseManager()
    sched_service = SchedulingService()
    compiler = AcademicConstraintCompiler()
    dataset_feasible, resources = load_education_dataset("FEASIBLE")

    # Compile a canonical generic request to submit as jobs
    base_req = compiler.compile_request(dataset_feasible, resources)

    # 1. Concurrent Submissions across tenants
    job_ids: list[tuple[str, str]] = []  # (tenant_id, job_id)
    submit_latencies: list[float] = []

    t_sub_start = time.perf_counter()
    async with db_manager.session_factory() as session:
        for t_idx, t_id in enumerate(tenants):
            req = base_req.model_copy(
                update={
                    "tenant_id": t_id,
                    "schedule_key": f"perf-sched-key-{t_idx}",
                }
            )
            for j_idx in range(3):
                t0 = time.perf_counter()
                job_rec = await sched_service.submit_job(
                    session=session,
                    tenant_id=t_id,
                    request=req,
                    schedule_key=f"perf-sched-key-{t_idx}-{j_idx}",
                )
                await session.commit()
                submit_latencies.append(time.perf_counter() - t0)
                job_ids.append((t_id, str(job_rec.id)))

    submit_duration = time.perf_counter() - t_sub_start
    submit_stats = calculate_latency_stats(submit_latencies, submit_duration)

    # 2. Worker Queue Processing (simulating 2 concurrent workers claiming jobs)
    claimed_jobs: list[str] = []
    executed_jobs: list[str] = []
    solve_latencies: list[float] = []

    async def worker(worker_id: str) -> None:
        while True:
            async with db_manager.session_factory() as session:
                claimed = await sched_service.claim_jobs(
                    session=session,
                    worker_id=worker_id,
                    batch_size=1,
                )
                if not claimed:
                    break
                job_rec = claimed[0]
                claimed_jobs.append(str(job_rec.id))
                await session.commit()

                # Execute solve
                t0 = time.perf_counter()
                await sched_service.execute_job_solve(
                    session=session,
                    job_id=job_rec.id,
                )
                await session.commit()
                solve_latencies.append(time.perf_counter() - t0)
                executed_jobs.append(str(job_rec.id))

    t_exec_start = time.perf_counter()
    w1 = asyncio.create_task(worker("perf-worker-1"))
    w2 = asyncio.create_task(worker("perf-worker-2"))
    await asyncio.gather(w1, w2)
    exec_duration = time.perf_counter() - t_exec_start

    exec_stats = calculate_latency_stats(solve_latencies, exec_duration)

    # Invariants verification
    target_ids = {jid for _, jid in job_ids}
    tenant_claimed = [j for j in claimed_jobs if j in target_ids]
    tenant_executed = [j for j in executed_jobs if j in target_ids]
    assert len(job_ids) == 9
    assert len(tenant_claimed) == 9
    assert len(tenant_executed) == 9
    # Zero duplicates
    assert len(set(tenant_claimed)) == 9
    assert len(set(tenant_executed)) == 9

    # Cleanup
    await cleanup_perf_tenant_data(tenant_prefix="perf-tenant-sched-")
    await db_manager.dispose()

    return {
        "job_submission_stats": submit_stats.to_dict(),
        "job_execution_stats": exec_stats.to_dict(),
        "total_jobs_processed": len(executed_jobs),
        "zero_duplicates_verified": True,
        "concurrency_workers": 2,
    }
