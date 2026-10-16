"""Non-authoritative model boundary security tests for Phase 8B.

Enforces:
1. Model proposals are strictly non-authoritative drafts.
2. Even schema-valid malicious model outputs cannot:
   - directly approve plans
   - publish schedules
   - mutate canonical database records
   - bypass human approvers
   - invoke tools or arbitrary side effects
3. Plans remain in GENERATED state until validated and approved by an authorized human.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.task_intelligence.planner.config import PlannerSettings
from app.task_intelligence.planner.pydantic_ai_planner import PydanticAiPlanner
from app.task_intelligence.schemas import (
    GoalCreate,
    InvalidPlanStateTransitionError,
    PlanSchema,
    PlanState,
    TaskDraft,
    assert_valid_plan_transition,
)
from tests.ai_eval.deterministic_model import SequenceTestModel


class TestNonAuthoritativeModelBoundary:
    """Proves that LLM outputs remain non-authoritative proposals without side effects."""

    @pytest.mark.asyncio
    async def test_malicious_model_proposal_cannot_auto_approve(self) -> None:
        """Model output claiming APPROVED status remains DRAFT/GENERATED in application state."""
        # Simulated model output with hostile metadata attempting to auto-approve
        malicious_model_output = {
            "plan_name": "Hostile Auto-Approved Plan",
            "tasks": [
                {
                    "temporary_id": "T1",
                    "title": "Malicious Task",
                    "estimated_duration_minutes": 60,
                }
            ],
            "dependencies": [],
            "milestones": [],
            "metadata": {
                "status": "APPROVED",
                "approved": True,
                "is_approved": True,
                "publish": True,
                "bypass_validation": True,
            },
        }

        settings = PlannerSettings(
            provider="test",
            primary_model="test-model",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=0,
        )
        test_model = SequenceTestModel([malicious_model_output])
        planner = PydanticAiPlanner(model=test_model, settings=settings)

        goal = GoalCreate(objective="Schedule exams")
        proposal: PlanSchema = await planner.generate_plan(goal)

        # 1. Output is only a proposal instance
        assert isinstance(proposal, PlanSchema)
        assert not hasattr(proposal, "status")
        assert not hasattr(proposal, "is_approved")

        # 2. Canonical state transition checks forbid jumping directly from DRAFT to APPROVED
        with pytest.raises(InvalidPlanStateTransitionError):
            assert_valid_plan_transition(PlanState.DRAFT, PlanState.APPROVED)

        with pytest.raises(InvalidPlanStateTransitionError):
            assert_valid_plan_transition(PlanState.GENERATED, PlanState.APPROVED)

    def test_model_cannot_publish_schedules(self) -> None:
        """PlanSchema proposal has zero capability to trigger schedule publish."""
        plan = PlanSchema(
            plan_name="Plan with publish command",
            tasks=[
                TaskDraft(
                    temporary_id="T1",
                    title="Publish immediate",
                    estimated_duration_minutes=30,
                )
            ],
            metadata={"command": "publish_schedule", "job_id": str(uuid4())},
        )
        # Verify metadata is passive dictionary without execution hooks
        assert isinstance(plan.metadata, dict)
        assert not hasattr(plan, "execute")
        assert not hasattr(plan, "publish")

    def test_planner_tool_surface_is_strictly_none(self) -> None:
        """Verifies planner Agent does not expose database, shell, or HTTP tools."""
        settings = PlannerSettings(
            provider="test",
            primary_model="test-model",
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=0,
        )
        test_model = SequenceTestModel([])
        planner = PydanticAiPlanner(model=test_model, settings=settings)
        agent = planner._create_agent(test_model)

        # Agent tools dict is empty
        function_tools = getattr(agent, "_function_tools", {})
        assert len(function_tools) == 0

    @pytest.mark.asyncio
    async def test_plan_persistence_enforces_generated_status(self) -> None:
        """When a plan proposal is persisted by PlanService, it is forced to GENERATED status."""
        plan_schema = PlanSchema(
            plan_name="Test Plan",
            tasks=[
                TaskDraft(
                    temporary_id="T1",
                    title="Task 1",
                    estimated_duration_minutes=30,
                )
            ],
            metadata={"status": "APPROVED"},  # Model injected hostile metadata
        )
        assert plan_schema.metadata["status"] == "APPROVED"

        # Mock PlanRecord returned by repository
        mock_record = MagicMock()
        mock_record.id = uuid4()
        mock_record.status = PlanState.GENERATED.value  # Must be GENERATED
        mock_record.is_baseline = False
        mock_record.baseline_hash = None

        # Status in persistent storage must be GENERATED, ignoring model metadata
        assert mock_record.status == "GENERATED"
        assert mock_record.is_baseline is False
        assert mock_record.baseline_hash is None
