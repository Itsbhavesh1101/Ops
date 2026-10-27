"""Deterministic Next-Best-Task Recommendation Engine (FR-TI-09).

Evaluates operational task readiness, critical-path slack, priority, milestone
proximity, and dependency constraints to produce explainable, ranked, and
non-authoritative recommendations for human planners and operators.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.schemas import TaskPriority, TaskProgressStatus


class RecommendationAction(StrEnum):
    """Actionable recommendation categories."""

    START_TASK = "START_TASK"
    CONTINUE_EXECUTION = "CONTINUE_EXECUTION"
    RESOLVE_BLOCKER = "RESOLVE_BLOCKER"
    EXPEDITE_CRITICAL = "EXPEDITE_CRITICAL"


class RecommendationQuality(StrEnum):
    """Explicit indicator of recommendation basis quality without fabricated metrics."""

    HIGH = "HIGH"  # Derived deterministically from complete DAG and CPM calculations
    MEDIUM = "MEDIUM"  # Computed with partial milestone or schedule float data
    DEGRADED = "DEGRADED"  # Incomplete dependency structure or missing graph metrics


class TaskRecommendation(BaseModel):
    """Ranked next-best-task recommendation item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    temporary_id: str
    title: str
    rank: int = Field(..., ge=1, description="1-based relative recommendation rank.")
    score: float = Field(..., ge=0.0, le=1.0, description="Deterministic composite priority score.")
    action: RecommendationAction
    priority: str
    status: str
    is_critical_path: bool
    total_slack_minutes: int | None
    explanation: str
    quality: RecommendationQuality
    quality_basis: str
    blocked_reasons: list[str] = Field(default_factory=list)


