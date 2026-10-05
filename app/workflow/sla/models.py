"""Domain models and schemas for SLA Policies, Timers, and Escalations (Phase 3B).

Defines:
- SlaStatus: RUNNING, WARNING, BREACHED, PAUSED, COMPLETED
- EscalationAction: REMINDER, REASSIGN, ESCALATE, SERVICE_ACTION
- SlaPolicyConfig: typed specification for warning and breach thresholds
- TaskSlaState: active tracking of warning_at, breach_at, pause durations
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SlaStatus(StrEnum):
    """Runtime status of an active task SLA."""

    RUNNING = "RUNNING"
    WARNING = "WARNING"
    BREACHED = "BREACHED"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class EscalationAction(StrEnum):
    """Actions triggered when an SLA warning or breach threshold is reached."""

    REMINDER = "REMINDER"
    REASSIGN = "REASSIGN"
    ESCALATE = "ESCALATE"
    SERVICE_ACTION = "SERVICE_ACTION"


class SlaEscalationRule(BaseModel):
    """Escalation behavior attached to an SLA trigger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: EscalationAction = Field(..., description="Action to execute")
    target_role: str | None = Field(
        default=None, description="Target role if action is REASSIGN or ESCALATE"
    )
    target_principal_id: str | None = Field(
        default=None, description="Explicit target principal if configured"
    )
    service_command_type: str | None = Field(
        default=None, description="Service action command type if action is SERVICE_ACTION"
    )


class SlaPolicyConfig(BaseModel):
    """Specification of warning and breach thresholds with business calendar rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_key: str = Field(..., description="Unique policy identifier")
    name: str = Field(..., description="Human-readable policy name")
    version: int = Field(default=1, ge=1, description="Policy version")
    calendar_ref: str = Field(
        default="CALENDAR_24X7", description="Business calendar reference code"
    )
    warning_duration_seconds: int = Field(
        ..., ge=1, description="Seconds from task creation until warning fires"
    )
    breach_duration_seconds: int = Field(
        ..., ge=1, description="Seconds from task creation until breach fires"
    )
    warning_escalation: SlaEscalationRule = Field(
        default_factory=lambda: SlaEscalationRule(action=EscalationAction.REMINDER),
        description="Escalation triggered upon warning",
    )
    breach_escalation: SlaEscalationRule = Field(
        default_factory=lambda: SlaEscalationRule(action=EscalationAction.ESCALATE),
        description="Escalation triggered upon breach",
    )


class TaskSlaState(BaseModel):
    """Authoritative projection of SLA tracking for a canonical task."""

    model_config = ConfigDict(extra="ignore")

    id: UUID = Field(default_factory=uuid4, description="SLA record ID")
    task_id: UUID = Field(..., description="Canonical task UUID")
    instance_id: UUID = Field(..., description="Canonical instance UUID")
    tenant_id: str = Field(..., description="Tenant ID")
    policy_key: str = Field(..., description="Associated SLA policy key")
    status: SlaStatus = Field(default=SlaStatus.RUNNING, description="Current SLA status")
    created_at: datetime = Field(..., description="Task creation timestamp")
    warning_at: datetime = Field(..., description="Computed warning threshold")
    breach_at: datetime = Field(..., description="Computed breach threshold")
    paused_at: datetime | None = Field(default=None, description="Timestamp when SLA was paused")
    total_paused_seconds: int = Field(
        default=0, description="Cumulative seconds spent in paused state"
    )
    remaining_warning_seconds: int | None = Field(
        default=None, description="Preserved remaining business seconds until warning"
    )
    remaining_breach_seconds: int | None = Field(
        default=None, description="Preserved remaining business seconds until breach"
    )
    warning_fired_at: datetime | None = Field(
        default=None, description="Timestamp when warning was emitted"
    )
    breach_fired_at: datetime | None = Field(
        default=None, description="Timestamp when breach was recorded"
    )
