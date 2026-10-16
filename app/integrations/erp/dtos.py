"""Typed E-7 Integration DTOs for ERP/SIS data sources.

Part of Phase 7B Real ERP/SIS Adapter.
Adheres strictly to docs/integrations/MYSQL_ERP_ADAPTER_CONTRACT.md and
docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class ExternalIdentityMappingDTO(BaseModel):
    """Provider-neutral external identity mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(default="mysql_iredu")
    external_entity_type: str
    external_id: str
    canonical_entity_id: str
    tenant_id: str
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_updated_at: datetime | None = None
    source_version: str | None = None


class ErpOrganizationContextDTO(BaseModel):
    """Institutional organization metadata from ERP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    institution_name: str
    institution_code: str | None = None
    active_session_id: str
    active_session_name: str
    source_system: str = Field(default="mysql_iredu")


class ErpAcademicSessionDTO(BaseModel):
    """Academic session / calendar year boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    session_name: str
    is_active: bool


class ErpClassSectionDTO(BaseModel):
    """Cohort pairing of Class and Section (e.g. Class 10 - Section A)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    class_id: str
    class_name: str
    section_id: str
    section_name: str
    class_section_id: str


class ErpSubjectDTO(BaseModel):
    """Academic course / subject catalog entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str
    name: str
    code: str | None = None
    type: str = Field(default="theory")  # 'theory' or 'practical'


class ErpStaffDTO(BaseModel):
    """Sanitized institutional staff metadata.

    Strictly preserves the 28 PLANNER_CONTEXT_ALLOWED fields rule.
    Excludes all sensitive PII, salary, bank, and medical data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    staff_id: str
    opaque_ref: str
    name: str
    surname: str
    qualification: str | None = None
    work_exp: str | None = None
    department_name: str | None = None
    designation_name: str | None = None
    is_active: bool = True


class ErpStudentHeadcountAggregateDTO(BaseModel):
    """Cohort headcount aggregate.

    Strictly AGGREGATE ONLY: mathematical counts per cohort.
    Zero individual student rows, names, or IDs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    class_section_id: str
    class_name: str
    section_name: str
    enrolled_student_count: int
    active_student_count: int


class ErpTimetableCommitmentDTO(BaseModel):
    """Weekly recurring instructional commitment for a teacher/cohort.

    Note: raw_room_label is an unconstrained string label only (lacks physical room authority).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timetable_id: str
    staff_id: str
    subject_id: str
    class_section_id: str
    day_of_week: str
    start_time: str
    end_time: str
    raw_room_label: str | None = None


class ErpFreshnessMetadataDTO(BaseModel):
    """Tracking metadata for external ERP freshness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(default="mysql_iredu")
    tenant_id: str
    last_successful_fetch: datetime | None = None
    source_timestamp: datetime | None = None
    staleness_state: str = Field(default="FRESH")  # FRESH, STALE, DEGRADED, OFFLINE
    last_error: str | None = None
    record_counts: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "ErpAcademicSessionDTO",
    "ErpClassSectionDTO",
    "ErpFreshnessMetadataDTO",
    "ErpOrganizationContextDTO",
    "ErpStaffDTO",
    "ErpStudentHeadcountAggregateDTO",
    "ErpSubjectDTO",
    "ErpTimetableCommitmentDTO",
    "ExternalIdentityMappingDTO",
]
