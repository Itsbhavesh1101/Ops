"""Plan Generation & Context Builder Performance Benchmark Suite (Phase 8C).

Benchmarks:
1. Context Builder Performance:
   - Adapter record retrieval
   - Data minimization & allowlisting
   - Value-level redaction and scrubbing
   - ModelContext serialization
2. Deterministic Plan Generation Pipeline:
   - Prompt construction & prompt-injection defense overhead
   - Deterministic planner execution (SemesterExamPlanner)
   - Schema validation (PlanValidator)
   - CPM DAG critical path calculation
   - Transactional plan persistence in PostgreSQL (PlanService)
3. External LLM Mode:
   - Evaluated safely: reports SKIPPED_PER_GOVERNANCE to prevent unintentional paid external calls.
"""

import time
from typing import Any
from uuid import uuid4

import pytest

from app.db.engine import DatabaseManager
from app.db.uow import create_unit_of_work_factory
from app.task_intelligence.context.adapter import SyntheticContextAdapter
from app.task_intelligence.context.builder import ContextBuilder
from app.task_intelligence.context.models import ContextAssemblyRequest
from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.planner.phase6_planner import SemesterExamPlanner
from app.task_intelligence.planner.prompts import build_generation_prompt
from app.task_intelligence.schemas import GoalCreate, PlanSchema
from app.task_intelligence.service import PlanService
from app.task_intelligence.validators import PlanValidator
from tests.performance.fixtures import cleanup_perf_tenant_data
from tests.performance.metrics import calculate_latency_stats


@pytest.mark.asyncio
async def test_context_builder_performance() -> dict[str, Any]:
    """Benchmark Task Intelligence Context Builder assembly duration and component breakdown."""
    tenant_id = "perf-tenant-cb"
    adapter = SyntheticContextAdapter(tenant_id=tenant_id)
    builder = ContextBuilder(adapter=adapter)

    request = ContextAssemblyRequest(
        tenant_id=tenant_id,
        goal_id=uuid4(),
        objective="Generate Academic Semester Exam Schedule and Operations Plan",
    )

    latencies_sec: list[float] = []
    component_breakdown: dict[str, list[float]] = {
        "adapter_read_ms": [],
        "allowlist_and_minimization_ms": [],
        "serialization_ms": [],
    }

    iterations = 50
    for _ in range(iterations):
        t0 = time.perf_counter()

        # Step 1: Adapter fetch time
        t_ad_start = time.perf_counter()
        _ = await adapter.fetch_organization_records(tenant_id)
        _ = await adapter.fetch_academic_records(tenant_id)
        _ = await adapter.fetch_subject_records(tenant_id)
        _ = await adapter.fetch_staff_records(tenant_id)
        t_ad = (time.perf_counter() - t_ad_start) * 1000.0
        component_breakdown["adapter_read_ms"].append(t_ad)

        # Step 2: Full context build (including allowlisting and privacy controls)
        t_build_start = time.perf_counter()
        result = await builder.build_context(request)
        t_build = (time.perf_counter() - t_build_start) * 1000.0
        component_breakdown["allowlist_and_minimization_ms"].append(t_build)

        # Step 3: Serialization
        t_ser_start = time.perf_counter()
        _dump = result.model_context.model_dump(exclude_none=True)
        t_ser = (time.perf_counter() - t_ser_start) * 1000.0
        component_breakdown["serialization_ms"].append(t_ser)

        total_elapsed = time.perf_counter() - t0
        latencies_sec.append(total_elapsed)

        # Verify Phase 8B privacy invariants
        assert result.assembly_status.value in ["READY", "DEGRADED"]
        assert len(result.model_context.staff_resources) > 0
        for staff in result.model_context.staff_resources:
            assert staff.opaque_staff_ref.startswith("StaffRef_")

    stats = calculate_latency_stats(latencies_sec, sum(latencies_sec))
    return {
        "total_stats": stats.to_dict(),
        "breakdown": {
            "avg_adapter_read_ms": round(
                sum(component_breakdown["adapter_read_ms"]) / iterations, 2
            ),
            "avg_allowlist_minimization_ms": round(
                sum(component_breakdown["allowlist_and_minimization_ms"]) / iterations, 2
            ),
            "avg_serialization_ms": round(
                sum(component_breakdown["serialization_ms"]) / iterations, 2
            ),
        },
        "privacy_controls_verified": True,
    }


