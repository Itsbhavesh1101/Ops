"""Validation schemas for Task Intelligence.

Defines machine-readable validation issues, severities, error codes,
and validation results.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ValidationSeverity(StrEnum):
    """Severity classification for validation issues."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ValidationIssueCode(StrEnum):
    """Deterministic, machine-readable validation codes."""

    # Schema & identity
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
    DUPLICATE_MILESTONE_ID = "DUPLICATE_MILESTONE_ID"
    EMPTY_PLAN = "EMPTY_PLAN"

    # Hierarchy
    PARENT_NOT_FOUND = "PARENT_NOT_FOUND"
    PARENT_SELF_REFERENCE = "PARENT_SELF_REFERENCE"
    PARENT_CYCLE = "PARENT_CYCLE"

    # Dependencies & Graph
    UNKNOWN_TASK_REFERENCE = "UNKNOWN_TASK_REFERENCE"
    SELF_DEPENDENCY = "SELF_DEPENDENCY"
    DUPLICATE_DEPENDENCY = "DUPLICATE_DEPENDENCY"
    CYCLE_DETECTED = "CYCLE_DETECTED"

    # Estimates & Dates
    INVALID_DURATION = "INVALID_DURATION"
    INVALID_CONFIDENCE = "INVALID_CONFIDENCE"
    DATE_INCONSISTENCY = "DATE_INCONSISTENCY"

    # Milestones & Resources
    UNKNOWN_MILESTONE_TASK = "UNKNOWN_MILESTONE_TASK"
    UNRESOLVED_RESOURCE_REFERENCE = "UNRESOLVED_RESOURCE_REFERENCE"

    # Policy & Integrity
    POLICY_VIOLATION = "POLICY_VIOLATION"


class ValidationIssue(BaseModel):
    """A single deterministic, machine-readable validation issue."""

    code: ValidationIssueCode | str = Field(
        ...,
        description="Deterministic machine-readable issue code.",
    )
    location: str = Field(
        ...,
        description=(
            "Field or entity location path (e.g., 'tasks[T1].parent_id', 'dependencies[2]')."
        ),
    )
    message: str = Field(
        ...,
        description="Safe human-readable message without raw secrets or prompts.",
    )
    severity: ValidationSeverity = Field(
        default=ValidationSeverity.ERROR,
        description="Issue severity level.",
    )
    repairable: bool = Field(
        default=False,
        description="Whether this issue can be targeted by automated AI repair in Phase 2B.",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional safe structured context (e.g. cycle node path).",
    )


class ValidationResult(BaseModel):
    """Aggregated machine-readable validation result for a PlanSchema."""

    is_valid: bool = Field(
        ...,
        description="True if and only if zero ERROR severity issues exist.",
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="List of detected validation issues.",
    )
    evaluated_task_count: int = Field(
        default=0,
        description="Total number of tasks evaluated.",
    )
    evaluated_dependency_count: int = Field(
        default=0,
        description="Total number of dependencies evaluated.",
    )
    cycle_path: list[str] | None = Field(
        default=None,
        description="Explicit cycle node path if a cycle was detected.",
    )
    topological_order: list[str] | None = Field(
        default=None,
        description="Topologically sorted task IDs if graph is acyclic.",
    )
    critical_path: list[str] | None = Field(
        default=None,
        description="List of task IDs on the critical path if CPM was computed.",
    )
