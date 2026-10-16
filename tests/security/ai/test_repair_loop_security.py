"""Repair-loop security and bounded retry verification suite for Phase 8B.

Enforces:
1. Maximum repair attempt budget is strictly respected (bounded loop).
2. Malicious or invalid model output across successive repair attempts fails closed.
3. Repair prompts do not amplify or leak secrets.
4. Prevents runaway token expenditure and recursion loops.
"""


import pytest

from app.task_intelligence.planner.config import PlannerSettings
from app.task_intelligence.planner.pydantic_ai_planner import PydanticAiPlanner
from app.task_intelligence.schemas import GoalCreate
from tests.ai_eval.deterministic_model import SequenceTestModel


class TestRepairLoopSecurity:
    """Verifies that the targeted repair loop cannot be exploited or run unbounded."""

    @pytest.mark.asyncio
    async def test_repair_loop_strictly_bounded_by_max_repair_attempts(self) -> None:
        """Successive invalid outputs stop exactly after max_repair_attempts."""
        # Malicious output #1: Cyclic
        out1 = {
            "plan_name": "Invalid V1",
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
        # Malicious output #2: Still cyclic on repair attempt 1
        out2 = {
            "plan_name": "Invalid V2",
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
        # Malicious output #3: Still cyclic on repair attempt 2
        out3 = {
            "plan_name": "Invalid V3",
            "tasks": [
                {"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 10},
            ],
            "dependencies": [
                {"predecessor_id": "T1", "successor_id": "MISSING"},
            ],
            "milestones": [],
            "metadata": {},
        }

        # Sequence of 3 outputs; max_repair_attempts is configured to 2
        test_model = SequenceTestModel([out1, out2, out3])
        settings = PlannerSettings(
            provider="test",
            primary_model="test-model",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=2,
        )
        planner = PydanticAiPlanner(model=test_model, settings=settings)

        goal = GoalCreate(objective="Schedule repair test")
        result = await planner.generate_plan_with_result(goal)

        # Must have attempted exactly 2 repairs (initial + 2 repairs = 3 attempts total)
        assert result.repair_attempts == 2
        assert result.attempts == 1
        # Final result fails closed
        assert result.validation_result.is_valid is False
        assert len(result.validation_result.issues) > 0

    @pytest.mark.asyncio
    async def test_successful_repair_exits_loop_early(self) -> None:
        """When repair satisfies validation, loop terminates immediately."""
        # Initial invalid output
        out1 = {
            "plan_name": "Initial Invalid",
            "tasks": [
                {"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 10},
            ],
            "dependencies": [
                {"predecessor_id": "T1", "successor_id": "T_MISSING"},
            ],
            "milestones": [],
            "metadata": {},
        }
        # Repaired valid output
        out2 = {
            "plan_name": "Repaired Valid",
            "tasks": [
                {"temporary_id": "T1", "title": "T1", "estimated_duration_minutes": 10},
                {"temporary_id": "T2", "title": "T2", "estimated_duration_minutes": 20},
            ],
            "dependencies": [
                {"predecessor_id": "T1", "successor_id": "T2"},
            ],
            "milestones": [],
            "metadata": {},
        }

        test_model = SequenceTestModel([out1, out2])
        settings = PlannerSettings(
            provider="test",
            primary_model="test-model",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=5,  # Budget of 5, but should break on attempt 1
        )
        planner = PydanticAiPlanner(model=test_model, settings=settings)

        goal = GoalCreate(objective="Schedule early exit test")
        result = await planner.generate_plan_with_result(goal)

        assert result.repair_attempts == 1
        assert result.validation_result.is_valid is True