@pytest.mark.asyncio
async def test_deterministic_planner_pipeline_load() -> dict[str, Any]:
    """Benchmark end-to-end plan generation pipeline in deterministic mode."""
    tenant_id = "perf-tenant-plan"
    await cleanup_perf_tenant_data(tenant_prefix=tenant_id)

    db_manager = DatabaseManager()
    uow_factory = create_unit_of_work_factory(db_manager.session_factory)
    plan_service = PlanService(uow_factory=uow_factory)
    planner = SemesterExamPlanner()

    # Pre-create goal in database
    goal_dto = GoalCreate(
        objective="Grade 10 Semester Examination Operations",
        scope="Standard academic examination administration",
    )
    goal_record = await plan_service.create_goal(tenant_id=tenant_id, goal_data=goal_dto)

    latencies_sec: list[float] = []
    breakdown_ms: dict[str, list[float]] = {
        "prompt_construction_ms": [],
        "planner_generation_ms": [],
        "schema_validation_ms": [],
        "cpm_calculation_ms": [],
        "plan_persistence_ms": [],
    }

    iterations = 25
    for idx in range(iterations):
        t0 = time.perf_counter()

        # 1. Prompt construction & scrub
        t_p_start = time.perf_counter()
        prompt_text = build_generation_prompt(goal_dto, context={"sample_key": f"test_val_{idx}"})
        t_p = (time.perf_counter() - t_p_start) * 1000.0
        breakdown_ms["prompt_construction_ms"].append(t_p)
        assert len(prompt_text) > 50

        # 2. Planner generation
        t_g_start = time.perf_counter()
        plan_schema: PlanSchema = await planner.generate_plan(goal_dto)
        t_g = (time.perf_counter() - t_g_start) * 1000.0
        breakdown_ms["planner_generation_ms"].append(t_g)

        # 3. Schema & DAG validation
        t_v_start = time.perf_counter()
        validator = PlanValidator()
        validation_res = validator.validate(plan_schema)
        t_v = (time.perf_counter() - t_v_start) * 1000.0
        breakdown_ms["schema_validation_ms"].append(t_v)
        assert validation_res.is_valid

        # 4. CPM Critical Path calculation
        t_c_start = time.perf_counter()
        graph = TaskDependencyGraph()
        for t in plan_schema.tasks:
            graph.add_task(t.temporary_id, t.estimated_duration_minutes)
        for d in plan_schema.dependencies:
            graph.add_dependency(d.predecessor_id, d.successor_id)
        cpm_res = CriticalPathCalculator.calculate(graph)
        t_c = (time.perf_counter() - t_c_start) * 1000.0
        breakdown_ms["cpm_calculation_ms"].append(t_c)
        assert cpm_res.project_duration_minutes > 0

        # 5. Plan persistence
        t_s_start = time.perf_counter()
        persisted_plan = await plan_service.persist_generated_plan(
            tenant_id=tenant_id,
            goal_id=goal_record.id,
            plan_schema=plan_schema,
        )
        t_s = (time.perf_counter() - t_s_start) * 1000.0
        breakdown_ms["plan_persistence_ms"].append(t_s)
        assert persisted_plan.id is not None

        elapsed = time.perf_counter() - t0
        latencies_sec.append(elapsed)

    stats = calculate_latency_stats(latencies_sec, sum(latencies_sec))

    # Teardown
    await cleanup_perf_tenant_data(tenant_prefix=tenant_id)
    await db_manager.dispose()

    return {
        "mode": "DETERMINISTIC_MODEL_MODE",
        "total_stats": stats.to_dict(),
        "breakdown": {
            "avg_prompt_construction_ms": round(
                sum(breakdown_ms["prompt_construction_ms"]) / iterations, 2
            ),
            "avg_planner_generation_ms": round(
                sum(breakdown_ms["planner_generation_ms"]) / iterations, 2
            ),
            "avg_schema_validation_ms": round(
                sum(breakdown_ms["schema_validation_ms"]) / iterations, 2
            ),
            "avg_cpm_calculation_ms": round(
                sum(breakdown_ms["cpm_calculation_ms"]) / iterations, 2
            ),
            "avg_plan_persistence_ms": round(
                sum(breakdown_ms["plan_persistence_ms"]) / iterations, 2
            ),
        },
        "external_model_mode": "SKIPPED_PER_GOVERNANCE: NO_EXTERNAL_PAID_LLM_LOAD",
    }
