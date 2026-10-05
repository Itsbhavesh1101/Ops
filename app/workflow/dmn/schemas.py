"""Pydantic schemas and typed contracts for DMN 1.3 decision tables (Phase 3B).

Defines:
- DmnHitPolicy enum (supported hit policies)
- DmnDecisionMetadata (parsed semantic information from validated DMN XML)
- DmnExecutionRequest / DmnExecutionResponse (deterministic rule evaluation)
- DmnArtifactRegisterRequest / DmnArtifactResponse (versioned canonical storage)
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DmnHitPolicy(StrEnum):
    """Supported DMN decision table hit policies."""

    UNIQUE = "UNIQUE"
    FIRST = "FIRST"
    RULE_ORDER = "RULE_ORDER"
    COLLECT = "COLLECT"


class DmnInputClause(BaseModel):
    """Input definition in a DMN decision table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="XML ID of the input clause")
    label: str | None = Field(default=None, description="Human-readable input label")
    expression: str = Field(..., description="Variable name evaluated by this input")
    type_ref: str = Field(
        default="string", description="Expected data type (string, integer, boolean, double)"
    )


class DmnOutputClause(BaseModel):
    """Output definition in a DMN decision table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="XML ID of the output clause")
    name: str = Field(..., description="Variable name produced by this output")
    label: str | None = Field(default=None, description="Human-readable output label")
    type_ref: str = Field(
        default="string", description="Produced data type (string, integer, boolean, double)"
    )


class DmnDecisionMetadata(BaseModel):
    """Extracted semantic metadata from a validated DMN 1.3 XML definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_key: str = Field(..., description="Executable decision ID")
    decision_name: str = Field(..., description="Human-readable decision name")
    hit_policy: DmnHitPolicy = Field(..., description="Evaluated hit policy")
    inputs: list[DmnInputClause] = Field(default_factory=list, description="Defined input clauses")
    outputs: list[DmnOutputClause] = Field(
        default_factory=list, description="Defined output clauses"
    )
    rule_count: int = Field(default=0, description="Total number of rules declared in the table")
    artifact_hash: str = Field(..., description="SHA-256 fingerprint of the DMN XML")


class DmnExecutionRequest(BaseModel):
    """Request to deterministically execute a deployed DMN decision table."""

    model_config = ConfigDict(extra="forbid")

    decision_key: str = Field(..., description="Unique key of the decision table to execute")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Input variables conforming to decision table inputs"
    )
    correlation_id: str | None = Field(
        default=None, description="Optional business or instance correlation ID for audit tracing"
    )


class DmnExecutionResponse(BaseModel):
    """Deterministic result of a DMN decision table evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_key: str = Field(..., description="Executed decision key")
    result_variables: dict[str, Any] = Field(
        ..., description="Result variables produced by the matching rule(s)"
    )
    evaluated_at: datetime = Field(..., description="UTC timestamp of evaluation")
    artifact_hash: str | None = Field(
        default=None, description="Fingerprint of evaluated DMN artifact"
    )


class DmnArtifactRegisterRequest(BaseModel):
    """Request to store and validate a new versioned DMN decision table."""

    model_config = ConfigDict(extra="forbid")

    artifact_key: str = Field(..., description="Stable business key for the decision table")
    name: str = Field(..., description="Human-readable artifact name")
    version: int = Field(default=1, ge=1, description="Version sequence number")
    dmn_xml: str = Field(..., description="Raw DMN 1.3 XML definition")
    description: str | None = Field(default=None, description="Optional documentation description")


class DmnArtifactResponse(BaseModel):
    """Canonical representation of a stored versioned DMN artifact."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    tenant_id: str
    artifact_type: str = "DMN"
    artifact_key: str
    name: str
    version: int
    artifact_hash: str
    engine_deployment_id: str | None = None
    engine_reference_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
