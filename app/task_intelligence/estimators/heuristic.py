"""Configurable heuristic effort estimation engine."""

from app.task_intelligence.estimators.base import (
    EffortEstimateResult,
    HistoricalMedianProviderProtocol,
)


class HeuristicEffortEstimator:
    """Deterministic, transparent effort estimator.

    Precedence:
    1. Human explicit override (confidence 1.0)
    2. Historical median provider if available and matches category (confidence 0.85)
    3. Category heuristic default (confidence 0.7)
    4. Fallback default duration (confidence 0.5)
    """

    def __init__(
        self,
        category_durations: dict[str, float] | None = None,
        default_duration_minutes: float = 60.0,
        historical_provider: HistoricalMedianProviderProtocol | None = None,
    ) -> None:
        self._category_durations = category_durations or {
            "PLANNING": 120.0,
            "QUESTION_PAPER_READINESS": 180.0,
            "APPROVALS": 60.0,
            "ROOM_PREPARATION": 90.0,
            "INVIGILATOR_PREPARATION": 60.0,
            "SCHEDULING": 120.0,
            "EXAM_EXECUTION": 180.0,
            "INCIDENT_HANDLING": 60.0,
            "CLOSURE": 60.0,
        }
        self._default_duration_minutes = default_duration_minutes
        self._historical_provider = historical_provider

    def estimate(
        self,
        title: str,
        work_category: str | None = None,
        human_override: float | None = None,
        task_type: str | None = None,
    ) -> EffortEstimateResult:
        """Calculate effort estimate with full provenance.

        Args:
            title: Task headline (for logging/audit, no heuristic NLP).
            work_category: Normalized work category.
            human_override: Optional explicit human duration override.
            task_type: Optional specific task type.

        Returns:
            EffortEstimateResult with value, unit, basis, and confidence.
        """
        # 1. Human override
        if human_override is not None:
            if human_override < 0:
                raise ValueError("Human override duration cannot be negative.")
            return EffortEstimateResult(
                value=float(human_override),
                unit="minutes",
                basis="human_override",
                confidence=1.0,
                is_manual_override=True,
            )

        # 2. Historical median
        if self._historical_provider and work_category:
            median = self._historical_provider.get_historical_median_duration(
                work_category=work_category,
                task_type=task_type,
            )
            if median is not None and median >= 0:
                return EffortEstimateResult(
                    value=float(median),
                    unit="minutes",
                    basis="historical_median",
                    confidence=0.85,
                    is_manual_override=False,
                )

        # 3. Category heuristic
        if work_category and work_category in self._category_durations:
            return EffortEstimateResult(
                value=self._category_durations[work_category],
                unit="minutes",
                basis=f"category_heuristic:{work_category}",
                confidence=0.7,
                is_manual_override=False,
            )

        # 4. Default fallback
        return EffortEstimateResult(
            value=self._default_duration_minutes,
            unit="minutes",
            basis="default_fallback",
            confidence=0.5,
            is_manual_override=False,
        )
