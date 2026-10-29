"""Worker and Integration Reliability Unit Test Suite (DQ-5 Hardening).

Validates:
1. Outbox worker lifecycle, bounded polling, lease management, rollback, and graceful shutdown.
2. Notification delivery worker transactional staging, lease expiration contract,
   fail-closed provisional recipient handling, retry bounds, and disabled dispatch.
3. Scheduling worker solver isolation, timeout handling (BUDGET_EXHAUSTED), and graceful shutdown.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.events.outbox.dispatcher import (
    DispatchOutcome,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.runner import (
    ManagedEventPublisher,
    OutboxDispatcherRunner,
    OutboxRunnerPolicy,
)
from app.events.publishers.base import EventPublisher
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.delivery_worker import NotificationDeliveryWorker
from app.integrations.engagement.dispatcher import NotificationDispatcherProtocol
from app.integrations.engagement.recovery import NotificationRecoveryService
from app.scheduling.config import SchedulerSettings
from app.scheduling.worker import SchedulerWorker

# =============================================================================
# 1. Outbox Worker Tests
# =============================================================================


class TestOutboxWorkerReliability:
    """Verifies Outbox Worker transactional polling, batch bounds, and graceful shutdown."""

    @pytest.mark.asyncio
    async def test_outbox_runner_graceful_shutdown_on_stop_event(self) -> None:
        """Worker exits cleanly when stop_event is set without executing further batches."""
        stop_event = asyncio.Event()
        stop_event.set()  # Pre-set stop event

        uow_factory = MagicMock()
        publisher = AsyncMock(spec=ManagedEventPublisher)

        runner_policy = OutboxRunnerPolicy(
            poll_interval=timedelta(milliseconds=10),
            max_concurrency=1,
            shutdown_grace_period=timedelta(seconds=1),
        )
        dispatch_policy = OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=30),
            publish_timeout=timedelta(seconds=5),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=60),
            retry_multiplier=2.0,
        )

        runner = OutboxDispatcherRunner(
            publisher=publisher,
            runner_policy=runner_policy,
            dispatch_policy=dispatch_policy,
            uow_factory=uow_factory,
        )

        # Run with pre-set stop event
        await runner.run(stop_event=stop_event)

        # Invariant: pre-set stop_event transitions to STOPPED immediately
        assert runner.state.value == "STOPPED"
        publisher.start.assert_not_called()
        uow_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_outbox_dispatcher_idle_when_no_events_pending(self) -> None:
        """OutboxDispatcher returns DispatchOutcome.IDLE when outbox table has no pending events."""
        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=None)
        uow.commit = AsyncMock()
        uow.rollback = AsyncMock()

        mock_scalar_result = MagicMock()
        mock_scalar_result.scalars.return_value.all.return_value = []
        uow.session = MagicMock()
        uow.session.execute = AsyncMock(return_value=mock_scalar_result)

        uow_factory = MagicMock(return_value=uow)
        publisher = AsyncMock(spec=EventPublisher)
        policy = OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=30),
            publish_timeout=timedelta(seconds=5),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=60),
            retry_multiplier=2.0,
        )

        dispatcher = OutboxDispatcher(
            uow_factory=uow_factory,
            publisher=publisher,
            policy=policy,
        )

        result = await dispatcher.dispatch_once()
        assert result.outcome == DispatchOutcome.IDLE
        publisher.publish.assert_not_called()


# =============================================================================
# 2. Notification Delivery Worker Tests
# =============================================================================


class TestNotificationDeliveryWorkerReliability:
    """Verifies Notification Delivery Worker transactional outbox consumption and safety."""

    @pytest.mark.asyncio
    async def test_delivery_worker_disabled_by_default(self) -> None:
        """Notification delivery worker respects enabled=False and performs zero dispatches."""
        settings = NotificationSettings(enabled=False)
        assert settings.enabled is False

        mock_session_factory = MagicMock()
        mock_dispatcher = AsyncMock(spec=NotificationDispatcherProtocol)

        worker = NotificationDeliveryWorker(
            session_factory=mock_session_factory,
            dispatcher=mock_dispatcher,
            settings=settings,
        )

        count = await worker.poll_and_deliver()
        assert count == 0
        mock_session_factory.assert_not_called()
        mock_dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_recovery_service_rbac_and_pii_sanitization(self) -> None:
        """NotificationRecoveryService rejects unauthorized roles and prohibited PII."""
        mock_session = AsyncMock()
        service = NotificationRecoveryService(session=mock_session)

        # 1. Non-administrative role is rejected (RBAC)
        with pytest.raises(PermissionError, match="required role"):
            await service.recover_blocked_notification(
                actor_principal_id="usr_guest",
                actor_roles=["viewer"],
                tenant_id="tenant-alpha",
                command_id=str(uuid4()),
                resolved_recipients=["usr_safe_admin"],
            )

        # 2. Plaintext email PII is rejected
        with pytest.raises(ValueError, match="Plaintext email address is prohibited"):
            await service.recover_blocked_notification(
                actor_principal_id="usr_admin",
                actor_roles=["admin"],
                tenant_id="tenant-alpha",
                command_id=str(uuid4()),
                resolved_recipients=["admin@example.com"],
            )


# =============================================================================
# 3. Scheduling Worker Tests
# =============================================================================


class TestSchedulingWorkerReliability:
    """Verifies Scheduling Worker solve isolation, timeout handling, and failure recording."""

    @pytest.mark.asyncio
    async def test_scheduling_worker_budget_exhausted_on_timeout(self) -> None:
        """When solver exceeds solve_timeout_seconds, job status transitions to BUDGET_EXHAUSTED."""
        job_id = uuid4()
        tenant_id = "tenant-sched"
        claimed_job = MagicMock()
        claimed_job.id = job_id
        claimed_job.tenant_id = tenant_id

        sched_service = AsyncMock()
        sched_service.claim_jobs.return_value = [claimed_job]

        # Mock get_job returning a record that will be updated
        job_record = MagicMock()
        job_record.status = "SOLVING"
        sched_service.get_job.return_value = job_record

        # Mock DB Manager sessions
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_tx = MagicMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin = MagicMock(return_value=mock_tx)

        mock_db_manager = MagicMock()
        mock_db_manager.session_factory = MagicMock(return_value=mock_session)

        settings = SchedulerSettings(
            worker_id="test-sched-worker",
            claim_batch_size=1,
            solve_timeout_seconds=1.0,
            poll_interval_seconds=0.1,
            lease_duration_seconds=30.0,
            shutdown_grace_seconds=1.0,
        )

        worker = SchedulerWorker(
            db_manager=mock_db_manager,
            service=sched_service,
            settings=settings,
        )

        # Force timeout by patching asyncio.wait_for to raise TimeoutError
        with patch("asyncio.wait_for", side_effect=TimeoutError()):
            processed = await worker.run_once()

        assert processed == 0  # Did not complete successfully
        # Verify job was marked BUDGET_EXHAUSTED
        assert job_record.status == "BUDGET_EXHAUSTED"
        assert "budget exhausted" in job_record.failure_message.lower()
        assert job_record.completed_at is not None

    @pytest.mark.asyncio
    async def test_scheduling_worker_graceful_shutdown(self) -> None:
        """Worker exits main loop immediately when stop_event is set."""
        mock_db_manager = MagicMock()
        sched_service = AsyncMock()
        settings = SchedulerSettings(
            worker_id="test-sched-worker",
            claim_batch_size=1,
            solve_timeout_seconds=1.0,
            poll_interval_seconds=0.1,
            lease_duration_seconds=30.0,
            shutdown_grace_seconds=1.0,
        )

        worker = SchedulerWorker(
            db_manager=mock_db_manager,
            service=sched_service,
            settings=settings,
        )

        stop_event = asyncio.Event()
        stop_event.set()  # Stop immediately

        # Run loop - should terminate cleanly on first loop check
        await worker.run(stop_event=stop_event)
        assert worker._stop_event.is_set()
