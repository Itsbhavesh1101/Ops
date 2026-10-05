"""Goal intake and response schemas for Task Intelligence (FR-TI-01).

Implements provider-neutral goal intake supporting objective, scope,
target window, constraints, and opaque context references.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TargetWindow(BaseModel):
    """Optional operational temporal window for goal execution."""

    start_at: datetime | None = Field(
        default=None,
        description="Operational start boundary.",
    )
    end_at: datetime | None = Field(
        default=None,
        description="Operational deadline or end boundary.",
    )


class GoalCreate(BaseModel):
    """Canonical goal intake payload (FR-TI-01).

    Provider-neutral: Context references are opaque strings, not raw MySQL IDs.
    """

    objective: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="High-level operational objective to achieve.",
    )
    scope: str | None = Field(
        default=None,
        max_length=2000,
        description="Operational scope or boundary description.",
    )
    deadline: datetime | None = Field(
        default=None,
        description="Target completion datetime.",
    )
    target_window: TargetWindow | None = Field(
        default=None,
        description="Target execution window with start and end times.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Operational constraints (e.g., policy rules, blackout windows).",
    )
    resource_scope: list[str] = Field(
        default_factory=list,
        description="Broad resource categories required (e.g., ['ROOM', 'INVIGILATOR']).",
    )
    context_references: list[str] = Field(
        default_factory=list,
        description="Opaque, provider-neutral references to external context assets.",
    )
    priority_target: str | None = Field(
        default=None,
        description="Optional high-level priority target.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional operational metadata.",
    )


class GoalResponse(BaseModel):
    """Canonical goal representation returned by E-7 services."""

    id: UUID = Field(
        ...,
        description="Canonical E-7 UUID identifier.",
    )
    tenant_id: str = Field(
        ...,
        description="Authoritative tenant identifier.",
    )
    objective: str = Field(
        ...,
        description="High-level operational objective.",
    )
    scope: str | None = Field(
        default=None,
        description="Operational scope description.",
    )
    deadline: datetime | None = Field(
        default=None,
        description="Target completion datetime.",
    )
    target_window: TargetWindow | None = Field(
        default=None,
        description="Target operational window.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Operational constraints.",
    )
    resource_scope: list[str] = Field(
        default_factory=list,
        description="Resource categories involved.",
    )
    context_references: list[str] = Field(
        default_factory=list,
        description="Opaque context references.",
    )
    priority_target: str | None = Field(
        default=None,
        description="Priority target.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Operational metadata.",
    )
    created_at: datetime = Field(
        ...,
        description="Creation timestamp.",
    )
    updated_at: datetime = Field(
        ...,
        description="Last update timestamp.",
    )
