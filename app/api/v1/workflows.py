"""HTTP API contracts and safe router factory for Workflow Runtime (Phase 3A).

Provides:
- POST /definitions: Register versioned BPMN workflow definition
- POST /definitions/{definition_id}/deploy: Deploy definition artifact to workflow engine
- GET  /definitions/{definition_id}: Get definition metadata
- POST /instances: Start a workflow process instance
- GET  /instances/{instance_id}: Get canonical process instance projection
- GET  /tasks: List human user tasks for a tenant or instance
- POST /tasks/{task_id}/actions: Authorize and execute human task action (approve/reject/complete)
- GET  /instances/{instance_id}/history: Retrieve normalized activity execution history
- POST /callbacks: Idempotent external service task callback

Architectural & Security Invariants:
- WORKFLOW_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_AUTHORIZATION_DEPENDENCIES
- Unmounted: This router is NOT registered in app.main until trusted tenant &
  principal auth dependencies exist.
- Pre-execution authorization: Actor eligibility verified before calling Flowable.
- Idempotent execution: Duplicate action submissions do not duplicate committed side effects.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Path, Query, status

from app.api.errors import ApiError, ApiErrorResponse
from app.core.auth.models import AuthenticatedPrincipal, TenantContext
from app.core.idempotency.executor import IdempotencyConflictError
from app.workflow.authorization import WorkflowTaskUnauthorizedError
from app.workflow.schemas import (
    WorkflowCallbackPayload,
    WorkflowDefinitionCreate,
    WorkflowDefinitionResponse,
    WorkflowHistoryEntry,
    WorkflowInstanceResponse,
    WorkflowInstanceStart,
    WorkflowTaskActionRequest,
    WorkflowTaskResponse,
)
from app.workflow.service import (
    WorkflowAdapterOperationUnsupportedError,
    WorkflowDefinitionNotFoundError,
    WorkflowInstanceNotFoundError,
    WorkflowService,
    WorkflowTaskAlreadyCompletedError,
    WorkflowTaskNotFoundError,
)

WORKFLOW_API_PRODUCTION_MOUNT: str = "DEFERRED_PENDING_TRUSTED_HTTP_AUTHORIZATION_DEPENDENCIES"


def _extract_tenant_id(tenant_ctx: Any) -> str:
    """Extract tenant_id string from TenantContext or string value."""
    if isinstance(tenant_ctx, TenantContext):
        return tenant_ctx.tenant_id
    if isinstance(tenant_ctx, str):
        return tenant_ctx
    raise ApiError(
        status_code=401,
        code="tenant.unresolved",
        message="Cannot resolve tenant context.",
    )


def create_workflow_router(
    tenant_dependency: Callable[..., Any],
    principal_dependency: Callable[..., AuthenticatedPrincipal],
    workflow_service_dependency: Callable[..., WorkflowService],
) -> APIRouter:
    """Factory creating a decoupled workflow API router with injected dependencies."""
    parent_router = APIRouter(tags=["Workflows"])
    router = APIRouter()

    @router.post(
        "/definitions",
        response_model=WorkflowDefinitionResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register Workflow Definition",
        responses={
            201: {"model": WorkflowDefinitionResponse, "description": "Definition registered."},
            400: {"model": ApiErrorResponse, "description": "Malformed or invalid BPMN XML."},
            403: {"model": ApiErrorResponse, "description": "Security screening rejected."},
        },
    )
    async def register_definition(
        payload: WorkflowDefinitionCreate = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> WorkflowDefinitionResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.register_definition(
                tenant_id=tenant_id,
                workflow_key=payload.workflow_key,
                name=payload.name,
                description=payload.description,
                bpmn_xml=payload.bpmn_xml,
                actor_id=principal.principal_id,
            )
        except ValueError as exc:
            raise ApiError(
                status_code=400,
                code="workflow.invalid_artifact",
                message=str(exc),
            ) from exc

    @router.post(
        "/definitions/{definition_id}/deploy",
        response_model=WorkflowDefinitionResponse,
        summary="Deploy Workflow Definition",
        responses={
            200: {"model": WorkflowDefinitionResponse, "description": "Definition deployed."},
            404: {"model": ApiErrorResponse, "description": "Definition not found."},
        },
    )
    async def deploy_definition(
        definition_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> WorkflowDefinitionResponse:
        if not (
            principal.has_any_role("tenant_admin", "admin", "system")
            or principal.has_permission("workflow:deploy")
        ):
            raise ApiError(
                status_code=403,
                code="workflow.unauthorized",
                message=(
                    "Deploying workflow definitions requires administrative "
                    "role or workflow:deploy permission."
                ),
            )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.deploy_definition(
                tenant_id=tenant_id,
                definition_id=definition_id,
                actor_id=principal.principal_id,
            )
        except WorkflowDefinitionNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.definition_not_found",
                message="Workflow definition not found.",
            ) from exc

    @router.get(
        "/definitions/{definition_id}",
        response_model=WorkflowDefinitionResponse,
        summary="Get Workflow Definition",
        responses={
            200: {"model": WorkflowDefinitionResponse, "description": "Definition metadata."},
            404: {"model": ApiErrorResponse, "description": "Definition not found."},
        },
    )
    async def get_definition(
        definition_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> WorkflowDefinitionResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_definition(tenant_id=tenant_id, definition_id=definition_id)
        except WorkflowDefinitionNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.definition_not_found",
                message="Workflow definition not found.",
            ) from exc

    @router.post(
        "/instances",
        response_model=WorkflowInstanceResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Start Workflow Instance",
        responses={
            201: {"model": WorkflowInstanceResponse, "description": "Process instance started."},
            404: {"model": ApiErrorResponse, "description": "Workflow definition not found."},
        },
    )
    async def start_instance(
        payload: WorkflowInstanceStart = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> WorkflowInstanceResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.start_instance(
                tenant_id=tenant_id,
                workflow_key=payload.workflow_key,
                version=payload.version,
                business_reference=payload.business_reference,
                correlation_id=payload.correlation_id or x_correlation_id,
                variables=payload.variables,
                actor_id=principal.principal_id,
            )
        except WorkflowDefinitionNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.definition_not_found",
                message="Workflow definition not found.",
            ) from exc

    @router.get(
        "/instances/{instance_id}",
        response_model=WorkflowInstanceResponse,
        summary="Get Workflow Instance",
        responses={
            200: {"model": WorkflowInstanceResponse, "description": "Instance projection."},
            404: {"model": ApiErrorResponse, "description": "Instance not found."},
        },
    )
    async def get_instance(
        instance_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> WorkflowInstanceResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_instance(tenant_id=tenant_id, workflow_instance_id=instance_id)
        except WorkflowInstanceNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.instance_not_found",
                message="Workflow instance not found.",
            ) from exc

    @router.get(
        "/tasks",
        response_model=list[WorkflowTaskResponse],
        summary="List Workflow Tasks",
        responses={
            200: {"model": list[WorkflowTaskResponse], "description": "List of task mappings."},
        },
    )
    async def list_tasks(
        instance_id: UUID | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        assignee_filter: str | None = Query(default=None, alias="assignee"),
        tenant_context: Any = Depends(tenant_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> list[WorkflowTaskResponse]:
        tenant_id = _extract_tenant_id(tenant_context)
        return await service.list_tasks(
            tenant_id=tenant_id,
            workflow_instance_id=instance_id,
            status=status_filter,
            assignee=assignee_filter,
        )

    @router.post(
        "/tasks/{task_id}/action",
        response_model=WorkflowTaskResponse,
        summary="Perform Human Task Action (Singular Route Alias)",
        include_in_schema=False,
    )
    @router.post(
        "/tasks/{task_id}/actions",
        response_model=WorkflowTaskResponse,
        summary="Perform Human Task Action",
        responses={
            200: {"model": WorkflowTaskResponse, "description": "Task action completed."},
            403: {"model": ApiErrorResponse, "description": "Actor not authorized for task."},
            404: {"model": ApiErrorResponse, "description": "Task not found."},
            409: {"model": ApiErrorResponse, "description": "Task action conflict."},
        },
    )
    async def perform_task_action(
        task_id: UUID = Path(...),
        payload: WorkflowTaskActionRequest = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
        idempotency_key: str = Header(
            ...,
            alias="Idempotency-Key",
            description="Canonical idempotency key required for mutating actions",
        ),
    ) -> WorkflowTaskResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.perform_task_action(
                tenant_id=tenant_id,
                workflow_task_id=task_id,
                action=payload.action,
                principal=principal,
                idempotency_key=idempotency_key,
                variables=payload.variables,
            )
        except WorkflowTaskNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.task_not_found",
                message="Workflow task not found.",
            ) from exc
        except WorkflowTaskUnauthorizedError as exc:
            raise ApiError(
                status_code=403,
                code="workflow.task_unauthorized",
                message=exc.reason,
            ) from exc
        except IdempotencyConflictError as exc:
            raise ApiError(
                status_code=409,
                code="idempotency.conflict",
                message=(
                    "An idempotent mutation conflict was detected for the provided idempotency key."
                ),
            ) from exc
        except WorkflowTaskAlreadyCompletedError as exc:
            raise ApiError(
                status_code=409,
                code="workflow.task_already_completed",
                message=str(exc),
            ) from exc
        except WorkflowAdapterOperationUnsupportedError as exc:
            raise ApiError(
                status_code=501,
                code="workflow.action_unsupported",
                message=str(exc),
            ) from exc

    @router.get(
        "/instances/{instance_id}/history",
        response_model=list[WorkflowHistoryEntry],
        summary="Get Instance History",
        responses={
            200: {"model": list[WorkflowHistoryEntry], "description": "Normalized history."},
            404: {"model": ApiErrorResponse, "description": "Instance not found."},
        },
    )
    async def get_history(
        instance_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> list[WorkflowHistoryEntry]:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_normalized_history(
                tenant_id=tenant_id,
                workflow_instance_id=instance_id,
            )
        except WorkflowInstanceNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.instance_not_found",
                message="Workflow instance not found.",
            ) from exc

    @router.post(
        "/callbacks",
        summary="Service Task Callback",
        responses={
            200: {"description": "Callback accepted and recorded."},
            404: {"model": ApiErrorResponse, "description": "Workflow instance not found."},
        },
    )
    async def process_callback(
        payload: WorkflowCallbackPayload = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.process_callback(
                tenant_id=tenant_id,
                payload=payload,
                actor_id=principal.principal_id,
            )
        except WorkflowInstanceNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="workflow.instance_not_found",
                message="Workflow instance not found.",
            ) from exc

    # ==========================================================================
    # UI Artifacts, DMN, Forms, and Task Action Aliases (Phase 3B)
    # ==========================================================================

    @router.post(
        "/validate-bpmn",
        summary="Validate BPMN XML",
    )
    async def validate_bpmn(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        from app.workflow.artifacts.validator import BpmnValidationError, BpmnValidator

        bpmn_xml = str(payload.get("bpmn_xml", ""))
        try:
            meta = BpmnValidator().validate(bpmn_xml, allow_trusted_expressions=True)
            return {
                "valid": True,
                "process_id": meta.process_id,
                "process_name": meta.process_name,
                "artifact_hash": meta.artifact_hash,
            }
        except BpmnValidationError as exc:
            return {"valid": False, "error": str(exc)}

    @router.post(
        "/artifacts/bpmn",
        response_model=WorkflowDefinitionResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Save BPMN Artifact",
    )
    async def save_bpmn_artifact(
        payload: WorkflowDefinitionCreate = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> WorkflowDefinitionResponse:
        return await register_definition(
            payload=payload,
            tenant_context=tenant_context,
            principal=principal,
            service=service,
        )

    @router.post(
        "/artifacts/bpmn/validate",
        summary="Validate BPMN Artifact",
    )
    async def validate_bpmn_artifact(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        return await validate_bpmn(payload=payload)

    @router.post(
        "/dmn/deploy",
        summary="Deploy DMN Decision Table",
    )
    @router.post(
        "/artifacts/dmn/deploy",
        summary="Deploy DMN Artifact",
    )
    async def deploy_dmn(
        payload: dict[str, Any] = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        tenant_id = _extract_tenant_id(tenant_context)
        dmn_xml = str(payload.get("dmn_xml", ""))
        resource_name = str(payload.get("resource_name", "decision.dmn"))
        meta = await service.deploy_decision_table(
            tenant_id=tenant_id,
            dmn_xml=dmn_xml,
            resource_name=resource_name,
            actor_id=principal.principal_id,
        )
        return {
            "decision_key": meta.decision_key,
            "name": meta.decision_name,
            "hit_policy": meta.hit_policy.value,
            "rule_count": meta.rule_count,
            "artifact_hash": meta.artifact_hash,
        }

    @router.post(
        "/dmn/execute",
        summary="Execute DMN Decision Table",
    )
    @router.post(
        "/artifacts/dmn/execute",
        summary="Execute DMN Artifact",
    )
    async def execute_dmn(
        payload: dict[str, Any] = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        tenant_id = _extract_tenant_id(tenant_context)
        resp = await service.execute_decision_table(
            tenant_id=tenant_id,
            decision_key=str(payload.get("decision_key", "")),
            variables=dict(payload.get("variables", {})),
            actor_id=principal.principal_id,
        )
        return {
            "decision_key": resp.decision_key,
            "result_variables": resp.result_variables,
            "evaluated_at": resp.evaluated_at.isoformat(),
        }

    @router.post(
        "/artifacts/dmn",
        summary="Save DMN Artifact",
    )
    async def save_dmn_artifact(
        payload: dict[str, Any] = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        return await deploy_dmn(
            payload=payload,
            tenant_context=tenant_context,
            principal=principal,
            service=service,
        )

    @router.post(
        "/artifacts/forms",
        summary="Save Form Artifact",
    )
    async def save_form_artifact(
        payload: dict[str, Any] = Body(...),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        schema_raw = service.validate_form_definition(payload.get("schema_json", payload))
        schema = await schema_raw if hasattr(schema_raw, "__await__") else schema_raw
        return {
            "form_key": str(payload.get("form_key", schema.id)),
            "title": str(payload.get("title", schema.id)),
            "schema_version": str(schema.schema_version),
            "component_count": len(schema.components),
        }

    @router.post(
        "/artifacts/forms/validate",
        summary="Validate Form Schema",
    )
    async def validate_form_schema(
        payload: dict[str, Any] = Body(...),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        schema_raw = service.validate_form_definition(payload.get("schema_json", payload))
        schema = await schema_raw if hasattr(schema_raw, "__await__") else schema_raw
        return {
            "valid": True,
            "title": str(payload.get("title", schema.id)),
            "components": [c.model_dump() for c in schema.components],
        }

    @router.post(
        "/tasks/{task_id}/action",
        response_model=WorkflowTaskResponse,
        summary="Perform Human Task Action (Singular alias)",
    )
    async def perform_task_action_alias(
        task_id: UUID = Path(...),
        payload: WorkflowTaskActionRequest = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: AuthenticatedPrincipal = Depends(principal_dependency),
        service: WorkflowService = Depends(workflow_service_dependency),
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
    ) -> WorkflowTaskResponse:
        return await perform_task_action(
            task_id=task_id,
            payload=payload,
            tenant_context=tenant_context,
            principal=principal,
            service=service,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/sla/policies",
        summary="List SLA Policies",
    )
    async def list_sla_policies(
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> list[dict[str, Any]]:
        sla_svc = getattr(service, "_sla_service", None)
        raw_policies = getattr(sla_svc, "_policies", {}) if sla_svc else {}
        policies = raw_policies if isinstance(raw_policies, dict) else {}
        return [
            {
                "policy_key": p.policy_key,
                "name": p.name,
                "calendar_ref": p.calendar_ref,
                "warning_duration_seconds": p.warning_duration_seconds,
                "breach_duration_seconds": p.breach_duration_seconds,
            }
            for p in policies.values()
        ]

    @router.post(
        "/sla/policies",
        summary="Register SLA Policy",
    )
    async def register_sla_policy(
        payload: dict[str, Any] = Body(...),
        service: WorkflowService = Depends(workflow_service_dependency),
    ) -> dict[str, Any]:
        from app.workflow.sla.models import SlaPolicyConfig

        policy = SlaPolicyConfig(**payload)
        service.register_sla_policy(policy)
        return {"policy_key": policy.policy_key, "name": policy.name, "status": "REGISTERED"}

    parent_router.include_router(router, prefix="/workflows")
    parent_router.include_router(router, prefix="/workflow")
    return parent_router
