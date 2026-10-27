"""Regression test for PERF-CORR-001 (Task update detached-instance defect).

Verifies that PlanService.update_task_progress() explicitly flushes and refreshes the
TaskRecord before committing the UnitOfWork session, preventing post-transaction
DetachedInstanceError when accessing model attributes outside the session context.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models.planning import TaskRecord
from app.db.uow import SqlAlchemyUnitOfWork
from app.task_intelligence.progress import TaskProgressStatus, TaskProgressUpdate
from app.task_intelligence.service import PlanService, TaskNotFoundError


@pytest.mark.asyncio
async def test_update_task_refreshes_before_commit_regression_perf_corr_001() -> None:
    """PERF-CORR-001: Ensure session.flush() and session.refresh() occur before commit()."""
    call_order: list[str] = []

    mock_session = AsyncMock()

    async def mock_flush() -> None:
        call_order.append("flush")

    async def mock_refresh(instance: Any) -> None:
        call_order.append("refresh")

    async def mock_commit() -> None:
        call_order.append("commit")

    mock_session.flush = AsyncMock(side_effect=mock_flush)
    mock_session.refresh = AsyncMock(side_effect=mock_refresh)
    mock_session.commit = AsyncMock(side_effect=mock_commit)
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    service = PlanService(uow_factory=uow_factory)

    tenant_id = "tenant-test-01"
    task_id = uuid4()
    plan_id = uuid4()

    mock_existing_task = MagicMock()
    mock_existing_task.id = task_id
    mock_existing_task.plan_id = plan_id
    mock_existing_task.temporary_id = "T1"
    mock_existing_task.status = "TODO"

    mock_updated_task = MagicMock()
    mock_updated_task.id = task_id
    mock_updated_task.status = "COMPLETED"
    mock_updated_task.progress_percentage = 100

    with (
        patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls,
        patch("app.task_intelligence.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.task_intelligence.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_existing_task)
        mock_task_repo.update_task_progress = AsyncMock(return_value=mock_updated_task)

        mock_audit_repo = mock_audit_repo_cls.return_value
        mock_audit_repo.append = AsyncMock()

        mock_outbox_repo = mock_outbox_repo_cls.return_value
        mock_outbox_repo.enqueue = AsyncMock()

        update_data = TaskProgressUpdate(
            status=TaskProgressStatus.COMPLETED,
            progress_percentage=100,
        )

        res = await service.update_task_progress(
            tenant_id=tenant_id,
            task_id=task_id,
            update_data=update_data,
            actor_id="test-operator",
        )

        assert res is mock_updated_task
        assert call_order == ["flush", "refresh", "commit"], (
            f"Expected flush and refresh before commit, got {call_order}"
        )
        mock_session.refresh.assert_awaited_once_with(mock_updated_task)
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_task_rollback_on_failure_preserves_semantics() -> None:
    """Verify that rollback semantics remain intact if an exception occurs prior to commit."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    service = PlanService(uow_factory=uow_factory)

    tenant_id = "tenant-test-01"
    task_id = uuid4()

    with patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls:
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=None)  # Triggers TaskNotFoundError

        update_data = TaskProgressUpdate(status=TaskProgressStatus.IN_PROGRESS)

        with pytest.raises(TaskNotFoundError):
            await service.update_task_progress(
                tenant_id=tenant_id,
                task_id=task_id,
                update_data=update_data,
            )

        mock_session.commit.assert_not_awaited()
        mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_task_progress_completion_evidence_and_audit() -> None:
    """Verify that completion evidence, actual effort, and variance are audited
    and emitted to outbox.
    """
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    service = PlanService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    tenant_id = "tenant-test-01"
    task_id = uuid4()
    plan_id = uuid4()

    mock_existing_task = MagicMock()
    mock_existing_task.id = task_id
    mock_existing_task.plan_id = plan_id
    mock_existing_task.temporary_id = "T_FINISH"
    mock_existing_task.status = "IN_PROGRESS"
    mock_existing_task.estimated_duration_minutes = 60

    mock_updated_task = MagicMock()
    mock_updated_task.id = task_id
    mock_updated_task.status = "COMPLETED"
    mock_updated_task.progress_percentage = 100
    mock_updated_task.actual_duration_minutes = 75
    mock_updated_task.variance_minutes = 15
    mock_updated_task.completion_evidence = "https://artifacts.org/audit/seal-verify.pdf"

    with (
        patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls,
        patch("app.task_intelligence.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.task_intelligence.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_existing_task)
        mock_task_repo.update_task_progress = AsyncMock(return_value=mock_updated_task)

        mock_audit_repo = mock_audit_repo_cls.return_value
        mock_audit_repo.append = AsyncMock()

        mock_outbox_repo = mock_outbox_repo_cls.return_value
        mock_outbox_repo.enqueue = AsyncMock()

        update_data = TaskProgressUpdate(
            status=TaskProgressStatus.COMPLETED,
            progress_percentage=100,
            actual_duration_minutes=75,
            completion_evidence="https://artifacts.org/audit/seal-verify.pdf",
        )

        res = await service.update_task_progress(
            tenant_id=tenant_id,
            task_id=task_id,
            update_data=update_data,
            actor_id="operator-123",
        )

        assert res.completion_evidence == "https://artifacts.org/audit/seal-verify.pdf"
        assert res.variance_minutes == 15

        # Check Audit event
        mock_audit_repo.append.assert_awaited_once()
        audit_call_arg = mock_audit_repo.append.call_args[0][0]
        assert audit_call_arg.details["completion_evidence"] == "https://artifacts.org/audit/seal-verify.pdf"
        assert audit_call_arg.details["actual_duration_minutes"] == 75
        assert audit_call_arg.details["variance_minutes"] == 15
        assert audit_call_arg.details["is_reopen"] is False

        # Check Outbox event
        mock_outbox_repo.enqueue.assert_awaited_once()
        outbox_envelope = mock_outbox_repo.enqueue.call_args[0][0]
        assert outbox_envelope.payload["completion_evidence"] == "https://artifacts.org/audit/seal-verify.pdf"
        assert outbox_envelope.payload["variance_minutes"] == 15
        assert outbox_envelope.payload["actual_duration_minutes"] == 75


@pytest.mark.asyncio
async def test_update_task_progress_reopening_sets_is_reopen_flag() -> None:
    """Transitioning from COMPLETED to IN_PROGRESS sets is_reopen=True in audit log."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    service = PlanService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    tenant_id = "tenant-test-01"
    task_id = uuid4()

    mock_existing_task = MagicMock()
    mock_existing_task.id = task_id
    mock_existing_task.status = "COMPLETED"

    mock_updated_task = MagicMock()
    mock_updated_task.id = task_id
    mock_updated_task.status = "IN_PROGRESS"
    mock_updated_task.progress_percentage = 50

    with (
        patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls,
        patch("app.task_intelligence.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.task_intelligence.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_existing_task)
        mock_task_repo.update_task_progress = AsyncMock(return_value=mock_updated_task)

        mock_audit_repo = mock_audit_repo_cls.return_value
        mock_audit_repo.append = AsyncMock()

        mock_outbox_repo = mock_outbox_repo_cls.return_value
        mock_outbox_repo.enqueue = AsyncMock()

        update_data = TaskProgressUpdate(
            status=TaskProgressStatus.IN_PROGRESS,
            progress_percentage=50,
        )

        await service.update_task_progress(
            tenant_id=tenant_id,
            task_id=task_id,
            update_data=update_data,
            actor_id="operator-123",
        )

        mock_audit_repo.append.assert_awaited_once()
        audit_call_arg = mock_audit_repo.append.call_args[0][0]
        assert audit_call_arg.details["is_reopen"] is True
        assert audit_call_arg.details["previous_status"] == "COMPLETED"
        assert audit_call_arg.details["new_status"] == "IN_PROGRESS"


def test_task_record_completion_evidence_property() -> None:
    """Verify TaskRecord.completion_evidence property reads/writes effort_estimate_json."""
    task = TaskRecord(
        temporary_id="T-100",
        title="Test Task",
        estimated_duration_minutes=60,
        actual_duration_minutes=80,
    )
    assert task.completion_evidence is None
    assert task.variance_minutes == 20

    task.completion_evidence = "https://internal/evidence.pdf"
    assert task.completion_evidence == "https://internal/evidence.pdf"
    assert task.effort_estimate_json == {"completion_evidence": "https://internal/evidence.pdf"}

    task.completion_evidence = None
    assert task.completion_evidence is None
    assert task.effort_estimate_json == {}


@pytest.mark.asyncio
async def test_update_task_audit_captures_true_previous_status_when_mutated_in_place() -> None:
    """Task 2: Verify audit event captures true previous status even when ORM entity
    is mutated in place within SQLAlchemy's identity map.
    """
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    service = PlanService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    tenant_id = "tenant-test-01"
    task_id = uuid4()

    # Single shared instance simulating SQLAlchemy identity map
    shared_task = MagicMock()
    shared_task.id = task_id
    shared_task.status = "TODO"
    shared_task.progress_percentage = 0
    shared_task.actual_duration_minutes = None
    shared_task.variance_minutes = None
    shared_task.completion_evidence = None
    shared_task.blocked_reason = None

    async def fake_update_repo(tid: Any, ten: Any, data: Any) -> Any:
        # Simulate in-place mutation of the ORM entity inside the repository
        shared_task.status = str(data.status)
        shared_task.progress_percentage = data.progress_percentage or 100
        return shared_task

    with (
        patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls,
        patch("app.task_intelligence.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.task_intelligence.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=shared_task)
        mock_task_repo.update_task_progress = AsyncMock(side_effect=fake_update_repo)

        mock_audit_repo = mock_audit_repo_cls.return_value
        mock_audit_repo.append = AsyncMock()
        mock_outbox_repo = mock_outbox_repo_cls.return_value
        mock_outbox_repo.enqueue = AsyncMock()

        update_data = TaskProgressUpdate(
            status=TaskProgressStatus.IN_PROGRESS,
            progress_percentage=25,
        )

        await service.update_task_progress(
            tenant_id=tenant_id,
            task_id=task_id,
            update_data=update_data,
            actor_id="operator-456",
        )

        mock_audit_repo.append.assert_awaited_once()
        audit_call_arg = mock_audit_repo.append.call_args[0][0]
        # Must capture original status "TODO", not the mutated "IN_PROGRESS"
        assert audit_call_arg.details["previous_status"] == "TODO"
        assert audit_call_arg.details["new_status"] == "IN_PROGRESS"


def test_completing_task_without_explicit_percentage_normalizes_to_100() -> None:
    """Task 3: Completing a task without an explicit percentage normalizes to 100%."""
    update = TaskProgressUpdate(status=TaskProgressStatus.COMPLETED)
    assert update.status == TaskProgressStatus.COMPLETED
    assert update.progress_percentage == 100


def test_completing_task_with_100_percent_progress() -> None:
    """Task 3: Completing a task with explicit 100% progress succeeds."""
    update = TaskProgressUpdate(status=TaskProgressStatus.COMPLETED, progress_percentage=100)
    assert update.status == TaskProgressStatus.COMPLETED
    assert update.progress_percentage == 100


def test_rejecting_incompatible_completion_percentage() -> None:
    """Task 3: Rejecting an incompatible completion percentage (< 100% with COMPLETED)."""
    with pytest.raises(ValueError, match="Completed tasks must have 100% progress_percentage."):
        TaskProgressUpdate(status=TaskProgressStatus.COMPLETED, progress_percentage=50)


@pytest.mark.asyncio
async def test_rejecting_reducing_progress_on_completed_task_without_reopening() -> None:
    """Task 3: Cannot reduce progress on a COMPLETED task without explicit reopening."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    service = PlanService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    tenant_id = "tenant-test-01"
    task_id = uuid4()

    mock_task = MagicMock()
    mock_task.id = task_id
    mock_task.status = "COMPLETED"
    mock_task.progress_percentage = 100

    with patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls:
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_task)

        # Attempting partial update to 50% without changing status raises ValueError
        partial_update = TaskProgressUpdate(progress_percentage=50)

        with pytest.raises(
            ValueError,
            match="Cannot set progress_percentage < 100 on a COMPLETED task without reopening.",
        ):
            await service.update_task_progress(
                tenant_id=tenant_id,
                task_id=task_id,
                update_data=partial_update,
            )


@pytest.mark.asyncio
async def test_reopening_completed_task_allows_partial_progress() -> None:
    """Task 3: Reopening a completed task to IN_PROGRESS allows partial progress."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.close = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)
    service = PlanService(uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory))

    tenant_id = "tenant-test-01"
    task_id = uuid4()

    mock_task = MagicMock()
    mock_task.id = task_id
    mock_task.status = "COMPLETED"
    mock_task.progress_percentage = 100

    mock_updated = MagicMock()
    mock_updated.id = task_id
    mock_updated.status = "IN_PROGRESS"
    mock_updated.progress_percentage = 40
    mock_updated.actual_duration_minutes = None
    mock_updated.variance_minutes = None
    mock_updated.completion_evidence = None
    mock_updated.blocked_reason = None

    with (
        patch("app.task_intelligence.service.TaskRepository") as mock_task_repo_cls,
        patch("app.task_intelligence.service.AuditRepository") as mock_audit_repo_cls,
        patch("app.task_intelligence.service.OutboxRepository") as mock_outbox_repo_cls,
    ):
        mock_task_repo = mock_task_repo_cls.return_value
        mock_task_repo.get_task_by_id = AsyncMock(return_value=mock_task)
        mock_task_repo.update_task_progress = AsyncMock(return_value=mock_updated)

        mock_audit_repo = mock_audit_repo_cls.return_value
        mock_audit_repo.append = AsyncMock()
        mock_outbox_repo = mock_outbox_repo_cls.return_value
        mock_outbox_repo.enqueue = AsyncMock()

        reopen_update = TaskProgressUpdate(
            status=TaskProgressStatus.IN_PROGRESS,
            progress_percentage=40,
        )

        res = await service.update_task_progress(
            tenant_id=tenant_id,
            task_id=task_id,
            update_data=reopen_update,
        )

        assert res.status == "IN_PROGRESS"
        assert res.progress_percentage == 40
        mock_audit_repo.append.assert_awaited_once()
        audit_call = mock_audit_repo.append.call_args[0][0]
        assert audit_call.details["is_reopen"] is True
        assert audit_call.details["previous_status"] == "COMPLETED"
        assert audit_call.details["new_status"] == "IN_PROGRESS"
        assert audit_call.details["progress_percentage"] == 40
        # Outbox event task.completed must NOT be emitted on reopening
        mock_outbox_repo.enqueue.assert_not_awaited()


def test_milestone_health_after_task_completion() -> None:
    """Task 3: Milestone health reflects COMPLETED when member task reaches COMPLETED."""
    from app.db.models.planning import MilestoneRecord
    from app.task_intelligence.milestones import MilestoneHealth, MilestoneHealthCalculator

    milestone = MilestoneRecord(
        id=uuid4(),
        tenant_id="tenant-test-01",
        plan_id=uuid4(),
        temporary_id="M1",
        name="Phase 1 Milestone",
    )
    completed_task = TaskRecord(
        temporary_id="T1",
        title="Completed task",
        status="COMPLETED",
        progress_percentage=100,
        estimated_duration_minutes=60,
    )

    response = MilestoneHealthCalculator.evaluate_milestone_health(milestone, [completed_task])
    assert response.health == MilestoneHealth.COMPLETED
    assert response.completed_tasks == 1
    assert response.total_tasks == 1



