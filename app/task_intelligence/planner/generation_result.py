"""IR-owned generation result contract for Task Intelligence (Phase 2B).

Records safe metadata without exposing private credentials, raw HTTP bodies,
or internal reasoning traces.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.task_intelligence.schemas import PlanSchema, ValidationResult


class PlannerGenerationResult(BaseModel):
    """Safe execution and provenance record of an AI plan generation run."""

    plan: PlanSchema = Field(..., description="Generated or repaired PlanSchema proposal")
    model_name: str = Field(..., description="Provider-neutral model identifier")
    provider: str = Field(..., description="Provider abstraction classification")
    attempts: int = Field(default=1, description="Total transport/model calls made")
    repair_attempts: int = Field(default=0, description="Total targeted repair loops executed")
    fallback_used: bool = Field(
        default=False,
        description="Whether fallback provider/model was invoked",
    )
    structured_output_success: bool = Field(
        default=True,
        description="Whether schema parsed cleanly",
    )
    latency_ms: float = Field(default=0.0, description="Total elapsed runtime in milliseconds")
    usage: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe numeric token usage if reported",
    )
    validation_result: ValidationResult | None = Field(
        default=None,
        description="Machine-readable deterministic validation outcome",
    )
