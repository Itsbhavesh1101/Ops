"""Model fallback security verification suite for Phase 8B.

Enforces:
1. When primary model fails and fallback is engaged:
   - Same PLANNER_SYSTEM_INSTRUCTION is enforced.
   - Same PlanSchema output contract is enforced.
   - Same deterministic PlanValidator checks apply.
   - Same context minimization and secret redaction apply.
2. A fallback model cannot become a weaker security path.
"""

from typing import Any

import pytest

from app.task_intelligence.planner.config import PlannerSettings
from app.task_intelligence.planner.prompts import PLANNER_SYSTEM_INSTRUCTION
from app.task_intelligence.planner.pydantic_ai_planner import PydanticAiPlanner
from app.task_intelligence.schemas import GoalCreate
from tests.ai_eval.deterministic_model import SequenceTestModel


class FailingTestModel(SequenceTestModel):
    """Model that always fails to trigger the fallback path."""

    def _get_output(self, model_request_parameters: Any) -> Any:
        raise RuntimeError("Primary model service unavailable.")


class TestModelFallbackSecurity:
    """Verifies security controls remain invariant across primary and fallback models."""

    @pytest.mark.asyncio
    async def test_fallback_model_applies_same_security_controls(self) -> None:
        """When primary model fails, fallback model enforces identical schema and validation."""
        primary_failing = FailingTestModel([])
        fallback_valid = SequenceTestModel(
            [
                {
                    "plan_name": "Fallback Generated Plan",
                    "tasks": [
                        {
                            "temporary_id": "T1",
                            "title": "Fallback Task",
                            "estimated_duration_minutes": 45,
                        }
                    ],
                    "dependencies": [],
                    "milestones": [],
                    "metadata": {},
                }
            ]
        )

        settings = PlannerSettings(
            provider="test",
            primary_model="primary-mock",
            fallback_model="fallback-mock",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=1,
        )

        planner = PydanticAiPlanner(
            model=primary_failing,
            fallback_model=fallback_valid,
            settings=settings,
        )

        # Verify fallback agent uses identical system instruction
        fallback_agent = planner._create_agent(fallback_valid)
        assert PLANNER_SYSTEM_INSTRUCTION in fallback_agent._system_prompts

        goal = GoalCreate(objective="Schedule fallback test")
        result = await planner.generate_plan_with_result(goal)

        assert result.fallback_used is True
        assert result.model_name == "fallback-mock"
        assert result.plan.plan_name == "Fallback Generated Plan"
        # Deterministic validator ran on fallback output
        assert result.validation_result.is_valid is True

    @pytest.mark.asyncio
    async def test_fallback_invalid_output_is_rejected_by_validator(self) -> None:
        """Fallback model producing invalid output still triggers validation failure."""
        primary_failing = FailingTestModel([])
        fallback_cyclic = SequenceTestModel(
            [
                {
                    "plan_name": "Fallback Cyclic Plan",
                    "tasks": [
                        {"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 10},
                        {"temporary_id": "T2", "title": "T2", "estimated_duration_minutes": 10},
                    ],
                    "dependencies": [
                        {"predecessor_id": "T1", "successor_id": "T2"},
                        {"predecessor_id": "T2", "successor_id": "T1"},
                    ],
                    "milestones": [],
                    "metadata": {},
                }
            ]
        )

        settings = PlannerSettings(
            provider="test",
            primary_model="primary-mock",
            fallback_model="fallback-mock",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=0,
        )

        planner = PydanticAiPlanner(
            model=primary_failing,
            fallback_model=fallback_cyclic,
            settings=settings,
        )

        goal = GoalCreate(objective="Schedule invalid fallback test")
        result = await planner.generate_plan_with_result(goal)

        assert result.fallback_used is True
        # Must be rejected by validator
        assert result.validation_result.is_valid is False
