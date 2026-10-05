"""Deterministic risk and blocker detection contracts (FR-TI-07).

Evaluates declared blockers, dependency slippage, and critical-path delays
that can be proven from plan data. Unprovable checks return not-evaluable.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.task_intelligence.graph.models import CPMScheduleResult


class RiskSeverity(StrEnum):
    """Severity level of an operational risk."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskCategory(StrEnum):
    """Categorization of operational risk signals."""

    DECLARED_BLOCKER = "DECLARED_BLOCKER"
    DEPENDENCY_SLIPPAGE = "DEPENDENCY_SLIPPAGE"
    CRITICAL_PATH_SLIPPAGE = "CRITICAL_PATH_SLIPPAGE"
    RESOURCE_CAPACITY_UNSUPPORTED = "RESOURCE_CAPACITY_UNSUPPORTED"


class TaskRiskItem(BaseModel):
    """Structured operational risk flag."""

    task_id: str = Field(
        ...,
        description="Plan-local or canonical task identifier.",
    )
    category: RiskCategory = Field(
        ...,
        description="Risk category code.",
    )
    severity: RiskSeverity = Field(
        ...,
        description="Assessed risk severity.",
    )
    description: str = Field(
        ...,
        description="Deterministic explanation of the risk.",
    )
    is_evaluable: bool = Field(
        default=True,
        description="False if this risk type requires external authority not present in Phase 2A.",
    )
    detected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the risk was evaluated.",
    )


class DeterministicRiskDetector:
    """Deterministic detector for provable plan risks."""

    @staticmethod
    def detect_critical_path_slippage(
        cpm_result: CPMScheduleResult,
        delayed_task_ids: set[str],
    ) -> list[TaskRiskItem]:
        """Detect tasks whose delay impacts the critical path.

        Any delay on a zero-slack task directly delays the entire project.
        """
        risks: list[TaskRiskItem] = []
        for task_id in delayed_task_ids:
            node = cpm_result.schedule.get(task_id)
            if node and node.is_critical:
                risks.append(
                    TaskRiskItem(
                        task_id=task_id,
                        category=RiskCategory.CRITICAL_PATH_SLIPPAGE,
                        severity=RiskSeverity.CRITICAL,
                        description=(
                            f"Task '{task_id}' is on the critical path (zero slack). "
                            "Any slippage directly delays overall project completion."
                        ),
                        is_evaluable=True,
                    )
                )
            elif node and not node.is_critical:
                risks.append(
                    TaskRiskItem(
                        task_id=task_id,
                        category=RiskCategory.DEPENDENCY_SLIPPAGE,
                        severity=RiskSeverity.MEDIUM,
                        description=(
                            f"Task '{task_id}' has {node.total_slack}m total slack. "
                            "Slippage will consume available float."
                        ),
                        is_evaluable=True,
                    )
                )
        return risks

    @staticmethod
    def evaluate_resource_capacity() -> TaskRiskItem:
        """Explicitly flags that resource capacity shortage cannot be evaluated.

        Preserves the invariant: Do NOT pretend to detect resource capacity
        without authoritative external resource master data.
        """
        return TaskRiskItem(
            task_id="GLOBAL",
            category=RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED,
            severity=RiskSeverity.LOW,
            description=(
                "Resource capacity cannot be evaluated in Phase 2A without "
                "authoritative upstream resource/room registry data."
            ),
            is_evaluable=False,
        )
