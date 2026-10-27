"""SQLAlchemy 2.x mapped models for canonical planning domain (Phase 2A).

Implements canonical System of Record persistence for:
- goals
- plans (with native versioning and approved baseline support)
- tasks
- task_dependencies
- milestones
- task_risks

Invariants:
- All entities are tenant-scoped via tenant_id.
- Application-generated canonical UUID primary keys.
- Strict database check constraints for state values, durations, percentages, and non-self edges.
- No PostgreSQL native ENUM types (standard VARCHAR + CHECK constraints).
- Single-database ACID participation with audit and outbox.
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class GoalRecord(Base):
    """Canonical operational goal entity mapped to PostgreSQL goals."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint("length(trim(objective)) > 0", name="goal_objective_not_blank"),
        Index("ix_goals_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    objective: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )
    scope: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    target_window: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    constraints: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    resource_scope: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    context_references: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    priority_target: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    plans: Mapped[list["PlanRecord"]] = relationship(
        "PlanRecord",
        back_populates="goal",
        cascade="all, delete-orphan",
    )


class PlanRecord(Base):
    """Canonical versioned plan entity mapped to PostgreSQL plans.

    Supports native plan versioning (goal_id, version_number) and
    approved baseline freezing (is_baseline=True, status=APPROVED).
    """

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("version_number >= 1", name="plan_version_number_positive"),
        CheckConstraint(
            "status IN ('DRAFT', 'GENERATED', 'VALIDATED', 'APPROVAL_PENDING', "
            "'APPROVED', 'ACTIVE', 'COMPLETED', 'CANCELLED')",
            name="plan_status_valid",
        ),
        UniqueConstraint(
            "tenant_id", "goal_id", "version_number", name="uq_plans_tenant_goal_version"
        ),
        Index("ix_plans_tenant_id_goal_id", "tenant_id", "goal_id"),
        Index("ix_plans_tenant_id_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    goal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("goals.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    plan_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="DRAFT",
    )
    is_baseline: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    superseded_by_plan_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    baseline_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    goal: Mapped["GoalRecord"] = relationship("GoalRecord", back_populates="plans")
    tasks: Mapped[list["TaskRecord"]] = relationship(
        "TaskRecord",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    dependencies: Mapped[list["TaskDependencyRecord"]] = relationship(
        "TaskDependencyRecord",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    milestones: Mapped[list["MilestoneRecord"]] = relationship(
        "MilestoneRecord",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    risks: Mapped[list["TaskRiskRecord"]] = relationship(
        "TaskRiskRecord",
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class MilestoneRecord(Base):
    """Canonical milestone entity mapped to PostgreSQL milestones."""

    __tablename__ = "milestones"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "plan_id", "temporary_id", name="uq_milestones_tenant_plan_temporary_id"
        ),
        Index("ix_milestones_tenant_id_plan_id", "tenant_id", "plan_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    temporary_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    target_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    plan: Mapped["PlanRecord"] = relationship("PlanRecord", back_populates="milestones")
    tasks: Mapped[list["TaskRecord"]] = relationship("TaskRecord", back_populates="milestone")


class TaskRecord(Base):
    """Canonical WBS task entity mapped to PostgreSQL tasks."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("estimated_duration_minutes >= 0", name="task_duration_non_negative"),
        CheckConstraint(
            "actual_duration_minutes IS NULL OR actual_duration_minutes >= 0",
            name="task_actual_duration_non_negative",
        ),
        CheckConstraint(
            "progress_percentage >= 0 AND progress_percentage <= 100",
            name="task_progress_percentage_bounds",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="task_priority_valid",
        ),
        CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED')",
            name="task_status_valid",
        ),
        UniqueConstraint(
            "tenant_id", "plan_id", "temporary_id", name="uq_tasks_tenant_plan_temporary_id"
        ),
        Index("ix_tasks_tenant_id_plan_id", "tenant_id", "plan_id"),
        Index("ix_tasks_tenant_id_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    temporary_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    parent_task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    wbs_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    owner_role: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    owner_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    estimated_duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
    )
    effort_estimate_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    work_category: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    priority: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="MEDIUM",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="TODO",
    )
    progress_percentage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    actual_duration_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    blocked_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    milestone_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("milestones.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def variance_minutes(self) -> int | None:
        """Variance between actual execution effort and estimated duration in minutes."""
        if self.actual_duration_minutes is None:
            return None
        return self.actual_duration_minutes - self.estimated_duration_minutes

    @property
    def completion_evidence(self) -> str | None:
        """Completion evidence, stored persistently in JSONB effort_estimate_json."""
        if self.effort_estimate_json and isinstance(self.effort_estimate_json, dict):
            val = self.effort_estimate_json.get("completion_evidence")
            return str(val) if val is not None else None
        return None

    @completion_evidence.setter
    def completion_evidence(self, val: str | None) -> None:
        data = (
            dict(self.effort_estimate_json)
            if isinstance(self.effort_estimate_json, dict)
            else {}
        )
        if val is not None:
            data["completion_evidence"] = val
        else:
            data.pop("completion_evidence", None)
        self.effort_estimate_json = data

    plan: Mapped["PlanRecord"] = relationship("PlanRecord", back_populates="tasks")
    milestone: Mapped["MilestoneRecord | None"] = relationship(
        "MilestoneRecord", back_populates="tasks"
    )
    risks: Mapped[list["TaskRiskRecord"]] = relationship(
        "TaskRiskRecord",
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskDependencyRecord(Base):
    """Canonical task dependency edge mapped to PostgreSQL task_dependencies."""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint("predecessor_id <> successor_id", name="dependency_no_self_edge"),
        CheckConstraint("lag_minutes >= 0", name="dependency_lag_non_negative"),
        CheckConstraint(
            "dependency_type IN ("
            "'FINISH_TO_START', 'START_TO_START', 'FINISH_TO_FINISH', 'START_TO_FINISH'"
            ")",
            name="dependency_type_valid",
        ),
        UniqueConstraint(
            "tenant_id",
            "plan_id",
            "predecessor_id",
            "successor_id",
            name="uq_task_dependencies_tenant_plan_pred_succ",
        ),
        Index("ix_task_dependencies_tenant_id_plan_id", "tenant_id", "plan_id"),
        Index("ix_task_dependencies_predecessor_id", "predecessor_id"),
        Index("ix_task_dependencies_successor_id", "successor_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    predecessor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    successor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    dependency_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="FINISH_TO_START",
    )
    lag_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    plan: Mapped["PlanRecord"] = relationship("PlanRecord", back_populates="dependencies")


class TaskRiskRecord(Base):
    """Canonical task operational risk entity mapped to PostgreSQL task_risks."""

    __tablename__ = "task_risks"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="task_risk_severity_valid",
        ),
        Index("ix_task_risks_tenant_id_plan_id", "tenant_id", "plan_id"),
        Index("ix_task_risks_task_id", "task_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_evaluable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    plan: Mapped["PlanRecord"] = relationship("PlanRecord", back_populates="risks")
    task: Mapped["TaskRecord"] = relationship("TaskRecord", back_populates="risks")
