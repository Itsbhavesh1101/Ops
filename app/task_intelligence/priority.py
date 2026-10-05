"""Provider-neutral priority calculation contracts and engine (FR-TI-05).

Evaluates urgency, impact, dependency criticality, and policy contribution
using explicit, caller-supplied policy weights without hardcoded defaults.
Human priority override is implemented as an explicit control/bypass path.
"""

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.task_intelligence.schemas import TaskPriority


class PriorityFactors(BaseModel):
    """Normalized input factors for priority evaluation (each bounded 0.0 to 1.0)."""

    urgency_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Temporal urgency score (e.g., proximity to deadline or low slack).",
    )
    impact_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Operational impact of task failure or delay.",
    )
    dependency_criticality_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Criticality from DAG (1.0 for critical path, or downstream dependent ratio).",
    )
    policy_contribution_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Institutional compliance or regulatory mandate weight.",
    )


class PriorityWeights(BaseModel):
    """Explicit weighting configuration for multi-criteria priority scoring.

    Weights are REQUIRED from the caller/policy configuration. Production code
    defines ZERO default numeric weights. All weights must be non-negative, finite,
    and sum exactly to 1.0 (within floating-point tolerance).
    """

    model_config = ConfigDict(populate_by_name=True)

    urgency: float = Field(
        ...,
        alias="urgency_weight",
        ge=0.0,
        description="Weight for temporal urgency factor.",
    )
    impact: float = Field(
        ...,
        alias="impact_weight",
        ge=0.0,
        description="Weight for operational impact factor.",
    )
    dependency_criticality: float = Field(
        ...,
        alias="criticality_weight",
        ge=0.0,
        description="Weight for dependency criticality factor.",
    )
    policy_contribution: float = Field(
        ...,
        alias="policy_weight",
        ge=0.0,
        description="Weight for institutional policy / compliance factor.",
    )

    @property
    def urgency_weight(self) -> float:
        return self.urgency

    @property
    def impact_weight(self) -> float:
        return self.impact

    @property
    def criticality_weight(self) -> float:
        return self.dependency_criticality

    @property
    def policy_weight(self) -> float:
        return self.policy_contribution

    @model_validator(mode="after")
    def validate_weights(self) -> "PriorityWeights":
        vals = [self.urgency, self.impact, self.dependency_criticality, self.policy_contribution]
        for v in vals:
            if not math.isfinite(v):
                raise ValueError("Priority weights must be finite numbers.")
        total = sum(vals)
        if total <= 0.0:
            raise ValueError("Sum of priority weights must be greater than zero.")
        if not math.isclose(total, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError(f"Sum of priority weights must equal 1.0 (got {total}).")
        return self


# Canonical alias for explicit policy object naming
PriorityPolicy = PriorityWeights


class PriorityEvaluationResult(BaseModel):
    """Result of multi-criteria priority scoring."""

    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite priority score between 0.0 and 1.0.",
    )
    priority: TaskPriority = Field(
        ...,
        description="Mapped canonical TaskPriority enum.",
    )
    factors: PriorityFactors = Field(
        ...,
        description="Input factors used.",
    )
    weights: PriorityWeights = Field(
        ...,
        description="Weights applied.",
    )
    is_overridden: bool = Field(
        default=False,
        description="True if priority was set via human override control path.",
    )
    human_override: TaskPriority | None = Field(
        default=None,
        description="Explicit human override if applied.",
    )


class PriorityEngine:
    """Calculates task priority deterministically using caller-supplied policy weights.

    Zero hardcoded production default weights exist. Weights are a required input.
    Human priority override is an explicit bypass/control path, NOT a weighted feature.
    """

    @staticmethod
    def calculate(
        factors: PriorityFactors,
        weights: PriorityWeights,
        human_override: TaskPriority | None = None,
    ) -> PriorityEvaluationResult:
        """Compute composite priority score and map to TaskPriority enum.

        Args:
            factors: Normalized input factors [0.0, 1.0].
            weights: Explicit caller-provided policy weights (REQUIRED, must sum to 1.0).
            human_override: Optional explicit human priority override. If provided,
                bypasses computed priority and directly assigns the specified TaskPriority.

        Returns:
            PriorityEvaluationResult with composite score and mapped enum.
        """
        composite = (
            factors.urgency_score * weights.urgency
            + factors.impact_score * weights.impact
            + factors.dependency_criticality_score * weights.dependency_criticality
            + factors.policy_contribution_score * weights.policy_contribution
        )

        composite_score = max(0.0, min(1.0, composite))

        if human_override is not None:
            mapped_priority = human_override
            is_overridden = True
        else:
            is_overridden = False
            if composite_score >= 0.75:
                mapped_priority = TaskPriority.CRITICAL
            elif composite_score >= 0.50:
                mapped_priority = TaskPriority.HIGH
            elif composite_score >= 0.25:
                mapped_priority = TaskPriority.MEDIUM
            else:
                mapped_priority = TaskPriority.LOW

        return PriorityEvaluationResult(
            score=composite_score,
            priority=mapped_priority,
            factors=factors,
            weights=weights,
            is_overridden=is_overridden,
            human_override=human_override,
        )
