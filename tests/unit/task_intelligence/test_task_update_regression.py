"""Regression test for PERF-CORR-001 (Task update detached-instance defect).

Verifies that PlanService.update_task_progress() explicitly flushes and refreshes the
TaskRecord before committing the UnitOfWork session, preventing post-transaction
DetachedInstanceError when accessing model attributes outside the session context.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

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