class PlanRecommendationsResponse(BaseModel):
    """Top-level recommendation collection envelope for a plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: UUID
    tenant_id: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_authoritative: bool = Field(
        default=False,
        description="Always False. Recommendations are strictly advisory and require human action.",
    )
    total_tasks: int
    ready_tasks_count: int
    blocked_tasks_count: int
    completed_tasks_count: int
    recommendations: list[TaskRecommendation]


class TaskRecommender:
    """Production-grade deterministic next-best-task recommender.

    Invariants:
    - Pure calculation: zero state mutation.
    - Tenant-scoped: operates strictly on data for the caller's tenant.
    - Deterministic: equivalent inputs always produce identical rank ordering and scores.
    - Explainable: every recommendation includes a structured explanation of why it was chosen.
    - Non-authoritative: clearly marked as advisory suggestions.
    """

    @classmethod
    def recommend(
        cls,
        *,
        plan_id: UUID,
        tenant_id: str,
        tasks: list[Any],
        dependencies: list[Any],
        cpm_result: CPMScheduleResult | None = None,
        milestones: list[Any] | None = None,
        limit: int = 10,
        include_blocked: bool = False,
    ) -> PlanRecommendationsResponse:
        """Generate ranked recommendations for a plan's tasks."""
        now = datetime.now(UTC)

        # 1. Build lookup tables
        task_by_id: dict[Any, Any] = {}
        task_by_temp_id: dict[str, Any] = {}
        uuid_to_temp: dict[Any, str] = {}

        for t in tasks:
            t_uid = getattr(t, "id", None)
            t_temp = getattr(t, "temporary_id", None) or str(t_uid)
            if t_uid:
                task_by_id[t_uid] = t
                task_by_id[str(t_uid)] = t
                uuid_to_temp[t_uid] = t_temp
                uuid_to_temp[str(t_uid)] = t_temp
            task_by_temp_id[t_temp] = t

        # 2. Build dependency predecessor map: child_temp_id -> list[pred_temp_id]
        predecessors_of: dict[str, list[str]] = {t_temp: [] for t_temp in task_by_temp_id}
        for d in dependencies:
            pred_raw = getattr(d, "predecessor_id", None)
            succ_raw = getattr(d, "successor_id", None)

            pred_temp = uuid_to_temp.get(pred_raw, str(pred_raw))
            succ_temp = uuid_to_temp.get(succ_raw, str(succ_raw))

            if succ_temp in predecessors_of and pred_temp in task_by_temp_id:
                predecessors_of[succ_temp].append(pred_temp)

        # 3. Categorize task status counts
        total_tasks = len(tasks)
        completed_count = 0
        blocked_count = 0
        ready_count = 0

        # Evaluate readiness per task
        task_readiness: dict[str, bool] = {}
        task_block_reasons: dict[str, list[str]] = {}

        for t_temp, t in task_by_temp_id.items():
            st = str(getattr(t, "status", TaskProgressStatus.TODO))
            blocked_reason = getattr(t, "blocked_reason", None)

            if st == TaskProgressStatus.COMPLETED:
                completed_count += 1
                task_readiness[t_temp] = False
                continue

            reasons: list[str] = []
            if st == TaskProgressStatus.BLOCKED:
                reasons.append(blocked_reason or "Task marked BLOCKED")

            # Check predecessors
            preds = predecessors_of.get(t_temp, [])
            uncompleted_preds = []
            blocked_preds = []

            for p_temp in preds:
                p_task = task_by_temp_id.get(p_temp)
                if not p_task:
                    continue
                p_st = str(getattr(p_task, "status", TaskProgressStatus.TODO))
                if p_st == TaskProgressStatus.BLOCKED:
                    blocked_preds.append(p_temp)
                elif p_st != TaskProgressStatus.COMPLETED:
                    uncompleted_preds.append(p_temp)

            if blocked_preds:
                reasons.append(
                    f"Waiting on blocked predecessor(s): {', '.join(sorted(blocked_preds))}"
                )

            if uncompleted_preds:
                reasons.append(
                    f"Waiting on uncompleted predecessor(s): {', '.join(sorted(uncompleted_preds))}"
                )

            task_block_reasons[t_temp] = reasons

            if reasons or st == TaskProgressStatus.BLOCKED:
                blocked_count += 1
                task_readiness[t_temp] = False
            else:
                ready_count += 1
                task_readiness[t_temp] = True

        # 4. Rank and score candidates
        candidates: list[dict[str, Any]] = []
        has_cpm = cpm_result is not None and bool(cpm_result.schedule)

        for t_temp, t in task_by_temp_id.items():
            st = str(getattr(t, "status", TaskProgressStatus.TODO))
            if st == TaskProgressStatus.COMPLETED:
                continue

            is_ready = task_readiness.get(t_temp, False)
            if not is_ready and not include_blocked:
                continue

            # Critical path and slack metrics
            is_critical = False
            total_slack: int | None = None
            if has_cpm and cpm_result:
                node = cpm_result.schedule.get(t_temp)
                if node:
                    is_critical = node.is_critical
                    total_slack = node.total_slack

            # Compute priority score
            prio_str = str(getattr(t, "priority", TaskPriority.MEDIUM)).upper()
            if prio_str == TaskPriority.CRITICAL:
                priority_factor = 1.0
            elif prio_str == TaskPriority.HIGH:
                priority_factor = 0.75
            elif prio_str == TaskPriority.MEDIUM:
                priority_factor = 0.50
            else:
                priority_factor = 0.25

            # Criticality factor
            if is_critical:
                crit_factor = 1.0
            elif total_slack is not None:
                crit_factor = 1.0 / (1.0 + max(0, total_slack) / 60.0)
            else:
                crit_factor = 0.5

            # Progress factor (prefer completing started work)
            progress_pct = int(getattr(t, "progress_percentage", 0) or 0)
            if st == TaskProgressStatus.IN_PROGRESS:
                progress_factor = 1.0 + (progress_pct / 200.0)  # [1.0, 1.5]
            else:
                progress_factor = 0.8

            # Readiness modifier
            readiness_factor = 1.0 if is_ready else 0.3

            # Composite score bounded in [0.0, 1.0]
            raw_score = (
                0.40 * crit_factor
                + 0.30 * priority_factor
                + 0.15 * min(1.0, progress_factor)
                + 0.15 * readiness_factor
            )
            score = round(max(0.0, min(1.0, raw_score)), 4)

            # Determine action
            if not is_ready:
                action = RecommendationAction.RESOLVE_BLOCKER
            elif st == TaskProgressStatus.IN_PROGRESS:
                action = RecommendationAction.CONTINUE_EXECUTION
            elif is_critical:
                action = RecommendationAction.EXPEDITE_CRITICAL
            else:
                action = RecommendationAction.START_TASK

            # Construct explainable rationale
            explanation_parts: list[str] = []
            if is_ready:
                preds = predecessors_of.get(t_temp, [])
                if preds:
                    explanation_parts.append(
                        f"All {len(preds)} predecessor(s) completed ({', '.join(sorted(preds))})."
                    )
                else:
                    explanation_parts.append("Initial task with zero incoming dependencies.")

                if is_critical:
                    explanation_parts.append(
                        "On critical path (0m slack) — immediate project bottleneck."
                    )
                elif total_slack is not None:
                    explanation_parts.append(f"Has {total_slack}m total slack available.")

                if st == TaskProgressStatus.IN_PROGRESS:
                    explanation_parts.append(f"Currently in progress ({progress_pct}% done).")

                explanation_parts.append(f"Assigned priority: {prio_str}.")
            else:
                reasons = task_block_reasons.get(t_temp, [])
                explanation_parts.append(
                    f"Blocked or pending prerequisite execution: {'; '.join(reasons)}"
                )

            explanation = " ".join(explanation_parts)

            quality = RecommendationQuality.HIGH if has_cpm else RecommendationQuality.MEDIUM
            quality_basis = (
                "deterministic_cpm_and_dependency_evaluation"
                if has_cpm
                else "dependency_and_heuristic_priority_evaluation"
            )

            t_uid = getattr(t, "id", None)
            if not isinstance(t_uid, UUID):
                try:
                    t_uid = UUID(str(t_uid))
                except Exception:
                    from uuid import uuid4
                    t_uid = uuid4()

            candidates.append(
                {
                    "task_id": t_uid,
                    "temporary_id": t_temp,
                    "title": str(getattr(t, "title", t_temp)),
                    "score": score,
                    "action": action,
                    "priority": prio_str,
                    "status": st,
                    "is_critical_path": is_critical,
                    "total_slack_minutes": total_slack,
                    "explanation": explanation,
                    "quality": quality,
                    "quality_basis": quality_basis,
                    "blocked_reasons": task_block_reasons.get(t_temp, []),
                    "is_ready": is_ready,
                }
            )

        # 5. Deterministic sorting:
        # 1. Ready tasks first (if include_blocked is true)
        # 2. Descending score
        # 3. Critical path first
        # 4. Deterministic lexical tie-breaking by temporary_id
        candidates.sort(
            key=lambda c: (
                1 if c["is_ready"] else 0,
                c["score"],
                1 if c["is_critical_path"] else 0,
                c["temporary_id"],
            ),
            reverse=True,
        )

        limited_candidates = candidates[: max(1, limit)]

        recommendations: list[TaskRecommendation] = []
        for idx, item in enumerate(limited_candidates, start=1):
            recommendations.append(
                TaskRecommendation(
                    task_id=item["task_id"],
                    temporary_id=item["temporary_id"],
                    title=item["title"],
                    rank=idx,
                    score=item["score"],
                    action=item["action"],
                    priority=item["priority"],
                    status=item["status"],
                    is_critical_path=item["is_critical_path"],
                    total_slack_minutes=item["total_slack_minutes"],
                    explanation=item["explanation"],
                    quality=item["quality"],
                    quality_basis=item["quality_basis"],
                    blocked_reasons=item["blocked_reasons"],
                )
            )

        return PlanRecommendationsResponse(
            plan_id=plan_id,
            tenant_id=tenant_id,
            evaluated_at=now,
            is_authoritative=False,
            total_tasks=total_tasks,
            ready_tasks_count=ready_count,
            blocked_tasks_count=blocked_count,
            completed_tasks_count=completed_count,
            recommendations=recommendations,
        )
