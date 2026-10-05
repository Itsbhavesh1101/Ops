"""Domain models and configuration schemas for Advanced Approval Models (Phase 3B).

Supports:
- ApprovalType: SINGLE, SEQUENTIAL, PARALLEL_ALL_OF, QUORUM_N_OF_M, ROLE_POOL
- RejectionPolicy: ANY_REJECTION_FAILS, QUORUM_REJECTION
- ApprovalStepConfig and ApprovalWorkflowConfig
- DelegationGrant for explicit, auditable, time-bounded authority transfer
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ApprovalType(StrEnum):
    """Supported multi-party approval patterns."""

    SINGLE = "SINGLE"
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL_ALL_OF = "PARALLEL_ALL_OF"
    QUORUM_N_OF_M = "QUORUM_N_OF_M"
    ROLE_POOL = "ROLE_POOL"


ApprovalPattern = ApprovalType


class RejectionPolicy(StrEnum):
    """Deterministic handling of task rejections."""

    ANY_REJECTION_FAILS = "ANY_REJECTION_FAILS"
    QUORUM_REJECTION = "QUORUM_REJECTION"


class ApprovalStepConfig(BaseModel):
    """Configuration for an individual approval step or quorum pool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str | None = Field(None, description="Step ID")
    step_key: str | None = Field(None, description="Unique step identifier in the workflow")
    step_name: str | None = Field(None, description="Human-readable step name")
    name: str | None = Field(None, description="Human-readable step name")
    pattern: ApprovalType | None = Field(None, description="Step approval pattern")
    assignee: str | None = Field(None, description="Explicit single assignee")
    candidate_roles: list[str] = Field(
        default_factory=list, description="Roles eligible to act on this step"
    )
    assignees: list[str] = Field(
        default_factory=list, description="Explicit principals assigned to this step"
    )
    form_key: str | None = Field(None, description="Optional form-js artifact key for this task")
    sla_policy_key: str | None = Field(
        None, description="Optional SLA policy key attached to this step"
    )
    required_quorum: int | None = Field(None, description="Required quorum for QUORUM_N_OF_M step")

    @property
    def effective_id(self) -> str:
        return self.step_id or self.step_key or "approval_step"

    @property
    def effective_name(self) -> str:
        return self.step_name or self.name or "Approval Step"


class ApprovalWorkflowConfig(BaseModel):
    """Specification used by the compiler to generate standard BPMN 2.0 XML."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    process_key: str | None = Field(None, description="Process definition key")
    workflow_key: str | None = Field(None, description="Process definition key")
    process_name: str | None = Field(None, description="Human-readable workflow name")
    name: str | None = Field(None, description="Human-readable workflow name")
    approval_type: ApprovalType | None = Field(None, description="High-level approval pattern")
    rejection_policy: RejectionPolicy = Field(
        RejectionPolicy.ANY_REJECTION_FAILS, description="Rejection evaluation strategy"
    )
    steps: list[ApprovalStepConfig] = Field(
        default_factory=list, description="Ordered list of approval steps"
    )
    quorum_count: int | None = Field(
        None, ge=1, description="Required approval count for QUORUM_N_OF_M"
    )
    total_participants: int | None = Field(
        None, ge=1, description="Total pool size M for QUORUM_N_OF_M"
    )

    @property
    def effective_key(self) -> str:
        return self.process_key or self.workflow_key or "approval_process"

    @property
    def effective_name(self) -> str:
        return self.process_name or self.name or "Approval Process"


class DelegationGrant(BaseModel):
    """Explicit, auditable delegation of approval authority."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique delegation grant ID")
    tenant_id: str = Field(..., description="Owning tenant ID")
    delegator_id: str = Field(..., description="Original principal delegating authority")
    delegatee_id: str = Field(..., description="Recipient principal receiving delegated authority")
    valid_from: datetime = Field(..., description="Start of delegation validity window")
    valid_until: datetime = Field(..., description="End of delegation validity window")
    workflow_key: str | None = Field(None, description="Optional workflow key scope")
    allowed_workflow_keys: list[str] = Field(
        default_factory=list, description="Scope of workflow keys (empty for all in tenant)"
    )
    allowed_roles: list[str] = Field(default_factory=list, description="Scope of roles delegated")
    is_active: bool = Field(True, description="Whether grant is active")

    @property
    def grant_id(self) -> UUID:
        return self.id
