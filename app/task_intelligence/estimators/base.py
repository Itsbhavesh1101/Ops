"""Base interfaces and data structures for effort estimation."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class EffortEstimateResult(BaseModel):
    """Result of an effort estimation calculation."""

    value: float = Field(
        ...,
        ge=0.0,
        description="Duration magnitude.",
    )
    unit: str = Field(
        default="minutes",
        description="Duration unit (normalized to 'minutes').",
    )
    basis: str = Field(
        ...,
        description=(
            "Basis of the estimate "
            "(e.g., 'human_override', 'category_heuristic', 'historical_median')."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score bounded between 0.0 and 1.0.",
    )
    is_manual_override: bool = Field(
        default=False,
        description="True if manually specified by human planner.",
    )


@runtime_checkable
class HistoricalMedianProviderProtocol(Protocol):
    """Provider-neutral interface for historical effort statistics."""

    def get_historical_median_duration(
        self,
        work_category: str,
        task_type: str | None = None,
    ) -> float | None:
        """Return historical median duration in minutes, or None if unavailable."""
        ...
