"""Solver result and execution metadata models.

Part of Phase 4A Generic Scheduling Core.
Encapsulates solver output, candidate assignments, score components,
and solver reproducibility metadata.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.scheduling.diagnostics.models import InfeasibilityDiagnostic
from app.scheduling.schemas import (
    CandidateAssignment,
    ScheduleScoreComponents,
    ScheduleStatus,
)


class SolverResult(BaseModel):
    """Encapsulates the complete outcome of an OR-Tools CP-SAT solve."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ScheduleStatus = Field(..., description="FEASIBLE, INFEASIBLE, or FAILED")
    assignments: list[CandidateAssignment] = Field(
        default_factory=list, description="Candidate assignments for FEASIBLE solutions"
    )
    score_components: ScheduleScoreComponents = Field(
        default_factory=ScheduleScoreComponents,
        description="Detailed objective scores and violation counts",
    )
    diagnostics: list[InfeasibilityDiagnostic] = Field(
        default_factory=list, description="Conflict diagnostics populated when status is INFEASIBLE"
    )
    solver_metadata: dict[str, Any] = Field(
        default_factory=dict, description="OR-Tools version, solve stats, seed, and profile metrics"
    )
