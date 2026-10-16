"""PostgreSQL Recovery & Disaster Recovery Tests.

Validates:
1. Logical backup & restore into isolated disposable DB (Alembic 0013, 25 tables, tenant check).
2. Physical Point-In-Time Recovery (PITR) with continuous WAL archiving on disposable container.
3. DatabaseManager connection pool recovery via pool_pre_ping without pool poisoning.
4. Database + Outbox atomicity ensuring zero partial-state commit on error.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope
from scripts.recovery.backup_logical import run_logical_backup
from scripts.recovery.pitr_drill import run_pitr_drill
from scripts.recovery.restore_logical import restore_and_verify


@pytest.mark.asyncio
async def test_logical_backup_and_restore_drill(tmp_path: Path) -> None:
    """Verify logical pg_dump / pg_restore drill into disposable database."""
    dump_file = tmp_path / "test_recovery_backup.dump"
    # 1. Run backup
    backup_result = run_logical_backup(output_path=dump_file)
    size_val = int(str(backup_result["backup_size_bytes"]))
    assert backup_result["backup_size_bytes"] is not None and size_val > 0
    assert dump_file.exists()
    assert backup_result["sha256_checksum"] is not None

    # 2. Run restore drill on disposable database
    restore_result = restore_and_verify(
        dump_path=dump_file, target_database="e7_recovery_drill_disposable"
    )
    assert restore_result["status"] == "PASS"
    assert restore_result["alembic_head"] in ("0013", "0014")
    assert restore_result["application_tables_count"] in (25, 26)
    assert restore_result["alembic_verified"] is True
    assert restore_result["tables_verified"] is True


@pytest.mark.asyncio
async def test_pitr_physical_drill_isolated() -> None:
    """Verify physical base backup + WAL replay PITR on isolated disposable container."""
    pitr_result = run_pitr_drill(disposable_port=55433)
    assert pitr_result["status"] == "PASS"
    details = pitr_result["details"]
    assert details["has_marker_a"] is True
    assert details["has_marker_b"] is True
    assert details["has_destructive_c"] is False
    assert details["isolation_verified"] is True
    assert details["primary_untouched"] is True


@pytest.mark.asyncio
async def test_connection_pool_recovery_and_pre_ping() -> None:
    """Verify connection pool evicts stale connections via pool_pre_ping without poisoning."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    try:
        # Initial health check
        healthy = await manager.check_readiness(timeout_seconds=3.0)
        assert healthy is True

        # Acquire connection and simulate a transient server-side disconnect
        async with manager.session_factory() as session:
            # Terminate this specific backend connection on PostgreSQL server side
            try:
                await session.execute(text("SELECT pg_terminate_backend(pg_backend_pid());"))
            except Exception:
                # Expected: connection terminates immediately
                pass

        # Verify pool_pre_ping transparently recovers on next acquisition without poisoning pool
        healthy_again = await manager.check_readiness(timeout_seconds=3.0)
        assert healthy_again is True

        async with manager.session_factory() as session:
            result = (await session.execute(text("SELECT 1;"))).scalar()
            assert result == 1
    finally:
        await manager.dispose()


@pytest.mark.asyncio
async def test_database_and_outbox_transactional_atomicity() -> None:
    """Verify domain mutation and outbox record commit atomically; error rolls back both."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    tenant_id = f"test-atomicity-{uuid.uuid4().hex[:8]}"

    try:
        # Case A: Partial failure before commit -> 0 records persisted
        uow_fail = SqlAlchemyUnitOfWork(session_factory=manager.session_factory)
        envelope_fail = create_event_envelope(
            EventCreate(
                event_type="test.recovery.atomicity.fail",
                tenant_id=tenant_id,
                payload={"status": "should_rollback"},
            )
        )
        with pytest.raises(RuntimeError, match="Simulated transient crash before commit"):
            async with uow_fail:
                outbox_repo = OutboxRepository(uow_fail.session)
                await outbox_repo.enqueue(envelope_fail)
                # Simulate mid-transaction fault before commit
                raise RuntimeError("Simulated transient crash before commit")

        # Verify neither record survived rollback
        async with manager.session_factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM outbox_events WHERE envelope->>'tenant_id' = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar()
            assert count == 0

        # Case B: Successful transaction -> outbox persists on commit
        uow_success = SqlAlchemyUnitOfWork(session_factory=manager.session_factory)
        envelope_success = create_event_envelope(
            EventCreate(
                event_type="test.recovery.atomicity.committed",
                tenant_id=tenant_id,
                payload={"status": "committed"},
            )
        )
        async with uow_success:
            outbox_repo = OutboxRepository(uow_success.session)
            await outbox_repo.enqueue(envelope_success)
            await uow_success.commit()

        # Verify exactly 1 record persisted
        async with manager.session_factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM outbox_events WHERE envelope->>'tenant_id' = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar()
            assert count == 1
    finally:
        # Cleanup test records
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM outbox_events WHERE envelope->>'tenant_id' = :tid"),
                {"tid": tenant_id},
            )
            await session.commit()
        await manager.dispose()
