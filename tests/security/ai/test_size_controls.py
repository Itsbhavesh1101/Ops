"""Prompt and response size control security test suite for Phase 8B.

Enforces:
1. Bounded objective text length (max 1000 chars) on GoalCreate.
2. Bounded scope text length (max 2000 chars) on GoalCreate.
3. Bounded task title and ID lengths (max 255 chars title, max 64 chars ID).
4. Rejection of oversized inputs preventing prompt buffer and memory exhaustion.
"""

import pytest
from pydantic import ValidationError

from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
    TaskDraft,
)


class TestPromptAndResponseSizeControls:
    """Verifies defensive bounds against oversized prompts and responses."""

    def test_oversized_goal_objective_rejected(self) -> None:
        """Goal objective exceeding 1000 characters is rejected at schema boundary."""
        oversized_obj = "A" * 1001
        with pytest.raises(ValidationError) as exc:
            GoalCreate(objective=oversized_obj)
        assert "String should have at most 1000 characters" in str(exc.value)

    def test_oversized_goal_scope_rejected(self) -> None:
        """Goal scope exceeding 2000 characters is rejected at schema boundary."""
        oversized_scope = "B" * 2001
        with pytest.raises(ValidationError) as exc:
            GoalCreate(objective="Valid Objective", scope=oversized_scope)
        assert "String should have at most 2000 characters" in str(exc.value)

    def test_oversized_task_temporary_id_rejected(self) -> None:
        """Task temporary_id exceeding 64 characters is rejected."""
        oversized_id = "T_" + "x" * 65
        with pytest.raises(ValidationError) as exc:
            TaskDraft(
                temporary_id=oversized_id,
                title="Task",
                estimated_duration_minutes=30,
            )
        assert "String should have at most 64 characters" in str(exc.value)

    def test_oversized_task_title_rejected(self) -> None:
        """Task title exceeding 255 characters is rejected."""
        oversized_title = "T" * 256
        with pytest.raises(ValidationError) as exc:
            TaskDraft(
                temporary_id="T1",
                title=oversized_title,
                estimated_duration_minutes=30,
            )
        assert "String should have at most 255 characters" in str(exc.value)

    def test_oversized_plan_name_rejected(self) -> None:
        """Plan name exceeding 255 characters is rejected."""
        oversized_name = "P" * 256
        with pytest.raises(ValidationError) as exc:
            PlanSchema(
                plan_name=oversized_name,
                tasks=[
                    TaskDraft(
                        temporary_id="T1",
                        title="Task 1",
                        estimated_duration_minutes=30,
                    )
                ],
            )
        assert "String should have at most 255 characters" in str(exc.value)
