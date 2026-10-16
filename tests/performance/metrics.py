"""Benchmark metric calculation utilities for Phase 8C."""

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LatencyStats:
    total_requests: int
    error_count: int
    error_rate_pct: float
    duration_seconds: float
    throughput_rps: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    status_distribution: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "error_count": self.error_count,
            "error_rate_pct": round(self.error_rate_pct, 2),
            "duration_seconds": round(self.duration_seconds, 3),
            "throughput_rps": round(self.throughput_rps, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p90_ms": round(self.p90_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "status_distribution": self.status_distribution,
        }


def calculate_latency_stats(
    latencies_sec: list[float],
    duration_seconds: float,
    error_count: int = 0,
    status_distribution: dict[int, int] | None = None,
) -> LatencyStats:
    """Calculate p50, p90, p95, p99, max, throughput and error rate."""
    total = len(latencies_sec) + error_count
    if not latencies_sec:
        return LatencyStats(
            total_requests=total,
            error_count=error_count,
            error_rate_pct=100.0 if total > 0 else 0.0,
            duration_seconds=duration_seconds,
            throughput_rps=0.0,
            p50_ms=0.0,
            p90_ms=0.0,
            p95_ms=0.0,
            p99_ms=0.0,
            max_ms=0.0,
            status_distribution=status_distribution or {},
        )

    sorted_ms = sorted(x * 1000.0 for x in latencies_sec)
    n = len(sorted_ms)

    def percentile(p: float) -> float:
        idx = max(0, min(n - 1, math.ceil(p * n) - 1))
        return sorted_ms[idx]

    p50 = percentile(0.50)
    p90 = percentile(0.90)
    p95 = percentile(0.95)
    p99 = percentile(0.99)
    max_val = sorted_ms[-1]
    dur = max(0.001, duration_seconds)
    throughput = len(latencies_sec) / dur
    err_rate = (error_count / total * 100.0) if total > 0 else 0.0

    return LatencyStats(
        total_requests=total,
        error_count=error_count,
        error_rate_pct=err_rate,
        duration_seconds=dur,
        throughput_rps=throughput,
        p50_ms=p50,
        p90_ms=p90,
        p95_ms=p95,
        p99_ms=p99,
        max_ms=max_val,
        status_distribution=status_distribution or {},
    )


def calculate_repeatability_stats(values: list[float]) -> dict[str, Any]:
    """Calculate run_1, run_2, run_3, median, min, max, std_dev, cv_pct across repetitions."""
    if not values:
        return {}
    n = len(values)
    sorted_vals = sorted(values)
    median = (
        sorted_vals[n // 2]
        if n % 2 != 0
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    )
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0.0
    std_dev = math.sqrt(variance)
    cv_pct = (std_dev / mean * 100.0) if mean > 0 else 0.0

    res = {
        "runs": [round(v, 3) for v in values],
        "median": round(median, 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(mean, 3),
        "std_dev": round(std_dev, 3),
        "cv_pct": round(cv_pct, 2),
    }
    for i, v in enumerate(values, 1):
        res[f"run_{i}"] = round(v, 3)
    return res
