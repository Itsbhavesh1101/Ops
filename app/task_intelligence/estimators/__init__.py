"""app.task_intelligence.estimators package — Deterministic effort estimation.

Provides HeuristicEffortEstimator and interfaces for transparent,
rule-based and historical duration estimation.
"""

from app.task_intelligence.estimators.base import (
    EffortEstimateResult,
    HistoricalMedianProviderProtocol,
)
from app.task_intelligence.estimators.heuristic import HeuristicEffortEstimator
from app.task_intelligence.estimators.historical import DatabaseHistoricalMedianProvider

__all__ = [
    "DatabaseHistoricalMedianProvider",
    "EffortEstimateResult",
    "HeuristicEffortEstimator",
    "HistoricalMedianProviderProtocol",
]

