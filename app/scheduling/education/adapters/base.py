"""Abstract Education Scheduler Adapter Contract.

Part of Phase 5 Education Scheduling PoC.
Provides a provider-neutral boundary for educational schedulers (UniTime, FET, etc.)
ensuring canonical E-7 schedule ownership and secondary ID isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.scheduling.education.dtos import (
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
)
from app.scheduling.schemas import ScheduleStatus, SchedulingResourceSnapshot


class EducationSchedulerAdapter(ABC):
    """Abstract contract for educational scheduling adapters."""

    @abstractmethod
    def solve(
        self,
        dataset: EducationBenchmarkDataset,
        resources: list[SchedulingResourceSnapshot],
        disruptions: list[dict[str, Any]] | None = None,
        is_repair: bool = False,
        timeout_seconds: float = 10.0,
    ) -> tuple[ScheduleStatus, list[CanonicalEducationAssignment], dict[str, Any]]:
        """Import canonical benchmark, invoke solver, map results back to canonical IDs."""
        pass
