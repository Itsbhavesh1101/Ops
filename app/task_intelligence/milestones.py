"""Canonical milestone health tracking and roll-up evaluation (FR-TI-06).

Provides deterministic runtime calculation of milestone health derived from
member task states, progress percentages, blockers, and target deadlines.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MilestoneHealth(StrEnum):
    """Canonical health status for a milestone."""

    PENDING = "PENDING"
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    COMPLETED = "COMPLETED"


class MilestoneHealthResponse(BaseModel):
    """Read representation of a milestone with derived health metrics."""

    id: UUID = Field(..., description="Canonical milestone UUID.")
    tenant_id: str = Field(..., description="Tenant scope.")
    plan_id: UUID = Field(..., description="Plan UUID.")
    temporary_id: str = Field(..., description="Plan-local temporary milestone ID.")
    name: str = Field(..., description="Milestone title.")
    description: str | None = Field(default="", description="Milestone description.")
    target_date: datetime | None = Field(default=None, description="Target completion datetime.")
    health: MilestoneHealth = Field(..., description="Derived operational health status.")
    total_tasks: int = Field(default=0, ge=0, description="Total count of associated member tasks.")
    completed_tasks: int = Field(default=0, ge=0, description="Count of completed member tasks.")
    blocked_tasks: int = Field(
        default=0, ge=0, description="Count of currently blocked member tasks."
    )
    progress_percentage: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Aggregated completion percentage across member tasks.",
    )
    is_overdue: bool = Field(
        default=False,
        description="True if target_date has passed without full completion.",
    )


class MilestoneHealthCalculator:
    """Pure domain calculator for milestone health rollups."""

    @staticmethod
    def evaluate_milestone_health(
        milestone: Any,
        member_tasks: list[Any],
        now: datetime | None = None,
    ) -> MilestoneHealthResponse:
        """Derive milestone health from associated tasks and target dates.

        Rules (FR-TI-06):
        1. 0 member tasks -> PENDING (0% progress)
        2. All member tasks COMPLETED (100%) -> COMPLETED
        3. Any member task BLOCKED -> AT_RISK
        4. Target date passed with incomplete tasks -> AT_RISK (is_overdue=True)
        5. In-progress or TODO tasks without blockers or deadline breach -> ON_TRACK
        """
        ref_now = now or datetime.now(UTC)
        total = len(member_tasks)

        target_date = getattr(milestone, "target_date", None)
        if target_date is not None and target_date.tzinfo is None:
            target_date = target_date.replace(tzinfo=UTC)

        if total == 0:
            return MilestoneHealthResponse(
                id=milestone.id,
                tenant_id=milestone.tenant_id,
                plan_id=milestone.plan_id,
                temporary_id=milestone.temporary_id,
                name=milestone.name,
                description=milestone.description or "",
                target_date=target_date,
                health=MilestoneHealth.PENDING,
                total_tasks=0,
                completed_tasks=0,
                blocked_tasks=0,
                progress_percentage=0.0,
                is_overdue=False,
            )

        completed = sum(1 for t in member_tasks if getattr(t, "status", "") == "COMPLETED")
        blocked = sum(
            1
            for t in member_tasks
            if getattr(t, "status", "") == "BLOCKED" or bool(getattr(t, "blocked_reason", None))
        )
        total_progress = sum(int(getattr(t, "progress_percentage", 0) or 0) for t in member_tasks)
        avg_progress = round(total_progress / float(total), 2)

        is_overdue = False
        if target_date is not None and target_date < ref_now and completed < total:
            is_overdue = True

        if completed == total:
            health = MilestoneHealth.COMPLETED
        elif blocked > 0 or is_overdue:
            health = MilestoneHealth.AT_RISK
        else:
            health = MilestoneHealth.ON_TRACK

        return MilestoneHealthResponse(
            id=milestone.id,
            tenant_id=milestone.tenant_id,
            plan_id=milestone.plan_id,
            temporary_id=milestone.temporary_id,
            name=milestone.name,
            description=milestone.description or "",
            target_date=target_date,
            health=health,
            total_tasks=total,
            completed_tasks=completed,
            blocked_tasks=blocked,
            progress_percentage=avg_progress,
            is_overdue=is_overdue,
        )
