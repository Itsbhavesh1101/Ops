"""SQLAlchemy 2.x mapped models for canonical workflow domain (Phase 3A).

Implements canonical System of Record persistence for:
- workflow_definitions (versioned BPMN definition metadata)
- workflow_instances (canonical process instance projection)
- workflow_task_mappings (human user task mapping and authorization state)

Invariants:
- All entities are tenant-scoped via tenant_id.
- Application-generated canonical UUID primary keys.
- Flowable native execution IDs are strictly secondary external references.
- No Flowable internal schema copied; zero Liquibase ACT_* tables managed here.
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


class WorkflowDefinitionRecord(Base):
    """Canonical workflow definition entity mapped to PostgreSQL workflow_definitions."""

    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "workflow_key", "version", name="uq_workflow_defs_tenant_key_version"
        ),
        CheckConstraint("version > 0", name="ck_workflow_def_version_positive"),
        CheckConstraint("length(trim(workflow_key)) > 0", name="ck_workflow_def_key_not_blank"),
        CheckConstraint(
            "length(trim(bpmn_process_id)) > 0", name="ck_workflow_def_process_id_not_blank"
        ),
        Index("ix_workflow_defs_tenant_key", "tenant_id", "workflow_key"),
        Index("ix_workflow_defs_tenant_engine_def", "tenant_id", "engine_definition_id"),
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
    workflow_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    bpmn_process_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    artifact_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    artifact_xml: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    deployment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    engine_definition_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    instances: Mapped[list["WorkflowInstanceRecord"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )


class WorkflowInstanceRecord(Base):
    """Canonical workflow process instance projection mapped to PostgreSQL workflow_instances."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "engine_instance_id", name="uq_workflow_inst_tenant_engine_id"
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'SUSPENDED', 'TERMINATED', 'FAILED')",
            name="ck_workflow_inst_status_valid",
        ),
        Index("ix_workflow_inst_tenant_status", "tenant_id", "status"),
        Index("ix_workflow_inst_tenant_business_ref", "tenant_id", "business_reference"),
        Index("ix_workflow_inst_tenant_correlation_id", "tenant_id", "correlation_id"),
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
    definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    business_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    engine_instance_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="RUNNING",
    )
    input_variables: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    started_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    definition: Mapped[WorkflowDefinitionRecord] = relationship(
        back_populates="instances",
    )
    tasks: Mapped[list["WorkflowTaskMappingRecord"]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
    )


class WorkflowTaskMappingRecord(Base):
    """Canonical human workflow task mapping mapped to PostgreSQL workflow_task_mappings."""

    __tablename__ = "workflow_task_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "engine_task_id", name="uq_workflow_task_tenant_engine_id"),
        CheckConstraint(
            "status IN ('PENDING', 'COMPLETED', 'CANCELLED')",
            name="ck_workflow_task_status_valid",
        ),
        Index("ix_workflow_tasks_tenant_instance", "tenant_id", "workflow_instance_id"),
        Index("ix_workflow_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_workflow_tasks_tenant_assignee", "tenant_id", "assignee"),
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
    workflow_instance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    engine_task_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    task_definition_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    assignee: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    candidate_roles: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="PENDING",
    )
    action: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    action_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    form_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    sla_policy_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sla_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    sla_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    warning_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accumulated_pause_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    variables: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    instance: Mapped[WorkflowInstanceRecord] = relationship(
        back_populates="tasks",
    )


class WorkflowArtifactRecord(Base):
    """Generic versioned workflow artifact entity mapped to PostgreSQL workflow_artifacts.

    Stores versioned BPMN, DMN, and form-js definitions with SHA-256 fingerprinting.
    """

    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "artifact_type",
            "artifact_key",
            "version",
            name="uq_workflow_artifacts_tenant_type_key_version",
        ),
        CheckConstraint("version > 0", name="ck_workflow_artifact_version_positive"),
        CheckConstraint(
            "artifact_type IN ('BPMN', 'DMN', 'FORM')", name="ck_workflow_artifact_type_valid"
        ),
        Index(
            "ix_workflow_artifacts_tenant_type_key", "tenant_id", "artifact_type", "artifact_key"
        ),
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
    artifact_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    artifact_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    artifact_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    engine_deployment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    engine_reference_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
