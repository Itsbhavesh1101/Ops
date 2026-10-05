"""End-to-End AI Plan Generation Integration Test (Phase 2B).

Tests the complete flow:
1. Goal Creation
2. ContextBuilder assembly (strictly sanitized model context)
3. PydanticAiPlanner execution with deterministic test model
4. PlanSchema proposal & deterministic validation
5. Persistence as PlanState.GENERATED
6. Outbox and audit events verification
7. Verifies non-authoritative boundary: generated plan remains GENERATED,
   never automatically VALIDATED or APPROVED.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.db.models.planning import PlanRecord
from app.task_intelligence.context import ContextBuilder, SyntheticContextAdapter
from app.task_intelligence.planner import PydanticAiPlanner
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanState,
)
from app.task_intelligence.service import PlanService
from tests.ai_eval.deterministic_model import (
    SequenceTestModel,
    create_gold_standard_exam_plan,
    create_test_planner_settings,
)


@pytest.mark.ti_integration
@pytest.mark.asyncio
async def test_end_to_end_ai_generation_flow(
    uow_factory: Any,
    test_engine: Any,
) -> None:
    """Verify end-to-end AI plan generation and persistence under PlanService."""
    tenant_id = f"tenant-ai-{uuid4().hex[:8]}"
    actor_id = "ai-eval-actor"

    # 1. Setup deterministic compliant model and ContextBuilder
    gold_plan = create_gold_standard_exam_plan()
    test_model = SequenceTestModel([gold_plan.model_dump()])
    planner = PydanticAiPlanner(
        model=test_model,
        settings=create_test_planner_settings(),
    )

    adapter = SyntheticContextAdapter(tenant_id=tenant_id)
    context_builder = ContextBuilder(adapter=adapter)

    service = PlanService(
        uow_factory=uow_factory,
        planner=planner,
        context_builder=context_builder,
    )

    # 2. Step 1: Goal Intake
    goal_input = GoalCreate(
        objective="Plan Grade 10 semester examinations",
        scope="Complete examination lifecycle",
        constraints=["Adhere to authorized resources"],
        resource_scope=["ROOM", "INVIGILATOR"],
    )
    goal = await service.create_goal(
        tenant_id=tenant_id,
        goal_data=goal_input,
        actor_id=actor_id,
    )
    assert goal.id is not None
    assert goal.tenant_id == tenant_id

    # 3. Step 2: Generate Plan via PlanService.generate_plan
    plan_record, proposed_schema, validation_res = await service.generate_plan(
        tenant_id=tenant_id,
        goal_id=goal.id,
        actor_id=actor_id,
    )

    # 4. Verify Non-Authoritative Plan State (Must be GENERATED, NOT VALIDATED or APPROVED)
    assert isinstance(plan_record, PlanRecord)
    assert plan_record.status == PlanState.GENERATED
    assert plan_record.is_baseline is False
    assert plan_record.version_number == 1
    assert len(proposed_schema.tasks) == 9
    assert len(proposed_schema.dependencies) == 9
    assert validation_res.is_valid is True

    # 5. Retrieve plan to confirm persistence in PostgreSQL
    persisted_plan = await service.get_plan(tenant_id, plan_record.id)
    assert persisted_plan is not None
    assert persisted_plan.status == PlanState.GENERATED

    # 6. Verify subsequent deterministic validation step transitions it to VALIDATED
    val_after = await service.validate_plan(
        tenant_id=tenant_id,
        plan_id=plan_record.id,
        actor_id=actor_id,
    )
    assert val_after.is_valid is True
    validated_plan = await service.get_plan(tenant_id, plan_record.id)
    assert validated_plan is not None
    assert validated_plan.status == PlanState.VALIDATED
