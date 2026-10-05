"""Typed Intermediate Representation (IR) models for graph and CPM computation.

Decouples internal graph algorithms from external API and database representations.
"""

from pydantic import BaseModel, Field


class CycleDetectionResult(BaseModel):
    """Result of cycle detection on a directed dependency graph."""

    has_cycle: bool = Field(
        ...,
        description="True if a cycle exists in the graph.",
    )
    cycle_path: list[str] | None = Field(
        default=None,
        description=(
            "Explicit cycle path returning to the starting node (e.g., ['A', 'B', 'C', 'A'])."
        ),
    )


class CPMScheduleNode(BaseModel):
    """Critical Path Method timing metrics for an individual task."""

    task_id: str = Field(
        ...,
        description="Task identifier.",
    )
    duration_minutes: int = Field(
        ...,
        ge=0,
        description="Task duration in normalized minutes.",
    )
    early_start: int = Field(
        ...,
        ge=0,
        description="Earliest possible start time in minutes from project baseline (0).",
    )
    early_finish: int = Field(
        ...,
        ge=0,
        description="Earliest possible finish time (early_start + duration).",
    )
    late_start: int = Field(
        ...,
        ge=0,
        description="Latest possible start time without delaying project completion.",
    )
    late_finish: int = Field(
        ...,
        ge=0,
        description="Latest possible finish time (late_start + duration).",
    )
    total_slack: int = Field(
        ...,
        ge=0,
        description="Total float/slack (late_start - early_start). Zero indicates critical path.",
    )
    free_slack: int = Field(
        default=0,
        ge=0,
        description="Free float: delay possible without impacting earliest start of any successor.",
    )
    is_critical: bool = Field(
        ...,
        description="True if task has zero total slack and is on the critical path.",
    )


class CPMScheduleResult(BaseModel):
    """Aggregate result of Critical Path Method calculation."""

    project_duration_minutes: int = Field(
        ...,
        ge=0,
        description="Total minimum project duration in minutes required by the critical path.",
    )
    schedule: dict[str, CPMScheduleNode] = Field(
        ...,
        description="Map of task_id to CPM timing metrics.",
    )
    critical_path: list[str] = Field(
        ...,
        description=(
            "Deterministic representative sequence of tasks forming the critical path "
            "from start to finish."
        ),
    )
    critical_paths: list[list[str]] = Field(
        default_factory=list,
        description="All critical path sequences if multiple parallel critical paths exist.",
    )
