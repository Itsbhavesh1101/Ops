"""Provider-neutral Education Scheduling DTOs and Data Models.

Part of Phase 5 Education Scheduling PoC.
These models reside outside the generic database schema (no migration 0013)
and provide a clean, provider-neutral representation for academic timetabling,
examination scheduling, cohort synchronization, and invigilator rostering.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GapScope(StrEnum):
    """Scope of exam spacing policies."""

    PER_COHORT = "PER_COHORT"
    GLOBAL = "GLOBAL"


class ExamGapPolicy(BaseModel):
    """Policy governing separation between examinations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: GapScope = Field(default=GapScope.PER_COHORT, description="Cohort or global scope")
    minimum_clear_calendar_days_between: int = Field(
        default=1, ge=0, description="Minimum full calendar days required between major exams"
    )
    is_hard: bool = Field(
        default=True, description="Whether this gap is a mandatory hard constraint"
    )


class TimeWindowDTO(BaseModel):
    """Timezone-aware scheduling window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_at: datetime = Field(..., description="Window start (timezone-aware)")
    end_at: datetime = Field(..., description="Window end (timezone-aware)")

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Time window timestamps must be timezone-aware.")
        return v

    @field_validator("end_at")
    @classmethod
    def validate_order(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("start_at")
        if start is not None and v <= start:
            raise ValueError(f"end_at ({v}) must be strictly after start_at ({start}).")
        return v


class StudentCohortDTO(BaseModel):
    """Student group / cohort taking courses or exams together."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, description="Unique cohort ID (e.g. cohort-g10-a)")
    display_label: str = Field(..., min_length=1, description="Human-readable label")
    student_count: int = Field(..., gt=0, description="Number of students in the cohort")


class AcademicSubjectDTO(BaseModel):
    """Course or examination subject definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, description="Subject/Exam ID")
    title: str = Field(..., min_length=1, description="Subject title")
    duration_minutes: int = Field(..., gt=0, description="Duration in minutes")
    major_subject: bool = Field(
        default=False, description="True if major subject subject to gap policies"
    )
    required_invigilator_skills: list[str] = Field(
        default_factory=list, description="Mandatory invigilation skills"
    )
    preferred_invigilator_skills: list[str] = Field(
        default_factory=list, description="Preferred subject-matter skills"
    )
    required_equipment: list[str] = Field(
        default_factory=list, description="Mandatory equipment skills/types required"
    )


class EducationSessionDTO(BaseModel):
    """Concrete activity instance requiring time, room, and invigilator allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, description="Session ID (e.g. session-math-g10a)")
    exam_id: str = Field(..., min_length=1, description="Reference to AcademicSubjectDTO")
    cohort_id: str = Field(..., min_length=1, description="Reference to StudentCohortDTO")
    minimum_room_capacity: int = Field(..., gt=0, description="Minimum seating required")
    allowed_windows: list[TimeWindowDTO] = Field(
        ..., min_length=1, description="Valid half-open scheduling windows"
    )
    preferred_room_ids: list[str] = Field(
        default_factory=list, description="Ordered or prioritized preferred room IDs"
    )
    preferred_invigilator_ids: list[str] = Field(
        default_factory=list, description="Preferred faculty/invigilator IDs"
    )
    required_equipment: list[str] = Field(
        default_factory=list,
        description="Mandatory equipment skills/types required for this session",
    )


class CanonicalEducationAssignment(BaseModel):
    """Evaluated or scheduled assignment in canonical E-7 terms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str = Field(..., description="Assignment identifier")
    session_id: str = Field(..., description="Canonical session ID")
    room_resource_id: str = Field(..., description="Assigned room resource ID")
    invigilator_resource_id: str = Field(..., description="Assigned invigilator resource ID")
    equipment_resource_id: str | None = Field(
        default=None, description="Assigned equipment resource ID, if equipment required"
    )
    start_at: datetime = Field(..., description="Scheduled start (timezone-aware)")
    end_at: datetime = Field(..., description="Scheduled end (timezone-aware)")
    locked: bool = Field(default=False, description="True if assignment is pinned/locked")

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Assignment timestamps must be timezone-aware.")
        return v


class SoftPreferenceSpec(BaseModel):
    """Specification of soft optimization objectives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(..., description="Objective kind, e.g. PREFERRED_INVIGILATOR, PREFERRED_ROOM")
    weight: int = Field(default=1, ge=0, description="Relative penalty weight")


class EducationBenchmarkDataset(BaseModel):
    """Complete, normalized, provider-neutral education scheduling benchmark."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(..., description="Canonical benchmark dataset ID")
    dataset_version: str = Field(..., description="Semantic version string")
    tenant_id: str = Field(..., description="Tenant isolation ID")
    timezone: str = Field(..., description="Institution timezone (e.g. Asia/Kolkata)")
    horizon_start: datetime = Field(..., description="Horizon start timestamp")
    horizon_end: datetime = Field(..., description="Horizon end timestamp")
    cohorts: list[StudentCohortDTO] = Field(..., min_length=1)
    exams: list[AcademicSubjectDTO] = Field(..., min_length=1)
    sessions: list[EducationSessionDTO] = Field(..., min_length=1)
    gap_policy: ExamGapPolicy = Field(default_factory=ExamGapPolicy)
    soft_preferences: list[SoftPreferenceSpec] = Field(default_factory=list)
    published_baseline: list[CanonicalEducationAssignment] = Field(default_factory=list)
    locked_session_ids: list[str] = Field(default_factory=list)
