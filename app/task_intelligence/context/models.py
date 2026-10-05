"""Context Builder domain models and contracts (Phase 2B).

Conforms strictly to Phase-0 CONTEXT_BUILDER_CONTRACT.json (E7-CTR-CB-001).
Enforces the 7 supported categories, exact 4 assembly statuses, and strict
separation between model-visible and validation-only context.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AssemblyStatus(StrEnum):
    """Provider-neutral operational assembly outcome enum."""

    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    REQUIRES_CLARIFICATION = "REQUIRES_CLARIFICATION"


class ContextCategory(StrEnum):
    """The exactly 7 frozen supported context categories."""

    ORGANIZATION_CONTEXT = "organization_context"
    ACADEMIC_CONTEXT = "academic_context"
    SUBJECT_CONTEXT = "subject_context"
    STAFF_RESOURCE_CONTEXT = "staff_resource_context"
    COMMITMENT_CONTEXT = "commitment_context"
    CALENDAR_CONTEXT = "calendar_context"
    AGGREGATE_CONTEXT = "aggregate_context"


class ProvenanceRecord(BaseModel):
    """Audit metadata recording source systems, entities, and freshness."""

    source_system: str = Field(
        ...,
        description="External source system identifier (e.g., mysql_iredu, synthetic)",
    )
    entity_name: str = Field(..., description="Source entity/table name")
    record_count: int = Field(default=0, description="Number of source records evaluated")
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of context extraction",
    )
    freshness_class: str = Field(default="STATIC_OR_SLOW", description="Freshness classification")
    mapping_version: str = Field(default="1.2.0", description="Source mapping version applied")


class OmissionRecord(BaseModel):
    """Audit record of omitted fields or categories with governance rationale."""

    field_path: str = Field(..., description="Entity or field path that was omitted")
    reason: str = Field(..., description="Policy or privacy rationale for omission")
    category: ContextCategory | str = Field(
        ...,
        description="Context category associated with omission",
    )


class ModelVisibleOrganization(BaseModel):
    """Planner-visible organization context (2 allowed fields)."""

    department_name: str | None = None
    designation: str | None = None


class ModelVisibleAcademic(BaseModel):
    """Planner-visible academic hierarchy (3 allowed fields)."""

    session: str | None = None
    grade_level: str | None = Field(
        default=None,
        validation_alias="class",
        serialization_alias="class",
    )
    section: str | None = None

    model_config = {"populate_by_name": True}


class ModelVisibleSubject(BaseModel):
    """Planner-visible subject and curricular grouping (6 allowed fields)."""

    subject_name: str | None = None
    subject_type: str | None = None  # Theory / Practical
    subject_code: str | None = None
    subject_group_name: str | None = None
    subject_group_description: str | None = None
    curricular_assignment_description: str | None = None


class ModelVisibleStaff(BaseModel):
    """Planner-visible staff resource context (4 allowed fields).

    Uses opaque provider-neutral reference tokens.
    Descriptive attributes do NOT prove task eligibility, capability, or invigilator suitability.
    """

    opaque_staff_ref: str = Field(..., description="Provider-neutral opaque token, e.g. StaffRef_1")
    qualification: str | None = None
    work_exp: str | None = None
    sanitized_display_name: str | None = None  # sanitized or obfuscated name under policy


class ModelVisibleCommitment(BaseModel):
    """Planner-visible timetable commitment window (5 allowed fields).

    Scheduled period = known busy commitment; absence of timetable != complete availability.
    """

    day: str | None = None
    time_from: str | None = None
    time_to: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class ModelVisibleCalendar(BaseModel):
    """Planner-visible holiday and academic event entries (8 allowed fields).

    Informational date context only; does NOT constitute automated hard constraint.
    """

    holiday_from_date: str | None = None
    holiday_to_date: str | None = None
    holiday_description: str | None = None
    event_title: str | None = None
    event_description: str | None = None
    event_start_date: str | None = None
    event_end_date: str | None = None
    event_type: str | None = None


class ModelContext(BaseModel):
    """Sanitized, minimized domain data permitted for model exposure.

    Bounded strictly to goal scope, whitelisted fields, opaque references, and sanitized text.
    Contains NO validation-only fields, NO raw database primary keys, NO individual student PII.
    """

    organization: list[ModelVisibleOrganization] = Field(default_factory=list)
    academic: list[ModelVisibleAcademic] = Field(default_factory=list)
    subjects: list[ModelVisibleSubject] = Field(default_factory=list)
    staff_resources: list[ModelVisibleStaff] = Field(default_factory=list)
    commitments: list[ModelVisibleCommitment] = Field(default_factory=list)
    calendar_entries: list[ModelVisibleCalendar] = Field(default_factory=list)
    cohort_student_headcounts: dict[str, int] = Field(
        default_factory=dict,
        description="Aggregate cohort counts only (e.g. {'Class 10 - Section A': 42})",
    )


class ValidationContext(BaseModel):
    """Internal evidence for deterministic validators.

    Strictly isolated from model prompts. Holds raw upstream keys, relational bindings,
    and institutional policies required for post-model deterministic verification.
    """

    raw_entity_id_map: dict[str, Any] = Field(
        default_factory=dict,
        description="Mapping from opaque reference to upstream entity identifiers",
    )
    authorized_resource_ids: set[str] = Field(
        default_factory=set,
        description="Authorized resource ID whitelist for deterministic validation",
    )
    source_work_categories: list[str] = Field(
        default_factory=list,
        description="Allowed work categories for plan verification",
    )
    session_id: int | str | None = None
    class_section_ids: list[int | str] = Field(default_factory=list)
    raw_timetable_records: list[dict[str, Any]] = Field(default_factory=list)
    raw_calendar_records: list[dict[str, Any]] = Field(default_factory=list)


class ContextAssemblyRequest(BaseModel):
    """Input parameters requesting context assembly for an operational goal."""

    request_id: UUID = Field(default_factory=uuid4)
    correlation_id: str | None = None
    tenant_id: str = Field(
        ...,
        description="Authoritative tenant/organization scope partition (BLK-01)",
    )
    goal_id: UUID | None = None
    objective: str = Field(..., description="Goal objective bounding the retrieval scope")
    requested_categories: list[ContextCategory] = Field(
        default_factory=lambda: list(ContextCategory),
        description="Explicit categories requested",
    )


class ContextAssemblyResult(BaseModel):
    """Canonical Context Builder output conforming to E7-CTR-CB-001."""

    assembly_status: AssemblyStatus = Field(..., description="Operational assembly outcome")
    model_context: ModelContext = Field(
        default_factory=ModelContext,
        description="Sanitized data safe for model prompt intake",
    )
    validation_context: ValidationContext = Field(
        default_factory=ValidationContext,
        description="Deterministic validation evidence; strictly isolated from model",
    )
    aggregates: dict[str, Any] = Field(
        default_factory=dict,
        description="Approved mathematical headcount calculations (e.g. cohort headcounts)",
    )
    provenance: list[ProvenanceRecord] = Field(
        default_factory=list,
        description="Audit ledger of source evidence",
    )
    omissions: list[OmissionRecord] = Field(
        default_factory=list,
        description="Explicit ledger of omitted/sanitized fields",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Structured diagnostic alerts detailing non-fatal degradations",
    )
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="Ambiguities or conflicts requiring human/administrative clarification",
    )
