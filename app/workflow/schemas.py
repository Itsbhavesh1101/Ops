"""Pydantic schemas and state contracts for canonical workflow domain (Phase 3A).

Provides:
- WorkflowDefinition schemas
- WorkflowInstance schemas
- WorkflowTaskMapping schemas
- Engine DTOs and callback contracts
- State enums for instances and tasks
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowInstanceStatus(StrEnum):
    """Canonical lifecycle state for a workflow instance projection."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


class WorkflowTaskStatus(StrEnum):
    """Canonical lifecycle state for a workflow human task mapping."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class WorkflowTaskAction(StrEnum):
    """Supported human task actions."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    COMPLETE = "COMPLETE"
    CLAIM = "CLAIM"
    UNCLAIM = "UNCLAIM"


# ==============================================================================
# Definition Schemas
# ==============================================================================


class WorkflowDefinitionCreate(BaseModel):
    """Payload for registering a new workflow definition."""

    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(
        ..., min_length=1, max_length=128, description="Unique business key for the workflow"
    )
    name: str = Field(..., min_length=1, max_length=255, description="Human-readable workflow name")
    description: str | None = Field(default=None, description="Optional workflow description")
    bpmn_xml: str = Field(..., min_length=10, description="Raw BPMN 2.0 XML string")


class WorkflowDefinitionResponse(BaseModel):
    """Canonical representation of a workflow definition."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    workflow_key: str
    name: str
    description: str | None
    version: int
    bpmn_process_id: str
    artifact_hash: str
    deployment_id: str | None
    engine_definition_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# Instance Schemas
# ==============================================================================


class WorkflowInstanceStart(BaseModel):
    """Request payload to start a workflow process instance."""

    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(..., min_length=1, max_length=128)
    version: int | None = Field(
        default=None, description="Optional specific version; defaults to latest active"
    )
    business_reference: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=255)
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Typed process input variables"
    )


class WorkflowInstanceResponse(BaseModel):
    """Canonical representation of a workflow process instance."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    definition_id: UUID
    workflow_key: str
    version: int
    business_reference: str | None
    correlation_id: str | None
    engine_instance_id: str
    status: WorkflowInstanceStatus
    input_variables: dict[str, Any]
    started_by: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# Task Mapping Schemas
# ==============================================================================


class WorkflowTaskActionRequest(BaseModel):
    """Request payload to complete an authorized human task action."""

    model_config = ConfigDict(extra="forbid")

    action: WorkflowTaskAction = Field(default=WorkflowTaskAction.COMPLETE)
    variables: dict[str, Any] = Field(default_factory=dict, description="Task output variables")
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.upper()
        return v


class WorkflowTaskResponse(BaseModel):
    """Canonical representation of a human workflow task."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    workflow_instance_id: UUID
    engine_task_id: str
    task_definition_key: str
    task_name: str
    assignee: str | None
    candidate_roles: list[str]
    status: WorkflowTaskStatus
    action: str | None
    action_by: str | None
    action_at: datetime | None
    due_at: datetime | None
    variables: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# Service Task Callback Schemas
# ==============================================================================


class WorkflowCallbackPayload(BaseModel):
    """Typed callback contract for asynchronous service tasks."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(..., min_length=1, max_length=255)
    workflow_instance_id: UUID
    activity_id: str = Field(
        ..., min_length=1, max_length=255, description="BPMN activity/service task ID"
    )
    correlation_id: str | None = Field(default=None, max_length=255)
    result_status: str = Field(..., description="'SUCCESS' or 'FAILURE'")
    output_variables: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = Field(default=None, max_length=1000)


class WorkflowHistoryEntry(BaseModel):
    """Normalized audit/history activity representation."""

    model_config = ConfigDict(extra="forbid")

    activity_id: str
    activity_name: str | None
    activity_type: str
    execution_id: str | None
    start_time: datetime | None
    end_time: datetime | None
    duration_ms: int | None
    assignee: str | None
    completed: bool
