"""Unit tests for PlanService domain operations and baseline versioning."""

from typing import Any
from uuid import uuid4

from app.task_intelligence.service import (
    compute_plan_baseline_hash,
)


def test_baseline_hash_deterministic() -> None:
    class DummyTask:
        def __init__(self, tid: str, duration: int, title: str):
            self.temporary_id = tid
            self.estimated_duration_minutes = duration
            self.title = title

    class DummyDep:
        def __init__(self, pred: Any, succ: Any, lag: int = 0):
            self.predecessor_id = pred
            self.successor_id = succ
            self.lag_minutes = lag

    t1_id = uuid4()
    t2_id = uuid4()

    tasks = [DummyTask("T1", 60, "Task A"), DummyTask("T2", 30, "Task B")]
    deps = [DummyDep(t1_id, t2_id, 0)]

    hash1 = compute_plan_baseline_hash(tasks, deps)
    hash2 = compute_plan_baseline_hash(tasks, deps)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 hex string
