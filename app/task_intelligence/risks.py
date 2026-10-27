"""Deterministic risk and blocker detection contracts (FR-TI-07).

Evaluates declared blockers, dependency slippage, and critical-path delays
that can be proven from plan data. Unprovable checks return not-evaluable.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

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
    RESOURCE_CAPACITY_SHORTFALL = "RESOURCE_CAPACITY_SHORTFALL"


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
    def detect_blocked_dependencies(
        tasks: list[Any],
        dependencies: list[Any] | None = None,
    ) -> list[TaskRiskItem]:
        """Detect declared task blockers and downstream dependency impacts.

        Evaluates tasks with status == 'BLOCKED' or declared blocked_reason,
        and identifies successors waiting on blocked predecessors.
        """
        risks: list[TaskRiskItem] = []
        blocked_task_ids: set[str] = set()

        # 1. Direct declared blockers
        for t in tasks:
            tid = getattr(t, "temporary_id", None) or str(getattr(t, "id", ""))
            status = str(getattr(t, "status", ""))
            reason = getattr(t, "blocked_reason", None)
            if status == "BLOCKED" or reason:
                blocked_task_ids.add(tid)
                risks.append(
                    TaskRiskItem(
                        task_id=tid,
                        category=RiskCategory.DECLARED_BLOCKER,
                        severity=RiskSeverity.HIGH,
                        description=(
                            f"Task '{tid}' is declared blocked: {reason or 'Reason not specified'}."
                        ),
                        is_evaluable=True,
                    )
                )

        # 2. Blocked downstream dependencies
        if dependencies and blocked_task_ids:
            # Build id map if task objects have canonical UUID and temporary_id
            uuid_to_temp = {}
            for t in tasks:
                t_uid = getattr(t, "id", None)
                t_temp = getattr(t, "temporary_id", None)
                if t_uid and t_temp:
                    uuid_to_temp[t_uid] = t_temp
                    uuid_to_temp[str(t_uid)] = t_temp

            for d in dependencies:
                pred_raw = getattr(d, "predecessor_id", None)
                succ_raw = getattr(d, "successor_id", None)

                pred_id = uuid_to_temp.get(pred_raw, str(pred_raw))
                succ_id = uuid_to_temp.get(succ_raw, str(succ_raw))

                if pred_id in blocked_task_ids and succ_id not in blocked_task_ids:
                    risks.append(
                        TaskRiskItem(
                            task_id=succ_id,
                            category=RiskCategory.DEPENDENCY_SLIPPAGE,
                            severity=RiskSeverity.HIGH,
                            description=(
                                f"Task '{succ_id}' is waiting on predecessor '{pred_id}' "
                                "which is BLOCKED."
                            ),
                            is_evaluable=True,
                        )
                    )

        return risks

    @staticmethod
    def evaluate_resource_shortfalls(
        required_capacities: dict[str, int],
        available_capacities: dict[str, int],
    ) -> list[TaskRiskItem]:
        """Detect capacity shortages using authoritative resource data."""
        risks: list[TaskRiskItem] = []
        for resource_key, required in sorted(required_capacities.items()):
            available = available_capacities.get(resource_key, 0)
            if required > available:
                risks.append(
                    TaskRiskItem(
                        task_id=f"RESOURCE:{resource_key}",
                        category=RiskCategory.RESOURCE_CAPACITY_SHORTFALL,
                        severity=RiskSeverity.HIGH,
                        description=(
                            f"Authoritative capacity shortfall: resource '{resource_key}' "
                            f"requires {required} units but only {available} are available."
                        ),
                        is_evaluable=True,
                    )
                )
        return risks

    @staticmethod
    def evaluate_resource_capacity(
        resource_demands: dict[str, int] | None = None,
        resource_capacities: dict[str, int] | None = None,
    ) -> TaskRiskItem | list[TaskRiskItem]:
        """Evaluate resource capacity signals.

        If authoritative demand and capacity mappings are provided, deterministically
        evaluates capacity shortfalls. When authoritative data is missing, returns an
        explicit not-evaluable status. Never fabricates capacity signals.
        """
        if resource_demands is not None and resource_capacities is not None:
            return DeterministicRiskDetector.evaluate_resource_shortfalls(
                required_capacities=resource_demands,
                available_capacities=resource_capacities,
            )

        return TaskRiskItem(
            task_id="GLOBAL",
            category=RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED,
            severity=RiskSeverity.LOW,
            description=(
                "Resource capacity cannot be evaluated without authoritative "
                "upstream resource/room registry data."
            ),
            is_evaluable=False,
        )

