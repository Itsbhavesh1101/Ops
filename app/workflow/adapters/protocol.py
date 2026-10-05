"""Workflow Adapter Protocol (Phase 3A).

Defines the provider-neutral abstraction boundary separating E-7 domain services
from specific workflow runtime implementations (such as Flowable or Operaton).
"""

from typing import Any, Protocol, runtime_checkable

from app.workflow.adapters.dto import (
    EngineDeploymentResult,
    EngineHistoricActivity,
    EngineProcessInstance,
    EngineTask,
    EngineTaskCompletionResult,
)


class WorkflowAdapterError(Exception):
    """Base exception for workflow adapter operations."""


class WorkflowEngineConnectionError(WorkflowAdapterError):
    """Raised when the workflow engine is unreachable or times out."""


class WorkflowEngineResponseError(WorkflowAdapterError):
    """Raised when the engine returns an unexpected or invalid response."""


class WorkflowResourceNotFoundError(WorkflowAdapterError):
    """Raised when a referenced engine resource (deployment, instance, task) does not exist."""


@runtime_checkable
class WorkflowAdapterProtocol(Protocol):
    """Contract for workflow engine adapter implementations."""

    async def deploy_definition(
        self,
        bpmn_xml: str,
        resource_name: str,
        tenant_id: str,
    ) -> EngineDeploymentResult:
        """Deploy a BPMN 2.0 XML definition to the workflow engine."""
        ...

    async def start_instance(
        self,
        process_definition_key: str,
        business_key: str | None,
        variables: dict[str, Any] | None,
        tenant_id: str,
    ) -> EngineProcessInstance:
        """Start a new process instance on the workflow engine."""
        ...

    async def get_instance(
        self,
        engine_instance_id: str,
        tenant_id: str,
    ) -> EngineProcessInstance | None:
        """Retrieve an active or finished process instance by engine ID."""
        ...

    async def list_user_tasks(
        self,
        engine_instance_id: str | None = None,
        assignee: str | None = None,
        candidate_group: str | None = None,
        tenant_id: str | None = None,
    ) -> list[EngineTask]:
        """List active user tasks matching specified criteria."""
        ...

    async def get_user_task(
        self,
        engine_task_id: str,
        tenant_id: str,
    ) -> EngineTask | None:
        """Retrieve a specific user task by engine task ID."""
        ...

    async def complete_user_task(
        self,
        engine_task_id: str,
        action: str,
        variables: dict[str, Any] | None,
        tenant_id: str,
    ) -> EngineTaskCompletionResult:
        """Complete an active user task with outcome variables."""
        ...

    async def get_history(
        self,
        engine_instance_id: str,
        tenant_id: str,
    ) -> list[EngineHistoricActivity]:
        """Retrieve the normalized activity execution history for an instance."""
        ...
