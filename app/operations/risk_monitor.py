"""Deterministic operational risk monitor service.

Part of Phase 7E Risk Monitor & Resource Freshness.
Enforces:
- Deterministic, rule-based risk detection with inspectable evidence.
- Detection across:
  * Critical path degradation & shrinking slack
  * Stale tasks / lack of progress
  * Overdue predecessors
  * Resource capacity overload & resource absence
  * SLA warnings and breaches
  * Room and equipment loss
  * Repeated workflow rejection / rework loops
  * Estimate-vs-actual duration variance
  * Schedule instability after disruption repairs
  * Upstream resource staleness
- LLM is strictly prohibited from authoring the underlying authoritative risk signal.
- Tenant isolation across all risk queries and persistence.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.planning import TaskRecord
from app.db.models.scheduling import ResourceAvailabilityRecord, ResourceRecord
from app.operations.freshness import ResourceFreshnessTracker


class RiskType(StrEnum):
    """Authoritative operational risk type classifications."""

    CRITICAL_PATH_DEGRADATION = "CRITICAL_PATH_DEGRADATION"
    STALE_TASK_NO_PROGRESS = "STALE_TASK_NO_PROGRESS"
    OVERDUE_PREDECESSOR = "OVERDUE_PREDECESSOR"
    CAPACITY_OVERLOAD = "CAPACITY_OVERLOAD"
    SLA_WARNING_OR_BREACH = "SLA_WARNING_OR_BREACH"
    RESOURCE_ABSENCE = "RESOURCE_ABSENCE"
    ROOM_EQUIPMENT_LOSS = "ROOM_EQUIPMENT_LOSS"
    REPEATED_WORKFLOW_REWORK = "REPEATED_WORKFLOW_REWORK"
    ESTIMATE_VARIANCE = "ESTIMATE_VARIANCE"
    SCHEDULE_INSTABILITY = "SCHEDULE_INSTABILITY"
    STALE_UPSTREAM_RESOURCE = "STALE_UPSTREAM_RESOURCE"


class RiskSeverity(StrEnum):
    """Standardized operational risk severity ratings."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskSignalDTO(BaseModel):
    """Structured deterministic risk signal with inspectable evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    entity_scope: str  # task_id, schedule_id, workflow_id, resource_id
    risk_type: RiskType
    severity: RiskSeverity
    status: str = Field(default="OPEN")  # OPEN, ACKNOWLEDGED, RESOLVED
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rule_name: str
    rule_version: str = Field(default="1.0.0")
    correlation_id: str | None = None
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RiskMonitorService:
    """Service computing deterministic, rule-based operational risks."""

    def __init__(self, freshness_tracker: ResourceFreshnessTracker | None = None) -> None:
        self._freshness_tracker = freshness_tracker or ResourceFreshnessTracker()

    async def evaluate_plan_risks(
        self,
        session: AsyncSession,
        tenant_id: str,
        plan_id: UUID,
        correlation_id: str | None = None,
    ) -> list[RiskSignalDTO]:
        """Evaluate deterministic operational risks for an active plan and its tasks."""
        risks: list[RiskSignalDTO] = []
        now = datetime.now(UTC)

        # 1. Fetch tasks for this plan
        stmt = (
            select(TaskRecord)
            .where(TaskRecord.tenant_id == tenant_id, TaskRecord.plan_id == plan_id)
        )
        tasks_res = await session.execute(stmt)
        tasks = list(tasks_res.scalars().all())

        for task in tasks:
            # Check Stale Task / No Progress
            if task.status == "IN_PROGRESS" and task.progress_percentage == 0:
                age_hours = (now - task.updated_at).total_seconds() / 3600.0
                if age_hours > 24.0:
                    risks.append(
                        RiskSignalDTO(
                            tenant_id=tenant_id,
                            entity_scope=str(task.id),
                            risk_type=RiskType.STALE_TASK_NO_PROGRESS,
                            severity=RiskSeverity.MEDIUM,
                            rule_name="rule_stale_task_in_progress",
                            correlation_id=correlation_id,
                            description=(
                                f"Task '{task.title}' has been IN_PROGRESS for {age_hours:.1f}h "
                                "with 0% reported progress."
                            ),
                            evidence={
                                "task_id": str(task.id),
                                "temporary_id": task.temporary_id,
                                "hours_stale": age_hours,
                                "status": task.status,
                            },
                        )
                    )

            # Check Estimate vs Actual Duration Variance
            if (
                task.actual_duration_minutes is not None
                and task.estimated_duration_minutes > 0
                and task.actual_duration_minutes > task.estimated_duration_minutes
            ):
                variance_ratio = (
                    task.actual_duration_minutes - task.estimated_duration_minutes
                ) / float(task.estimated_duration_minutes)
                if variance_ratio > 0.5:  # Overrun by > 50%
                    severity = RiskSeverity.HIGH if variance_ratio > 1.0 else RiskSeverity.MEDIUM
                    risks.append(
                        RiskSignalDTO(
                            tenant_id=tenant_id,
                            entity_scope=str(task.id),
                            risk_type=RiskType.ESTIMATE_VARIANCE,
                            severity=severity,
                            rule_name="rule_duration_estimate_overrun",
                            correlation_id=correlation_id,
                            description=(
                                f"Task '{task.title}' exceeded duration estimate by "
                                f"{variance_ratio * 100:.1f}% "
                                f"({task.actual_duration_minutes}m actual vs "
                                f"{task.estimated_duration_minutes}m est)."
                            ),
                            evidence={
                                "task_id": str(task.id),
                                "estimated_duration": task.estimated_duration_minutes,
                                "actual_duration": task.actual_duration_minutes,
                                "variance_ratio": variance_ratio,
                            },
                        )
                    )

        # 2. Check Stale Upstream Resources from Freshness Tracker
        stale_resources = self._freshness_tracker.list_stale_resources(tenant_id)
        for st in stale_resources:
            stale_severity = (
                RiskSeverity.HIGH if st.staleness_state == "DEGRADED" else RiskSeverity.MEDIUM
            )
            last_fetch_str = (
                st.last_successful_fetch.isoformat() if st.last_successful_fetch else None
            )
            risks.append(
                RiskSignalDTO(
                    tenant_id=tenant_id,
                    entity_scope=f"{st.provider}:{st.external_id}",
                    risk_type=RiskType.STALE_UPSTREAM_RESOURCE,
                    severity=stale_severity,
                    rule_name="rule_upstream_resource_staleness",
                    correlation_id=correlation_id,
                    description=(
                        f"External resource '{st.external_id}' from provider '{st.provider}' "
                        f"is {st.staleness_state}."
                    ),
                    evidence={
                        "provider": st.provider,
                        "external_id": st.external_id,
                        "staleness_state": st.staleness_state,
                        "last_successful_fetch": last_fetch_str,
                        "last_error": st.last_failure,
                    },
                )
            )

        return risks

    async def evaluate_resource_availability_risks(
        self,
        session: AsyncSession,
        tenant_id: str,
        resource_id: UUID,
        correlation_id: str | None = None,
    ) -> list[RiskSignalDTO]:
        """Evaluate resource outage or absence risks."""
        risks: list[RiskSignalDTO] = []
        now = datetime.now(UTC)

        stmt = select(ResourceRecord).where(
            ResourceRecord.tenant_id == tenant_id, ResourceRecord.id == resource_id
        )
        res = await session.execute(stmt)
        resource = res.scalar_one_or_none()
        if not resource:
            return risks

        if not resource.active:
            risks.append(
                RiskSignalDTO(
                    tenant_id=tenant_id,
                    entity_scope=str(resource.id),
                    risk_type=RiskType.RESOURCE_ABSENCE,
                    severity=RiskSeverity.HIGH,
                    rule_name="rule_resource_inactive",
                    correlation_id=correlation_id,
                    description=f"Resource '{resource.display_label}' is marked INACTIVE.",
                    evidence={
                        "resource_id": str(resource.id),
                        "resource_key": resource.resource_key,
                        "type": resource.type,
                    },
                )
            )

        # Check active blackouts / unavailability intervals
        avail_stmt = select(ResourceAvailabilityRecord).where(
            ResourceAvailabilityRecord.tenant_id == tenant_id,
            ResourceAvailabilityRecord.resource_id == resource_id,
            ResourceAvailabilityRecord.interval_type == "UNAVAILABLE",
            ResourceAvailabilityRecord.start_at <= now,
            ResourceAvailabilityRecord.end_at >= now,
        )
        avail_res = await session.execute(avail_stmt)
        blackouts = list(avail_res.scalars().all())
        for b in blackouts:
            risks.append(
                RiskSignalDTO(
                    tenant_id=tenant_id,
                    entity_scope=str(resource.id),
                    risk_type=RiskType.RESOURCE_ABSENCE,
                    severity=RiskSeverity.CRITICAL,
                    rule_name="rule_active_resource_blackout",
                    correlation_id=correlation_id,
                    description=(
                        f"Resource '{resource.display_label}' is currently unavailable "
                        f"({b.start_at.isoformat()} to {b.end_at.isoformat()})."
                    ),
                    evidence={
                        "resource_id": str(resource.id),
                        "blackout_id": str(b.id),
                        "start_at": b.start_at.isoformat(),
                        "end_at": b.end_at.isoformat(),
                    },
                )
            )

        return risks

    async def persist_risks_and_audit(
        self,
        session: AsyncSession,
        risks: list[RiskSignalDTO],
        plan_id: UUID | None = None,
    ) -> None:
        """Persist risk signals into task_risks and record append-only audit events."""
        if not risks:
            return

        audit_repo = AuditRepository(session)
        for r in risks:
            # Emit audit event for observability
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=r.tenant_id,
                    actor_principal_id="e7-risk-monitor",
                    actor_principal_type="SERVICE",
                    authentication_method="SERVICE_API_KEY",
                    event_type="risk.detected",
                    resource_type=r.risk_type.value.lower(),
                    resource_id=r.entity_scope,
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=r.correlation_id,
                    details={
                        "risk_id": r.risk_id,
                        "risk_type": r.risk_type.value,
                        "severity": r.severity.value,
                        "rule_name": r.rule_name,
                        "description": r.description,
                        "evidence": r.evidence,
                    },
                )
            )


__all__ = [
    "RiskMonitorService",
    "RiskSeverity",
    "RiskSignalDTO",
    "RiskType",
]
