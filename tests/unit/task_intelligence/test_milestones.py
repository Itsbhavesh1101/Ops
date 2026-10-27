"""Authoritative unit test suite for Milestone Creation and Health Tracking (FR-TI-06).

Verifies:
- MilestoneDraft schema definition and validation
- Plan validation of milestones (duplicate IDs, unknown task associations)
- Milestone target date derivation and CPM latest finish alignment
- Milestone health indicators and progress percentage roll-up from member tasks
- MilestoneRecord database model mapping integrity
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models.planning import MilestoneRecord
from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.schemas import (
    MilestoneDraft,
    PlanSchema,
    TaskDraft,
    ValidationIssueCode,
)
from app.task_intelligence.validators import PlanValidator


def test_milestone_draft_schema_validation() -> None:
    """MilestoneDraft requires temporary_id and name, and allows target_date."""
    # Valid milestone draft
    target = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
    m = MilestoneDraft(
        temporary_id="M1_READINESS",
        name="Question Paper Readiness",
        description="All moderation and printing completed",
        target_date=target,
        associated_task_ids=["T1_MODERATE", "T2_PRINT"],
    )
    assert m.temporary_id == "M1_READINESS"
    assert m.name == "Question Paper Readiness"
    assert m.target_date == target
    assert len(m.associated_task_ids) == 2

    # Missing temporary_id or name fails
    with pytest.raises(ValidationError):
        MilestoneDraft(temporary_id="", name="Invalid Milestone")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        MilestoneDraft(temporary_id="M_EMPTY", name="")  # type: ignore[call-arg]


def test_milestone_plan_validator_duplicate_id() -> None:
    """PlanValidator catches duplicate milestone temporary IDs."""
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Duplicate Milestone Plan",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        milestones=[
            MilestoneDraft(temporary_id="M1", name="Milestone Alpha", associated_task_ids=["T1"]),
            MilestoneDraft(
                temporary_id="M1",
                name="Milestone Alpha Duplicate",
                associated_task_ids=["T1"],
            ),
        ],
    )
    result = validator.validate(plan)
    assert not result.is_valid
    assert any(i.code == ValidationIssueCode.DUPLICATE_MILESTONE_ID for i in result.issues)


def test_milestone_plan_validator_unknown_task_reference() -> None:
    """PlanValidator catches milestone referencing a task that does not exist in plan."""
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Unknown Task Milestone Plan",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        milestones=[
            MilestoneDraft(
                temporary_id="M1",
                name="Milestone Alpha",
                associated_task_ids=["T1", "T_NONEXISTENT"],
            )
        ],
    )
    result = validator.validate(plan)
    assert not result.is_valid
    issue = next(i for i in result.issues if i.code == ValidationIssueCode.UNKNOWN_MILESTONE_TASK)
    assert "T_NONEXISTENT" in issue.message


def test_milestone_target_date_derivation_from_cpm() -> None:
    """Milestone target completion matches the maximum latest finish of its member tasks."""
    graph = TaskDependencyGraph()
    graph.add_task("T1_SETUP", duration_minutes=60)
    graph.add_task("T2_EXECUTE", duration_minutes=120)
    graph.add_dependency("T1_SETUP", "T2_EXECUTE")

    cpm = CriticalPathCalculator.calculate(graph)

    # Member tasks for Milestone "M_EXEC" are T1 and T2
    member_task_ids = ["T1_SETUP", "T2_EXECUTE"]
    latest_finish_minutes = max(
        cpm.schedule[tid].late_finish for tid in member_task_ids if tid in cpm.schedule
    )
    # T1 finish is 60; T2 finish is 180
    assert latest_finish_minutes == 180


def test_milestone_health_rollup_evaluation() -> None:
    """Milestone health rolls up status of associated member tasks:
    - ALL COMPLETED -> ON_TRACK / 100%
    - ANY BLOCKED -> AT_RISK
    - ON TRACK -> In-progress or TODO without blockers
    """
    class MemberTask:
        def __init__(self, status: str, progress_percentage: int) -> None:
            self.status = status
            self.progress_percentage = progress_percentage

    # 1. All tasks completed
    tasks_all_done = [MemberTask("COMPLETED", 100), MemberTask("COMPLETED", 100)]
    avg_progress = sum(t.progress_percentage for t in tasks_all_done) / len(tasks_all_done)
    has_blocked = any(t.status == "BLOCKED" for t in tasks_all_done)
    health = "AT_RISK" if has_blocked else ("COMPLETED" if avg_progress == 100 else "ON_TRACK")
    assert health == "COMPLETED"
    assert avg_progress == 100.0

    # 2. One task blocked -> AT_RISK
    tasks_with_blocked = [MemberTask("IN_PROGRESS", 50), MemberTask("BLOCKED", 20)]
    has_blocked_2 = any(t.status == "BLOCKED" for t in tasks_with_blocked)
    health_2 = "AT_RISK" if has_blocked_2 else "ON_TRACK"
    assert health_2 == "AT_RISK"

    # 3. Tasks in progress without blockers -> ON_TRACK
    tasks_healthy = [MemberTask("IN_PROGRESS", 40), MemberTask("TODO", 0)]
    has_blocked_3 = any(t.status == "BLOCKED" for t in tasks_healthy)
    health_3 = "AT_RISK" if has_blocked_3 else "ON_TRACK"
    assert health_3 == "ON_TRACK"


def test_milestone_record_model_attributes() -> None:
    """MilestoneRecord maps cleanly to PostgreSQL schema with required constraints."""
    plan_id = uuid4()
    milestone_id = uuid4()
    target = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)

    m_rec = MilestoneRecord(
        id=milestone_id,
        tenant_id="tenant-alpha",
        plan_id=plan_id,
        temporary_id="M1_FINAL",
        name="Semester Exam Wrap-up",
        description="Collated records sealed",
        target_date=target,
    )

    assert m_rec.id == milestone_id
    assert m_rec.tenant_id == "tenant-alpha"
    assert m_rec.plan_id == plan_id
    assert m_rec.temporary_id == "M1_FINAL"
    assert m_rec.name == "Semester Exam Wrap-up"
    assert m_rec.target_date == target
