"""Static and runtime regression tests enforcing zero test-fixture dependency in app/ runtime."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.schemas import EventCreate, create_event_envelope
from app.scheduling.handlers.task_completed import TaskCompletedSchedulingHandler


def test_app_runtime_has_no_fixture_path_literals() -> None:
    """Verify that production runtime code in app/ does not contain
    path references to tests/fixtures.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    app_dir = repo_root / "app"

    violations: list[str] = []
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if "tests/fixtures" in content or "tests\\fixtures" in content:
            violations.append(str(py_file.relative_to(repo_root)))

    assert not violations, f"Production code in app/ must not reference test fixtures: {violations}"


@pytest.mark.asyncio
async def test_task_completed_handler_defaults_to_no_synthetic_demand() -> None:
    """Verify TaskCompletedSchedulingHandler does not silently invoke benchmarks in runtime."""
    session = AsyncMock()
    exec_mock = MagicMock()
    exec_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = exec_mock
    scheduling_service = AsyncMock()

    # Default constructor has dataset_loader=None
    handler = TaskCompletedSchedulingHandler(
        scheduling_service=scheduling_service,
    )
    assert handler.dataset_loader is None

    envelope = create_event_envelope(
        EventCreate(
            event_type="task.completed",
            tenant_id="tenant-clean-audit",
            correlation_id="corr-audit",
            payload={
                "task_id": "T4_RESOURCE_READINESS",
                "plan_id": "plan-clean-001",
            },
        )
    )

    # Without explicit scheduling_request payload or injected test loader, returns None
    result = await handler.handle(session=session, envelope=envelope)
    assert result is None
    scheduling_service.submit_job.assert_not_called()
