"""Database-backed historical median effort estimation provider (FR-TI-04).

Implements closed-loop effort recalibration from real, persisted task
executions in PostgreSQL. Computes the historical median actual duration
for completed tasks partitioned strictly by tenant and work category.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from app.task_intelligence.estimators.base import HistoricalMedianProviderProtocol

if TYPE_CHECKING:
    from app.db.repositories.planning import TaskRepository


class DatabaseHistoricalMedianProvider(HistoricalMedianProviderProtocol):
    """Tenant-scoped historical median duration provider.

    Adheres strictly to FR-TI-04:
    - Derives from actual execution data (completed tasks with actual_duration_minutes).
    - Enforces tenant isolation (no cross-tenant leakage of duration benchmarks).
    - Returns None if historical records are absent (no fabricated numbers).
    - Computes true mathematical median across real sample distributions.
    """

    def __init__(
        self,
        tenant_id: str,
        category_medians: dict[str, float] | None = None,
        min_sample_size: int = 1,
    ) -> None:
        self.tenant_id = tenant_id
        self._medians: dict[str, float] = category_medians or {}
        self._min_sample_size = max(1, min_sample_size)

    def get_historical_median_duration(
        self,
        work_category: str,
        task_type: str | None = None,
    ) -> float | None:
        """Return historical median duration in minutes, or None if unavailable.

        Satisfies HistoricalMedianProviderProtocol.
        """
        normalized_cat = work_category.strip().upper() if work_category else ""
        return self._medians.get(normalized_cat)

    @classmethod
    async def load_for_tenant(
        cls,
        task_repo: TaskRepository,
        tenant_id: str,
        min_sample_size: int = 1,
    ) -> DatabaseHistoricalMedianProvider:
        """Asynchronously load completed task actual durations and compute medians."""
        # Query all completed actual durations for the tenant
        # We fetch durations for standard categories or dynamically group by category
        from sqlalchemy import select

        from app.db.models.planning import TaskRecord

        stmt = (
            select(
                TaskRecord.work_category,
                TaskRecord.actual_duration_minutes,
            )
            .where(
                TaskRecord.tenant_id == tenant_id,
                TaskRecord.status == "COMPLETED",
                TaskRecord.actual_duration_minutes.is_not(None),
                TaskRecord.actual_duration_minutes > 0,
            )
        )
        result = await task_repo.session.execute(stmt)
        rows = result.all()

        category_samples: dict[str, list[float]] = {}
        for cat, dur in rows:
            if not cat or dur is None:
                continue
            norm_cat = cat.strip().upper()
            category_samples.setdefault(norm_cat, []).append(float(dur))

        category_medians: dict[str, float] = {}
        for norm_cat, samples in category_samples.items():
            if len(samples) >= min_sample_size:
                category_medians[norm_cat] = float(statistics.median(samples))

        return cls(
            tenant_id=tenant_id,
            category_medians=category_medians,
            min_sample_size=min_sample_size,
        )

    def update_median(self, work_category: str, durations: list[float | int]) -> None:
        """Deterministically update or recalibrate median for a given category."""
        valid_samples = [float(d) for d in durations if d is not None and d > 0]
        norm_cat = work_category.strip().upper() if work_category else ""
        if len(valid_samples) >= self._min_sample_size:
            self._medians[norm_cat] = float(statistics.median(valid_samples))
        elif norm_cat in self._medians:
            del self._medians[norm_cat]
