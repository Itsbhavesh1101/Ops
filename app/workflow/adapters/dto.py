"""Provider-neutral DTOs for workflow engine communication (Phase 3A).

These DTOs decouple internal domain logic from upstream engine REST response formats
(e.g. Flowable, Operaton).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EngineDeploymentResult(BaseModel):
    """Normalized engine deployment outcome."""

    model_config = ConfigDict(extra="ignore")

    deployment_id: str = Field(..., description="Engine-generated deployment identifier")
    name: str = Field(..., description="Deployment name")
    deployment_time: datetime | None = None
    engine_definition_id: str | None = Field(
        default=None, description="Primary process definition ID created by deployment"
    )
    engine_definition_key: str | None = Field(
        default=None, description="Primary process definition key"
    )
    version: int | None = Field(default=None, description="Engine-assigned definition version")


class EngineProcessInstance(BaseModel):
    """Normalized active or completed engine process instance."""

    model_config = ConfigDict(extra="ignore")

    engine_instance_id: str = Field(..., description="Engine process instance identifier")
    engine_definition_id: str = Field(..., description="Engine process definition identifier")
    engine_definition_key: str = Field(..., description="Engine process definition key")
    business_key: str | None = None
    is_ended: bool = False
    is_suspended: bool = False
    start_time: datetime | None = None
    end_time: datetime | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class EngineTask(BaseModel):
    """Normalized human user task from the workflow engine."""

    model_config = ConfigDict(extra="ignore")

    engine_task_id: str = Field(..., description="Engine task identifier")
    task_definition_key: str = Field(..., description="BPMN activity identifier for the user task")
    name: str = Field(..., description="Human-readable task title")
    description: str | None = None
    engine_instance_id: str = Field(..., description="Parent process instance identifier")
    assignee: str | None = None
    candidate_roles: list[str] = Field(
        default_factory=list, description="Candidate group identifiers"
    )
    created_time: datetime | None = None
    due_date: datetime | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class EngineTaskCompletionResult(BaseModel):
    """Normalized outcome of completing a user task."""

    model_config = ConfigDict(extra="ignore")

    engine_task_id: str
    status: str = "COMPLETED"
    completed_at: datetime
    variables: dict[str, Any] = Field(default_factory=dict)


class EngineHistoricActivity(BaseModel):
    """Normalized historic activity instance representation."""

    model_config = ConfigDict(extra="ignore")

    activity_id: str = Field(..., description="BPMN activity element ID")
    activity_name: str | None = None
    activity_type: str = Field(..., description="BPMN element type (e.g. userTask, startEvent)")
    engine_instance_id: str
    execution_id: str | None = None
    assignee: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int | None = None
    is_completed: bool = False
