"""Business event handler consuming canonical plan.approved events.

Part of Phase 6 Unified Vertical Slice.
Enforces:
- Idempotent workflow start: duplicates on the same plan do NOT create duplicate instances.
- Preserves canonical references: tenant_id, plan_id, goal_id, correlation_id.
- Initiates configured Flowable workflow instance via WorkflowService.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.workflow import WorkflowInstanceRecord
from app.events.schemas import EventEnvelope
from app.workflow.schemas import WorkflowInstanceResponse
from app.workflow.service import (
    MissingWorkflowKeyError,
    WorkflowService,
)


class PlanApprovedWorkflowHandler:
    """Consumer handler for canonical plan.approved event."""

    def __init__(self, workflow_service: WorkflowService | None = None) -> None:
        self.workflow_service = workflow_service

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> WorkflowInstanceRecord | WorkflowInstanceResponse | None:
        """Handle plan.approved event idempotently within transaction.

        Flow:
        1. Validate tenant_id and plan_id from envelope.
        2. Validate required non-empty workflow_key from payload (fail closed).
        3. Query existing workflow instances matching business_reference = plan_id.
        4. If already present: return immediately (idempotent no-op).
        5. Start configured workflow process instance via WorkflowService.
        """
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return None

        payload = envelope.payload
        plan_id_raw = payload.get("plan_id")
        if not plan_id_raw:
            return None

        workflow_key_raw = payload.get("workflow_key")
        if workflow_key_raw is None or not str(workflow_key_raw).strip():
            raise MissingWorkflowKeyError(
                "plan.approved payload is missing required non-empty 'workflow_key'."
            )
        workflow_key = str(workflow_key_raw).strip()

        plan_id = str(plan_id_raw)
        goal_id = str(payload.get("goal_id", ""))

        # Idempotency check: does a workflow instance already exist for this plan?
        stmt = select(WorkflowInstanceRecord).where(
            WorkflowInstanceRecord.tenant_id == tenant_id,
            WorkflowInstanceRecord.business_reference == plan_id,
        )
        res = await session.execute(stmt)
        existing_instance = res.scalar_one_or_none()
        if existing_instance:
            # Duplicate plan.approved delivery: idempotent return
            return existing_instance

        if self.workflow_service is None:
            # Service not injected: record diagnostic audit and return
            audit_repo = AuditRepository(session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="workflow.service_unbound",
                    resource_type="plan",
                    resource_id=plan_id,
                    outcome=AuditEventOutcome.FAILURE,
                    correlation_id=envelope.correlation_id,
                    details={"reason": "WorkflowService not bound to handler"},
                )
            )
            return None

        # Build workflow variables, forwarding extra payload variables if provided
        wf_vars: dict[str, Any] = {
            "plan_id": plan_id,
            "goal_id": goal_id,
            "workflow_key": workflow_key,
        }
        if "variables" in payload and isinstance(payload["variables"], dict):
            wf_vars.update(payload["variables"])
        for k in ("sla_warning_at", "sla_breach_at", "curriculum_coverage"):
            if k in payload and k not in wf_vars:
                wf_vars[k] = payload[k]

        # Start workflow instance on Flowable runtime
        return await self.workflow_service.start_instance(
            tenant_id=tenant_id,
            workflow_key=workflow_key,
            business_reference=plan_id,
            correlation_id=envelope.correlation_id,
            variables=wf_vars,
        )


__all__ = ["MissingWorkflowKeyError", "PlanApprovedWorkflowHandler"]
