"""SQLAlchemy 2.x mapped models for canonical Phase-4A generic scheduling core.

Part of Phase 4A Generic Scheduling Core.
Maps:
- resources: Canonical resource registry
- resource_availabilities: Timezone-aware availability and blackout intervals
- constraint_definitions: Versioned constraint definitions
- solver_profiles: Versioned solver configuration profiles
- schedule_jobs: Asynchronous solver job tracking and worker leases
- schedules: Canonical candidate/published schedule records
- schedule_assignments: Discrete activity-to-resource time slot assignments
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ResourceRecord(Base):
    """Canonical E-7 resource entity mapped to the resources table."""

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "resource_key", name="uq_resources_tenant_key"),
        CheckConstraint("capacity >= 0", name="ck_resources_capacity_non_negative"),
        CheckConstraint(
            "type IN ('PERSON', 'ROOM', 'EQUIPMENT', 'OTHER')",
            name="ck_resources_type_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resource_key: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_label: Mapped[str] = mapped_column(String(255), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    availabilities: Mapped[list["ResourceAvailabilityRecord"]] = relationship(
        "ResourceAvailabilityRecord", back_populates="resource", cascade="all, delete-orphan"
    )


class ResourceAvailabilityRecord(Base):
    """Timezone-aware availability or blackout interval for a resource."""

    __tablename__ = "resource_availabilities"
    __table_args__ = (
        CheckConstraint("end_at >= start_at", name="ck_resource_availabilities_order"),
        CheckConstraint(
            "interval_type IN ('AVAILABLE', 'UNAVAILABLE')",
            name="ck_resource_availabilities_type_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resource_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_type: Mapped[str] = mapped_column(String(32), nullable=False, default="UNAVAILABLE")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    resource: Mapped["ResourceRecord"] = relationship(
        "ResourceRecord", back_populates="availabilities"
    )


class ConstraintDefinitionRecord(Base):
    """Versioned constraint definitions mapped to the constraint_definitions table."""

    __tablename__ = "constraint_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "constraint_key", "version", name="uq_constraint_def_tenant_key_ver"
        ),
        CheckConstraint("version > 0", name="ck_constraint_def_version_positive"),
        CheckConstraint("hardness IN ('HARD', 'SOFT')", name="ck_constraint_def_hardness_valid"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    constraint_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    constraint_type: Mapped[str] = mapped_column(String(32), nullable=False)
    hardness: Mapped[str] = mapped_column(String(16), nullable=False, default="HARD")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SolverProfileRecord(Base):
    """Versioned solver configuration profiles mapped to solver_profiles table."""

    __tablename__ = "solver_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "profile_key", "version", name="uq_solver_profile_tenant_key_ver"
        ),
        CheckConstraint("version > 0", name="ck_solver_profile_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    profile_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScheduleJobRecord(Base):
    """Asynchronous solve job tracking and worker lease record."""

    __tablename__ = "schedule_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED', 'SOLVING', 'FEASIBLE', "
            "'INFEASIBLE', 'BUDGET_EXHAUSTED', 'FAILED')",
            name="ck_schedule_jobs_status_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schedule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED", index=True)

    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    solver_profile_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("solver_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    solver_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScheduleRecord(Base):
    """Canonical schedule record projected from a solve job."""

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "schedule_key", "version", name="uq_schedules_tenant_key_ver"
        ),
        CheckConstraint("version > 0", name="ck_schedules_version_positive"),
        CheckConstraint(
            "status IN ('DRAFT', 'FEASIBLE', 'REVIEW', 'PUBLISHED', 'SUPERSEDED')",
            name="ck_schedules_status_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schedule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="FEASIBLE")

    source_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedule_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    solver_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solver_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repair_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repair_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    assignments: Mapped[list["ScheduleAssignmentRecord"]] = relationship(
        "ScheduleAssignmentRecord", back_populates="schedule", cascade="all, delete-orphan"
    )
    calendar_links: Mapped[list["CalendarLinkRecord"]] = relationship(
        "CalendarLinkRecord", back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduleAssignmentRecord(Base):
    """Individual activity time and resource assignment within a schedule."""

    __tablename__ = "schedule_assignments"
    __table_args__ = (
        CheckConstraint("end_at >= start_at", name="ck_schedule_assignments_order"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schedule_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    schedule: Mapped["ScheduleRecord"] = relationship(
        "ScheduleRecord", back_populates="assignments"
    )
    calendar_links: Mapped[list["CalendarLinkRecord"]] = relationship(
        "CalendarLinkRecord", back_populates="assignment", cascade="all, delete-orphan"
    )


class CalendarLinkRecord(Base):
    """Secondary mapping table linking canonical E-7 schedule assignments to external calendars."""

    __tablename__ = "calendar_links"
    __table_args__ = (
        Index(
            "uq_calendar_links_scope_ext_event",
            "tenant_id",
            "provider",
            "external_calendar_id",
            "external_event_id",
            unique=True,
            postgresql_where=text("link_kind = 'EVENT' AND external_event_id IS NOT NULL"),
        ),
        Index(
            "uq_calendar_links_cursor",
            "tenant_id",
            "provider",
            "external_calendar_id",
            unique=True,
            postgresql_where=text("link_kind = 'SYNC_CURSOR'"),
        ),
        Index(
            "ix_calendar_links_tenant_key_activity",
            "tenant_id",
            "schedule_key",
            "activity_id",
        ),
        UniqueConstraint(
            "tenant_id", "assignment_id", "provider", name="uq_calendar_links_tenant_assign_prov"
        ),
        CheckConstraint(
            "sync_status IN ('PENDING', 'SYNCED', 'FAILED', 'DEGRADED', 'DELETED', 'CONFLICT')",
            name="ck_calendar_links_sync_status_valid",
        ),
        CheckConstraint(
            "link_kind IN ('EVENT', 'SYNC_CURSOR')",
            name="ck_calendar_links_link_kind_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schedule_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedules.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedule_assignments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    activity_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    schedule_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    link_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="EVENT")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="google")
    external_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    sync_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    schedule: Mapped["ScheduleRecord | None"] = relationship(
        "ScheduleRecord", back_populates="calendar_links"
    )
    assignment: Mapped["ScheduleAssignmentRecord | None"] = relationship(
        "ScheduleAssignmentRecord", back_populates="calendar_links"
    )

