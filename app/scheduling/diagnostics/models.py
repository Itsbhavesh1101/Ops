"""Infeasibility diagnostic and conflict explanation schemas.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Structured mapping of solver infeasibility back to canonical constraint IDs,
  activities, resources, and time windows.
- Safe, deterministic human-readable explanations (non-LLM).
- Human-review-only relaxation candidates (never applied automatically).
"""

from pydantic import BaseModel, ConfigDict, Field


class InfeasibilityDiagnostic(BaseModel):
    """Detailed diagnostic explanation for an infeasible scheduling problem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_cause_type: str = Field(
        ..., description="Categorical conflict classification identifier"
    )
    human_readable_explanation: str = Field(
        ..., description="Deterministic, safe explanation of the identified conflict"
    )
    conflicting_constraint_ids: list[str] = Field(
        default_factory=list, description="Canonical constraint IDs involved in the conflict set"
    )
    affected_activity_ids: list[str] = Field(
        default_factory=list, description="Activities involved in the identified conflict"
    )
    affected_resource_ids: list[str] = Field(
        default_factory=list, description="Resources involved in the identified conflict"
    )
    relaxation_candidates: list[str] = Field(
        default_factory=list,
        description="Suggested policy relaxations for human review only (never auto-applied)",
    )
