"""Workflow engine adapters package."""

from app.workflow.adapters.dto import (
    EngineDeploymentResult,
    EngineHistoricActivity,
    EngineProcessInstance,
    EngineTask,
    EngineTaskCompletionResult,
)
from app.workflow.adapters.protocol import (
    WorkflowAdapterError,
    WorkflowAdapterProtocol,
    WorkflowEngineConnectionError,
    WorkflowEngineResponseError,
    WorkflowResourceNotFoundError,
)

__all__ = [
    "EngineDeploymentResult",
    "EngineHistoricActivity",
    "EngineProcessInstance",
    "EngineTask",
    "EngineTaskCompletionResult",
    "WorkflowAdapterError",
    "WorkflowAdapterProtocol",
    "WorkflowEngineConnectionError",
    "WorkflowEngineResponseError",
    "WorkflowResourceNotFoundError",
]
