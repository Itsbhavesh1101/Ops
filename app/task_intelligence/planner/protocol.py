"""Planner interface protocol for Task Intelligence.

Defines the contract between PlanService and plan generation providers.
Production implementation arrives in Phase 2B (PydanticAI).
In Phase 2A, test fixtures and deterministic planners conform to this protocol.
"""

from typing import Any, Protocol, runtime_checkable

from app.task_intelligence.schemas import GoalCreate, PlanSchema


@runtime_checkable
class PlannerProtocol(Protocol):
    """Protocol for generating structured PlanSchema proposals from goals."""

    async def generate_plan(
        self,
        goal: GoalCreate,
        context: dict[str, Any] | None = None,
    ) -> PlanSchema:
        """Decompose a high-level operational goal into a typed PlanSchema proposal.

        Args:
            goal: Validated GoalCreate intake contract.
            context: Optional authorized domain context.

        Returns:
            PlanSchema: Non-authoritative plan proposal.
        """
        ...
