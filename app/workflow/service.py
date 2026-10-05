"""Canonical Workflow Application Service (Phase 3A).

Coordinates:
- Canonical persistence (WorkflowDefinitionRecord, WorkflowInstanceRecord,
  WorkflowTaskMappingRecord)
- Provider-neutral workflow engine adapter (FlowableAdapter)
- Actor-eligibility authorization enforcement BEFORE engine execution
- Canonical Phase-1 idempotency identity and request fingerprinting
- Mandatory workflow variable allowlist filtering and security scrubbing
- Outbox event publication (workflow.task.created)
- Audit logging (workflow.*)
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    RequestFingerprint,
    create_idempotency_identity,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.core.idempotency.hashing import fingerprint_bytes
from app.core.idempotency.repository import (
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.db.models.workflow import (
    WorkflowDefinitionRecord,
    WorkflowInstanceRecord,
    WorkflowTaskMappingRecord,
)
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.approval.compiler import ApprovalBpmnCompiler
from app.workflow.approval.delegation import (
    DefaultDelegationPolicy,
    DelegationPolicyProtocol,
)
from app.workflow.approval.models import ApprovalWorkflowConfig
from app.workflow.artifacts.validator import BpmnValidator
from app.workflow.authorization import (
    DefaultTaskEligibilityPolicy,
    TaskEligibilityPolicyProtocol,
    WorkflowTaskUnauthorizedError,
)
from app.workflow.dmn.schemas import (
    DmnDecisionMetadata,
    DmnExecutionResponse,
)
from app.workflow.dmn.validator import DmnValidator
from app.workflow.forms.schemas import FormDefinitionSchema
from app.workflow.forms.validator import FormValidator
from app.workflow.repository import (
    WorkflowDefinitionRepository,
    WorkflowInstanceRepository,
    WorkflowTaskRepository,
)
from app.workflow.schemas import (
    WorkflowCallbackPayload,
    WorkflowDefinitionResponse,
    WorkflowHistoryEntry,
    WorkflowInstanceResponse,
    WorkflowInstanceStatus,
    WorkflowTaskAction,
    WorkflowTaskResponse,
    WorkflowTaskStatus,
)
from app.workflow.sla.models import SlaPolicyConfig, TaskSlaState
from app.workflow.sla.service import SlaService
from app.workflow.variables import (
    WorkflowVariablePolicy,
    filter_workflow_variables,
)

WORKFLOW_TASK_ACTION_IDEMPOTENCY: str = "PHASE_1_CANONICAL_IDENTITY_PLUS_REQUEST_FINGERPRINT"


class WorkflowServiceError(Exception):
    """Base exception for workflow domain service errors."""


class WorkflowDefinitionNotFoundError(WorkflowServiceError):
    """Raised when a workflow definition is not found in the tenant."""


class WorkflowInstanceNotFoundError(WorkflowServiceError):
    """Raised when a workflow process instance is not found in the tenant."""


class WorkflowTaskNotFoundError(WorkflowServiceError):
    """Raised when a human task mapping is not found in the tenant."""


class WorkflowTaskAlreadyCompletedError(WorkflowServiceError):
    """Raised when attempting an conflicting action on an already-completed task."""


class WorkflowService:
    """Application service orchestrating canonical workflow domain lifecycle."""

    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        adapter: WorkflowAdapterProtocol,
        eligibility_policy: TaskEligibilityPolicyProtocol | None = None,
        variable_policy: WorkflowVariablePolicy | None = None,
        bpmn_validator: BpmnValidator | None = None,
        dmn_validator: DmnValidator | None = None,
        form_validator: FormValidator | None = None,
        approval_compiler: ApprovalBpmnCompiler | None = None,
        delegation_policy: DelegationPolicyProtocol | None = None,
        sla_service: SlaService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._adapter = adapter
        self._eligibility_policy = eligibility_policy or DefaultTaskEligibilityPolicy()
        self._variable_policy = variable_policy or WorkflowVariablePolicy(allowed_keys=frozenset())
        self._bpmn_validator = bpmn_validator or BpmnValidator()
        self._dmn_validator = dmn_validator or DmnValidator()
        self._form_validator = form_validator or FormValidator()
        self._approval_compiler = approval_compiler or ApprovalBpmnCompiler()
        self._delegation_policy = delegation_policy or DefaultDelegationPolicy()
        self._sla_service = sla_service or SlaService()

    # ==========================================================================
    # 1. Definition Management & Deployment
    # ==========================================================================

    async def register_definition(
        self,
        tenant_id: str,
        workflow_key: str,
        name: str,
        bpmn_xml: str,
        description: str | None = None,
        actor_id: str = "system",
        allow_trusted_expressions: bool = False,
    ) -> WorkflowDefinitionResponse:
        """Validate, fingerprint, and persist a new workflow definition version."""
        # Security & topology validation
        parsed_def = self._bpmn_validator.validate(
            bpmn_xml, allow_trusted_expressions=allow_trusted_expressions
        )

        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            next_version = await def_repo.get_next_version(tenant_id, workflow_key)
            definition_id = uuid4()
            now = datetime.now(UTC)

            record = WorkflowDefinitionRecord(
                id=definition_id,
                tenant_id=tenant_id,
                workflow_key=workflow_key,
                name=name,
                description=description,
                version=next_version,
                bpmn_process_id=parsed_def.process_id,
                artifact_hash=parsed_def.artifact_hash,
                artifact_xml=bpmn_xml,
                deployment_id=None,
                engine_definition_id=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            await def_repo.create(record)

            # Audit definition registration
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.definition.registered",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                    resource_type="workflow_definition",
                    resource_id=str(definition_id),
                    outcome=AuditEventOutcome.SUCCESS,
                    details={
                        "workflow_key": workflow_key,
                        "version": next_version,
                        "bpmn_process_id": parsed_def.process_id,
                        "artifact_hash": parsed_def.artifact_hash,
                    },
                )
            )
            response = WorkflowDefinitionResponse.model_validate(record)
            await uow.commit()
            return response

    async def deploy_definition(
        self,
        tenant_id: str,
        definition_id: UUID,
        actor_id: str = "system",
    ) -> WorkflowDefinitionResponse:
        """Deploy an existing definition artifact to the Flowable engine."""
        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            record = await def_repo.get_by_id(tenant_id, definition_id)
            if not record:
                raise WorkflowDefinitionNotFoundError(
                    f"Workflow definition '{definition_id}' not found."
                )

            bpmn_xml = record.artifact_xml
            resource_name = f"{record.workflow_key}_v{record.version}"
            wf_key = record.workflow_key
            wf_ver = record.version

        # Call engine adapter outside UoW transaction
        deploy_res = await self._adapter.deploy_definition(
            bpmn_xml=bpmn_xml,
            resource_name=resource_name,
            tenant_id=tenant_id,
        )

        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            record = await def_repo.get_by_id(tenant_id, definition_id)
            if not record:
                raise WorkflowDefinitionNotFoundError(
                    f"Workflow definition '{definition_id}' not found."
                )

            record.deployment_id = deploy_res.deployment_id
            engine_def_id = deploy_res.engine_definition_id or deploy_res.deployment_id
            record.engine_definition_id = engine_def_id
            record.updated_at = datetime.now(UTC)

            # Audit definition deployment
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.definition.deployed",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                    resource_type="workflow_definition",
                    resource_id=str(definition_id),
                    outcome=AuditEventOutcome.SUCCESS,
                    details={
                        "workflow_key": wf_key,
                        "version": wf_ver,
                        "deployment_id": deploy_res.deployment_id,
                        "engine_definition_id": engine_def_id,
                    },
                )
            )
            response = WorkflowDefinitionResponse.model_validate(record)
            await uow.commit()
            return response

    async def get_definition(
        self,
        tenant_id: str,
        definition_id: UUID,
    ) -> WorkflowDefinitionResponse:
        """Retrieve a specific workflow definition by canonical ID."""
        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            record = await def_repo.get_by_id(tenant_id, definition_id)
            if not record:
                raise WorkflowDefinitionNotFoundError(
                    f"Workflow definition '{definition_id}' not found."
                )
            return WorkflowDefinitionResponse.model_validate(record)

    async def list_definitions(
        self,
        tenant_id: str,
        workflow_key: str | None = None,
        active_only: bool = True,
    ) -> list[WorkflowDefinitionResponse]:
        """List workflow definitions matching tenant filters."""
        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            records = await def_repo.list_by_tenant(tenant_id, workflow_key, active_only)
            return [WorkflowDefinitionResponse.model_validate(r) for r in records]

    # ==========================================================================
    # 2. Process Instance Lifecycle
    # ==========================================================================

    async def start_instance(
        self,
        tenant_id: str,
        workflow_key: str,
        business_reference: str | None = None,
        correlation_id: str | None = None,
        variables: dict[str, Any] | None = None,
        version: int | None = None,
        actor_id: str = "system",
    ) -> WorkflowInstanceResponse:
        """Start a new workflow process instance on the engine and synchronize canonical state."""
        async with self._uow_factory() as uow:
            def_repo = WorkflowDefinitionRepository(uow.session)
            if version is not None:
                def_record = await def_repo.get_by_key_and_version(tenant_id, workflow_key, version)
            else:
                def_record = await def_repo.get_latest_active_by_key(tenant_id, workflow_key)

            if not def_record:
                raise WorkflowDefinitionNotFoundError(
                    f"No active definition found for workflow key '{workflow_key}'."
                )
            definition_id = def_record.id
            bpmn_process_id = def_record.bpmn_process_id
            def_version = def_record.version
            is_deployed = bool(def_record.deployment_id)
            artifact_xml = def_record.artifact_xml

        # Auto-deploy if definition has not been deployed to engine yet
        if not is_deployed:
            dep_res = await self._adapter.deploy_definition(
                bpmn_xml=artifact_xml,
                resource_name=f"{workflow_key}_v{def_version}",
                tenant_id=tenant_id,
            )
            async with self._uow_factory() as uow:
                def_repo = WorkflowDefinitionRepository(uow.session)
                d_rec = await def_repo.get_by_id(tenant_id, definition_id)
                if d_rec:
                    d_rec.deployment_id = dep_res.deployment_id
                    eng_id = dep_res.engine_definition_id or dep_res.deployment_id
                    d_rec.engine_definition_id = eng_id
                    await uow.commit()

        # Sanitize input variables using mandatory allowlist
        filtered_input = filter_workflow_variables(
            variables,
            allowed_keys=self._variable_policy.allowed_keys,
        )

        # Build merged engine variables
        merged_vars = dict(filtered_input)
        merged_vars["_tenant_id"] = tenant_id
        if business_reference:
            merged_vars["_business_reference"] = business_reference
        if correlation_id:
            merged_vars["_correlation_id"] = correlation_id

        engine_inst = await self._adapter.start_instance(
            process_definition_key=bpmn_process_id,
            business_key=business_reference,
            variables=merged_vars,
            tenant_id=tenant_id,
        )

        instance_id = uuid4()
        now = datetime.now(UTC)
        initial_status = (
            WorkflowInstanceStatus.COMPLETED
            if engine_inst.is_ended
            else WorkflowInstanceStatus.RUNNING
        )

        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            inst_record = WorkflowInstanceRecord(
                id=instance_id,
                tenant_id=tenant_id,
                definition_id=definition_id,
                workflow_key=workflow_key,
                version=def_version,
                business_reference=business_reference,
                correlation_id=correlation_id,
                engine_instance_id=engine_inst.engine_instance_id,
                status=initial_status.value,
                input_variables=filtered_input,
                started_by=actor_id,
                started_at=engine_inst.start_time or now,
                completed_at=engine_inst.end_time if engine_inst.is_ended else None,
                created_at=now,
                updated_at=now,
            )
            await inst_repo.create(inst_record)

            # Audit instance creation
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.instance.started",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                    resource_type="workflow_instance",
                    resource_id=str(instance_id),
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=correlation_id,
                    details={
                        "workflow_key": workflow_key,
                        "version": def_version,
                        "business_reference": business_reference,
                        "engine_instance_id": engine_inst.engine_instance_id,
                        "variables": filtered_input,
                    },
                )
            )
            await uow.commit()

        # Synchronize active tasks immediately
        await self.sync_instance_tasks(tenant_id=tenant_id, workflow_instance_id=instance_id)

        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            reloaded = await inst_repo.get_by_id(tenant_id, instance_id)
            return WorkflowInstanceResponse.model_validate(reloaded)

    async def get_instance(
        self,
        tenant_id: str,
        workflow_instance_id: UUID,
    ) -> WorkflowInstanceResponse:
        """Retrieve a specific workflow instance by canonical ID."""
        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            record = await inst_repo.get_by_id(tenant_id, workflow_instance_id)
            if not record:
                raise WorkflowInstanceNotFoundError(
                    f"Workflow instance '{workflow_instance_id}' not found."
                )
            return WorkflowInstanceResponse.model_validate(record)

    async def list_instances(
        self,
        tenant_id: str,
        workflow_key: str | None = None,
        status: str | None = None,
        business_reference: str | None = None,
    ) -> list[WorkflowInstanceResponse]:
        """List workflow instances matching tenant filters."""
        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            records = await inst_repo.list_by_tenant(
                tenant_id, workflow_key, status, business_reference
            )
            return [WorkflowInstanceResponse.model_validate(r) for r in records]

    # ==========================================================================
    # 3. Task Management & Execution
    # ==========================================================================

    async def sync_instance_tasks(
        self,
        tenant_id: str,
        workflow_instance_id: UUID,
    ) -> list[WorkflowTaskResponse]:
        """Synchronize active human tasks from Flowable to canonical task mapping table."""
        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            instance = await inst_repo.get_by_id(tenant_id, workflow_instance_id)
            if not instance:
                raise WorkflowInstanceNotFoundError(
                    f"Workflow instance '{workflow_instance_id}' not found."
                )
            engine_inst_id = instance.engine_instance_id

        # Query engine tasks
        engine_tasks = await self._adapter.list_user_tasks(
            engine_instance_id=engine_inst_id,
            tenant_id=tenant_id,
        )

        synced_tasks: list[WorkflowTaskResponse] = []

        async with self._uow_factory() as uow:
            task_repo = WorkflowTaskRepository(uow.session)
            outbox_repo = OutboxRepository(uow.session)

            for et in engine_tasks:
                existing = await task_repo.get_by_engine_task_id(tenant_id, et.engine_task_id)
                if not existing:
                    new_task_id = uuid4()
                    # Apply mandatory allowlist to engine task variables
                    filtered_task_vars = filter_workflow_variables(
                        et.variables,
                        allowed_keys=self._variable_policy.allowed_keys,
                    )
                    task_record = WorkflowTaskMappingRecord(
                        id=new_task_id,
                        workflow_instance_id=workflow_instance_id,
                        tenant_id=tenant_id,
                        engine_task_id=et.engine_task_id,
                        task_definition_key=et.task_definition_key,
                        task_name=et.name or et.task_definition_key,
                        assignee=et.assignee,
                        candidate_roles=et.candidate_roles,
                        status=WorkflowTaskStatus.PENDING.value,
                        action=None,
                        action_by=None,
                        action_at=None,
                        due_at=et.due_date,
                        variables=filtered_task_vars,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    await task_repo.create(task_record)

                    # Emit canonical workflow.task.created event via outbox
                    envelope = create_event_envelope(
                        EventCreate(
                            event_type="workflow.task.created",
                            event_version=1,
                            tenant_id=tenant_id,
                            subject_type="workflow_task",
                            subject_id=str(new_task_id),
                            payload={
                                "workflow_task_id": str(new_task_id),
                                "workflow_instance_id": str(workflow_instance_id),
                                "engine_task_id": et.engine_task_id,
                                "task_definition_key": et.task_definition_key,
                                "task_name": task_record.task_name,
                                "assignee": et.assignee,
                                "candidate_roles": et.candidate_roles,
                                "due_at": et.due_date.isoformat() if et.due_date else None,
                            },
                        )
                    )
                    await outbox_repo.enqueue(envelope)
                    synced_tasks.append(WorkflowTaskResponse.model_validate(task_record))
                else:
                    synced_tasks.append(WorkflowTaskResponse.model_validate(existing))

            # Check if engine process has ended
            engine_inst = await self._adapter.get_instance(engine_inst_id, tenant_id)
            if engine_inst and engine_inst.is_ended:
                inst_repo = WorkflowInstanceRepository(uow.session)
                inst_record = await inst_repo.get_by_id(tenant_id, workflow_instance_id)
                if inst_record and inst_record.status != WorkflowInstanceStatus.COMPLETED.value:
                    inst_record.status = WorkflowInstanceStatus.COMPLETED.value
                    inst_record.completed_at = engine_inst.end_time or datetime.now(UTC)

            await uow.commit()

        return synced_tasks

    async def list_tasks(
        self,
        tenant_id: str,
        workflow_instance_id: UUID | None = None,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[WorkflowTaskResponse]:
        """List canonical workflow task mappings matching filter criteria."""
        async with self._uow_factory() as uow:
            task_repo = WorkflowTaskRepository(uow.session)
            if workflow_instance_id:
                records = await task_repo.list_by_instance(tenant_id, workflow_instance_id, status)
            else:
                records = await task_repo.list_by_tenant(tenant_id, status, assignee)
            return [WorkflowTaskResponse.model_validate(r) for r in records]

    async def perform_task_action(
        self,
        tenant_id: str,
        workflow_task_id: UUID,
        action: WorkflowTaskAction,
        principal: AuthenticatedPrincipal,
        idempotency_key: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> WorkflowTaskResponse:
        """Authorize and execute a human task action with canonical Phase-1 idempotency."""
        # 1. Sanitize variables using mandatory allowlist
        filtered_action_vars = filter_workflow_variables(
            variables,
            allowed_keys=self._variable_policy.allowed_keys,
        )

        # 2. Construct canonical identity and request fingerprint if idempotency_key is provided
        identity: IdempotencyIdentity | None = None
        fingerprint: RequestFingerprint | None = None
        if idempotency_key is not None:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="workflow.task.action",
                raw_key=idempotency_key,
            )
            fingerprint_payload = {
                "action": action.value,
                "variables": filtered_action_vars,
                "workflow_task_id": str(workflow_task_id),
            }
            serialized_payload = json.dumps(
                fingerprint_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            fingerprint = fingerprint_bytes(serialized_payload)

        # 3. Phase 1: Query task, evaluate authorization FIRST, and check idempotency marker
        async with self._uow_factory() as uow:
            task_repo = WorkflowTaskRepository(uow.session)
            task_record = await task_repo.get_by_id(tenant_id, workflow_task_id)
            if not task_record:
                raise WorkflowTaskNotFoundError(f"Workflow task '{workflow_task_id}' not found.")

            # ------------------------------------------------------------------
            # ACTOR ELIGIBILITY CHECK: Always strictly evaluated FIRST!
            # Unauthorized principal using a valid replay key is DENIED before replay disclosure!
            # ------------------------------------------------------------------
            is_eligible = self._eligibility_policy.is_eligible(
                principal=principal,
                task=task_record,
                action=action.value,
            )
            if not is_eligible and task_record.assignee and self._delegation_policy:
                is_eligible = self._delegation_policy.is_delegated(
                    tenant_id=tenant_id,
                    delegator_id=task_record.assignee,
                    delegatee_id=principal.principal_id,
                    workflow_key=str(task_record.workflow_instance_id),
                )
            if not is_eligible:
                audit_repo = AuditRepository(uow.session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="workflow.task.unauthorized",
                        actor_principal_id=principal.principal_id,
                        actor_principal_type=principal.principal_type.value,
                        authentication_method=principal.authentication_method.value,
                        resource_type="workflow_task",
                        resource_id=str(workflow_task_id),
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "action": action.value,
                            "assignee": task_record.assignee,
                            "candidate_roles": task_record.candidate_roles,
                            "principal_roles": list(principal.roles),
                        },
                    )
                )
                await uow.commit()

                raise WorkflowTaskUnauthorizedError(
                    principal_id=principal.principal_id,
                    task_id=str(workflow_task_id),
                    reason="Principal is not authorized to perform requested action on this task.",
                )

            # ------------------------------------------------------------------
            # CANONICAL IDEMPOTENCY CHECK
            # ------------------------------------------------------------------
            if identity is not None and fingerprint is not None:
                idempotency_repo = IdempotencyRepository(uow.session)
                decision = await idempotency_repo.acquire_or_observe(
                    identity=identity,
                    request_fingerprint=fingerprint,
                )

                if decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                    # Authorized replay: Return previously committed task without invoking Flowable
                    # Zero duplicate Flowable call, zero duplicate audit, zero duplicate outbox!
                    return WorkflowTaskResponse.model_validate(task_record)

                if decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                    # Conflicting request: Same key used with different fingerprint
                    assert decision.existing_fingerprint is not None
                    raise IdempotencyConflictError(
                        identity=identity,
                        request_fingerprint=fingerprint,
                        existing_fingerprint=decision.existing_fingerprint,
                    )

            # ------------------------------------------------------------------
            # TASK STATUS CHECK
            # ------------------------------------------------------------------
            if task_record.status == WorkflowTaskStatus.COMPLETED.value:
                # If here, this is NOT an EXISTING_MATCH replay for this identity
                # (either a new idempotency key or no key was passed)
                msg = f"Task '{workflow_task_id}' already completed with '{task_record.action}'."
                raise WorkflowTaskAlreadyCompletedError(msg)

            engine_task_id = task_record.engine_task_id
            instance_id = task_record.workflow_instance_id

        # 4. Execute completion on Flowable engine
        # Only allowlisted business variables sent to engine (plus non-raw metadata)
        outbound_vars = dict(filtered_action_vars)
        outbound_vars["_action_by"] = principal.principal_id
        if identity is not None:
            outbound_vars["_key_digest"] = str(identity.key_digest)

        await self._adapter.complete_user_task(
            engine_task_id=engine_task_id,
            action=action.value,
            variables=outbound_vars,
            tenant_id=tenant_id,
        )

        # 5. Persist completed state, idempotency marker, and audit in canonical UoW
        now = datetime.now(UTC)
        async with self._uow_factory() as uow:
            task_repo = WorkflowTaskRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            if identity is not None and fingerprint is not None:
                idempotency_repo = IdempotencyRepository(uow.session)
                await idempotency_repo.acquire_or_observe(
                    identity=identity,
                    request_fingerprint=fingerprint,
                )

            task_record = await task_repo.get_by_id(tenant_id, workflow_task_id)
            if not task_record:
                raise WorkflowTaskNotFoundError(f"Workflow task '{workflow_task_id}' not found.")

            task_record.status = WorkflowTaskStatus.COMPLETED.value
            task_record.action = action.value
            task_record.action_by = principal.principal_id
            task_record.action_at = now
            task_record.variables = filtered_action_vars
            task_record.updated_at = now

            # Audit successful action (Contains key_digest, NO raw idempotency key)
            audit_details: dict[str, Any] = {
                "action": action.value,
                "engine_task_id": engine_task_id,
                "variables": filtered_action_vars,
            }
            if identity is not None:
                audit_details["key_digest"] = str(identity.key_digest)

            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.task.completed",
                    actor_principal_id=principal.principal_id,
                    actor_principal_type=principal.principal_type.value,
                    authentication_method=principal.authentication_method.value,
                    resource_type="workflow_task",
                    resource_id=str(workflow_task_id),
                    outcome=AuditEventOutcome.SUCCESS,
                    details=audit_details,
                )
            )
            response = WorkflowTaskResponse.model_validate(task_record)
            await uow.commit()

        # 6. Synchronize subsequent tasks on the instance
        await self.sync_instance_tasks(tenant_id, instance_id)

        return response

    # ==========================================================================
    # 4. Service-Task Callback Foundation
    # ==========================================================================

    async def process_callback(
        self,
        tenant_id: str,
        payload: WorkflowCallbackPayload,
        actor_id: str = "callback-worker",
    ) -> dict[str, Any]:
        """Process an idempotent callback from an external service task."""
        identity: IdempotencyIdentity | None = None
        fingerprint: RequestFingerprint | None = None
        if payload.idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="workflow.service.callback",
                raw_key=payload.idempotency_key,
            )
            fingerprint_payload = {
                "activity_id": payload.activity_id,
                "result_status": payload.result_status,
                "workflow_instance_id": str(payload.workflow_instance_id),
            }
            serialized = json.dumps(
                fingerprint_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            fingerprint = fingerprint_bytes(serialized)

        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            instance = await inst_repo.get_by_id(tenant_id, payload.workflow_instance_id)
            if not instance:
                raise WorkflowInstanceNotFoundError(
                    f"Workflow instance '{payload.workflow_instance_id}' not found."
                )

            if identity is not None and fingerprint is not None:
                idempotency_repo = IdempotencyRepository(uow.session)
                decision = await idempotency_repo.acquire_or_observe(
                    identity=identity,
                    request_fingerprint=fingerprint,
                )
                if decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                    # Idempotent replay: return accepted without duplicate audit
                    return {
                        "status": "ACCEPTED",
                        "workflow_instance_id": str(payload.workflow_instance_id),
                        "result_status": payload.result_status,
                    }
                if decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                    assert decision.existing_fingerprint is not None
                    raise IdempotencyConflictError(
                        identity=identity,
                        request_fingerprint=fingerprint,
                        existing_fingerprint=decision.existing_fingerprint,
                    )

            audit_details: dict[str, Any] = {
                "activity_id": payload.activity_id,
                "result_status": payload.result_status,
                "correlation_id": payload.correlation_id,
            }
            if identity is not None:
                audit_details["key_digest"] = str(identity.key_digest)

            # Record audit evidence for the callback
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.callback.received",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                    resource_type="workflow_instance",
                    resource_id=str(payload.workflow_instance_id),
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=payload.correlation_id,
                    details=audit_details,
                )
            )
            await uow.commit()

        return {
            "status": "ACCEPTED",
            "workflow_instance_id": str(payload.workflow_instance_id),
            "result_status": payload.result_status,
        }

    # ==========================================================================
    # 5. History Normalization
    # ==========================================================================

    async def get_normalized_history(
        self,
        tenant_id: str,
        workflow_instance_id: UUID,
    ) -> list[WorkflowHistoryEntry]:
        """Fetch and normalize engine execution history for an instance."""
        async with self._uow_factory() as uow:
            inst_repo = WorkflowInstanceRepository(uow.session)
            instance = await inst_repo.get_by_id(tenant_id, workflow_instance_id)
            if not instance:
                raise WorkflowInstanceNotFoundError(
                    f"Workflow instance '{workflow_instance_id}' not found."
                )
            engine_inst_id = instance.engine_instance_id

        raw_history = await self._adapter.get_history(engine_inst_id, tenant_id)
        return [
            WorkflowHistoryEntry(
                activity_id=h.activity_id,
                activity_name=h.activity_name,
                activity_type=h.activity_type,
                execution_id=h.execution_id,
                start_time=h.start_time,
                end_time=h.end_time,
                duration_ms=h.duration_ms,
                assignee=h.assignee,
                completed=h.is_completed,
            )
            for h in raw_history
        ]

    # ==========================================================================
    # 6. DMN Decision Table Operations (Phase 3B)
    # ==========================================================================

    async def deploy_decision_table(
        self,
        tenant_id: str,
        dmn_xml: str,
        resource_name: str,
        actor_id: str = "system",
    ) -> DmnDecisionMetadata:
        """Validate, fingerprint, and deploy a DMN decision table to the engine."""
        metadata = self._dmn_validator.validate(dmn_xml)
        if hasattr(self._adapter, "deploy_decision"):
            await self._adapter.deploy_decision(
                dmn_xml=dmn_xml,
                resource_name=resource_name,
                tenant_id=tenant_id,
            )

        async with self._uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.dmn.deployed",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method="SERVICE_INTERNAL",
                    resource_type="workflow_decision_table",
                    resource_id=metadata.decision_key,
                    outcome=AuditEventOutcome.SUCCESS,
                    details={
                        "decision_key": metadata.decision_key,
                        "hit_policy": metadata.hit_policy.value,
                        "rule_count": metadata.rule_count,
                        "artifact_hash": metadata.artifact_hash,
                    },
                )
            )
            await uow.commit()

        return metadata

    async def execute_decision_table(
        self,
        tenant_id: str,
        decision_key: str,
        variables: dict[str, Any],
        correlation_id: str | None = None,
        actor_id: str = "system",
    ) -> DmnExecutionResponse:
        """Deterministically execute a DMN decision table and record an append-only audit trail."""
        now = datetime.now(UTC)
        if hasattr(self._adapter, "execute_decision"):
            result_vars = await self._adapter.execute_decision(
                decision_key=decision_key,
                variables=variables,
                tenant_id=tenant_id,
            )
        else:
            result_vars = {}

        response = DmnExecutionResponse(
            decision_key=decision_key,
            result_variables=result_vars,
            evaluated_at=now,
        )

        async with self._uow_factory() as uow:
            audit_repo = AuditRepository(uow.session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.dmn.executed",
                    actor_principal_id=actor_id,
                    actor_principal_type=PrincipalType.SERVICE.value,
                    authentication_method="SERVICE_INTERNAL",
                    resource_type="workflow_decision_table",
                    resource_id=decision_key,
                    outcome=AuditEventOutcome.SUCCESS,
                    details={
                        "decision_key": decision_key,
                        "input_variables": variables,
                        "result_variables": result_vars,
                        "correlation_id": correlation_id,
                    },
                )
            )
            await uow.commit()

        return response

    # ==========================================================================
    # 7. Form-JS Operations (Phase 3B)
    # ==========================================================================

    def validate_form_definition(self, raw_json: str | dict[str, Any]) -> FormDefinitionSchema:
        """Screen and validate a form-js definition schema."""
        return self._form_validator.validate_form_schema(raw_json)

    def derive_form_variable_policy(self, form_def: FormDefinitionSchema) -> WorkflowVariablePolicy:
        """Derive an explicit variable policy from a form-js definition."""
        return self._form_validator.derive_variable_policy(form_def)

    def validate_form_submission(
        self,
        form_def: FormDefinitionSchema,
        submitted_vars: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate user task submission against the form-js schema before engine dispatch."""
        return self._form_validator.validate_submission(form_def, submitted_vars)

    # ==========================================================================
    # 8. Approval Compiler & Delegation (Phase 3B)
    # ==========================================================================

    def compile_approval_workflow(self, config: ApprovalWorkflowConfig) -> str:
        """Compile a typed approval configuration into standards-compliant BPMN 2.0 XML."""
        compiled_xml = self._approval_compiler.compile(config)
        self._bpmn_validator.validate(compiled_xml, allow_trusted_expressions=True)
        return compiled_xml

    # ==========================================================================
    # 9. SLA Tracking, Evaluation & Escalations (Phase 3B)
    # ==========================================================================

    def register_sla_policy(self, policy: SlaPolicyConfig) -> None:
        """Register an SLA policy specification."""
        self._sla_service.register_policy(policy)

    def attach_task_sla(
        self,
        tenant_id: str,
        instance_id: UUID,
        task_id: UUID,
        policy_key: str,
        created_at: datetime | None = None,
    ) -> TaskSlaState:
        """Attach an SLA policy to a canonical task."""
        return self._sla_service.attach_task_sla(
            tenant_id=tenant_id,
            instance_id=instance_id,
            task_id=task_id,
            policy_key=policy_key,
            created_at=created_at,
        )

    def pause_task_sla(self, tenant_id: str, task_id: UUID) -> TaskSlaState:
        """Pause SLA progression for a task."""
        return self._sla_service.pause_task_sla(tenant_id=tenant_id, task_id=task_id)

    def resume_task_sla(self, tenant_id: str, task_id: UUID) -> TaskSlaState:
        """Resume SLA progression for a task."""
        return self._sla_service.resume_task_sla(tenant_id=tenant_id, task_id=task_id)

    async def evaluate_sla_triggers(
        self,
        tenant_id: str,
        as_of: datetime | None = None,
    ) -> list[str]:
        """Evaluate active SLAs and emit warnings or breach escalations."""
        now = as_of or datetime.now(UTC)
        async with self._uow_factory() as uow:
            outbox_repo = OutboxRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            actions = await self._sla_service.evaluate_triggers(
                as_of=now,
                outbox_repo=outbox_repo,
                audit_repo=audit_repo,
            )
            await uow.commit()
            return actions
