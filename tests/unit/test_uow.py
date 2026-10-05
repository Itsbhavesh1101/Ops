"""Unit tests for Phase 1 Task 1E Unit of Work and transaction management foundation.

Verifies:
- UnitOfWork protocol conformance and runtime checkability.
- SqlAlchemyUnitOfWork lifecycle states (NEW, ACTIVE, COMMITTED, ROLLED_BACK, CLOSED).
- Explicit commit requirement (clean exit without commit triggers rollback).
- Rollback on application exception and causal exception propagation.
- Guaranteed session close in all exit paths (success, rollback, exception, commit failure).
- One UoW = One fresh session; separate UoWs receive distinct sessions.
- Prohibition on re-entry / nested transactions and prohibition on closed UoW reuse.
- Illegal lifecycle state transition rejections (commit before enter, double commit, etc.).
- SqlAlchemyUnitOfWorkFactory creates fresh UoW instances per call.
- Zero network/PostgreSQL requirements (fully mockable without SQLite substitution).
"""

from unittest.mock import AsyncMock

import pytest

from app.db.uow import (
    SqlAlchemyUnitOfWork,
    SqlAlchemyUnitOfWorkFactory,
    UnitOfWork,
    UnitOfWorkClosedError,
    UnitOfWorkError,
    UnitOfWorkNotActiveError,
    UnitOfWorkState,
    UnitOfWorkStateError,
    create_unit_of_work_factory,
)


class MockSessionFactory:
    """Mock async_sessionmaker that returns fresh AsyncMock session doubles."""

    def __init__(self) -> None:
        self.created_sessions: list[AsyncMock] = []

    def __call__(self) -> AsyncMock:
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.close = AsyncMock()
        self.created_sessions.append(session)
        return session


def test_unit_of_work_protocol_conformance() -> None:
    """Verify SqlAlchemyUnitOfWork implements the UnitOfWork protocol."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]
    assert isinstance(uow, UnitOfWork)


def test_uow_requires_session_factory() -> None:
    """Verify constructing UoW or Factory with None raises ValueError."""
    with pytest.raises(ValueError, match="requires an async_sessionmaker"):
        SqlAlchemyUnitOfWork(None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="requires an async_sessionmaker"):
        SqlAlchemyUnitOfWorkFactory(None)  # type: ignore[arg-type]


def test_factory_creates_fresh_uow_instances() -> None:
    """Verify SqlAlchemyUnitOfWorkFactory creates a fresh, distinct UoW on each invocation."""
    session_factory = MockSessionFactory()
    factory = create_unit_of_work_factory(session_factory)  # type: ignore[arg-type]

    uow1 = factory.create()
    uow2 = factory()

    assert isinstance(uow1, SqlAlchemyUnitOfWork)
    assert isinstance(uow2, SqlAlchemyUnitOfWork)
    assert uow1 is not uow2
    assert uow1.state == UnitOfWorkState.NEW
    assert uow2.state == UnitOfWorkState.NEW


@pytest.mark.asyncio
async def test_uow_successful_explicit_commit() -> None:
    """Verify entering UoW, performing explicit commit, and exiting cleanly commits and closes."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    assert uow.state == UnitOfWorkState.NEW

    async with uow:
        assert uow.state == UnitOfWorkState.ACTIVE
        session = uow.session
        assert session is not None
        assert session.commit.call_count == 0
        assert session.rollback.call_count == 0
        assert session.close.call_count == 0

        await uow.commit()
        assert uow.state == UnitOfWorkState.COMMITTED
        assert session.commit.call_count == 1
        assert session.rollback.call_count == 0

    assert uow.state == UnitOfWorkState.CLOSED
    assert session.commit.call_count == 1
    assert session.rollback.call_count == 0
    assert session.close.call_count == 1


@pytest.mark.asyncio
async def test_uow_clean_exit_without_commit_triggers_rollback() -> None:
    """Verify entering UoW and exiting cleanly WITHOUT explicit commit triggers rollback."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        assert uow.state == UnitOfWorkState.ACTIVE
        session = uow.session
        # Do NOT call uow.commit()

    # Must be rolled back and closed; commit must NEVER have been called
    assert uow.state == UnitOfWorkState.CLOSED
    assert session.commit.call_count == 0, "Clean exit without explicit commit must NEVER commit"
    assert session.rollback.call_count == 1, "Clean exit without commit must roll back"
    assert session.close.call_count == 1


@pytest.mark.asyncio
async def test_uow_exception_triggers_rollback_and_propagates() -> None:
    """Verify exception inside context triggers rollback, closes session, and propagates."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class CustomDomainError(Exception):
        pass

    with pytest.raises(CustomDomainError, match="Domain violation occurred"):
        async with uow:
            session = uow.session
            raise CustomDomainError("Domain violation occurred")

    assert uow.state == UnitOfWorkState.CLOSED
    assert session.commit.call_count == 0
    assert session.rollback.call_count == 1
    assert session.close.call_count == 1


@pytest.mark.asyncio
async def test_uow_explicit_rollback() -> None:
    """Verify explicit rollback marks state as ROLLED_BACK and calls session.rollback."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        session = uow.session
        await uow.rollback()
        assert uow.state == UnitOfWorkState.ROLLED_BACK
        assert session.rollback.call_count == 1

    assert uow.state == UnitOfWorkState.CLOSED
    assert session.commit.call_count == 0
    assert session.rollback.call_count == 1
    assert session.close.call_count == 1


@pytest.mark.asyncio
async def test_uow_commit_failure_attempts_rollback_and_closes() -> None:
    """Verify if session.commit() raises, rollback is attempted and session is closed."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class DatabaseWriteError(RuntimeError):
        pass

    async with uow:
        session = uow.session
        session.commit.side_effect = DatabaseWriteError("Simulated write conflict")

        with pytest.raises(DatabaseWriteError, match="Simulated write conflict"):
            await uow.commit()

        assert uow.state == UnitOfWorkState.FAILED
        assert session.rollback.call_count == 1

    assert uow.state == UnitOfWorkState.CLOSED
    assert session.close.call_count == 1


@pytest.mark.asyncio
async def test_uow_separate_instances_have_distinct_sessions() -> None:
    """Verify separate Unit of Work instances create and own distinct sessions."""
    factory = MockSessionFactory()
    uow1 = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]
    uow2 = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow1:
        s1 = uow1.session
        await uow1.commit()

    async with uow2:
        s2 = uow2.session
        await uow2.commit()

    assert s1 is not s2
    assert len(factory.created_sessions) == 2


@pytest.mark.asyncio
async def test_uow_access_session_before_enter_rejected() -> None:
    """Verify accessing session before __aenter__ raises UnitOfWorkNotActiveError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    with pytest.raises(UnitOfWorkNotActiveError, match="has not been entered"):
        _ = uow.session


@pytest.mark.asyncio
async def test_uow_commit_before_enter_rejected() -> None:
    """Verify calling commit() before __aenter__ raises UnitOfWorkNotActiveError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    with pytest.raises(UnitOfWorkNotActiveError, match="has not been entered"):
        await uow.commit()


@pytest.mark.asyncio
async def test_uow_rollback_before_enter_rejected() -> None:
    """Verify calling rollback() before __aenter__ raises UnitOfWorkNotActiveError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    with pytest.raises(UnitOfWorkNotActiveError, match="has not been entered"):
        await uow.rollback()


@pytest.mark.asyncio
async def test_uow_nested_or_reenter_active_rejected() -> None:
    """Verify attempting to re-enter an already active UoW raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        with pytest.raises(UnitOfWorkStateError, match="Cannot re-enter an already active"):
            async with uow:
                pass


@pytest.mark.asyncio
async def test_uow_double_commit_rejected() -> None:
    """Verify calling commit() twice on the same UoW raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkStateError, match="already been committed"):
            await uow.commit()


@pytest.mark.asyncio
async def test_uow_commit_after_rollback_rejected() -> None:
    """Verify calling commit() after rollback() raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.rollback()
        with pytest.raises(UnitOfWorkStateError, match="already been rolled back"):
            await uow.commit()


@pytest.mark.asyncio
async def test_uow_rollback_after_commit_rejected() -> None:
    """Verify calling rollback() after commit() raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkStateError, match="already been committed"):
            await uow.rollback()


@pytest.mark.asyncio
async def test_uow_reuse_closed_instance_rejected() -> None:
    """Verify attempting to enter a closed UoW raises UnitOfWorkClosedError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.commit()

    assert uow.state == UnitOfWorkState.CLOSED

    with pytest.raises(UnitOfWorkClosedError, match="Cannot reuse a closed Unit of Work"):
        async with uow:
            pass


@pytest.mark.asyncio
async def test_uow_access_session_after_close_rejected() -> None:
    """Verify accessing session after UoW closure raises UnitOfWorkClosedError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.commit()

    with pytest.raises(UnitOfWorkClosedError, match="Unit of Work is closed"):
        _ = uow.session


@pytest.mark.asyncio
async def test_uow_session_access_after_commit_rejected() -> None:
    """Verify accessing uow.session after explicit commit raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        _ = uow.session  # Valid while ACTIVE
        await uow.commit()
        with pytest.raises(UnitOfWorkStateError, match="already been committed"):
            _ = uow.session


@pytest.mark.asyncio
async def test_uow_session_access_after_rollback_rejected() -> None:
    """Verify accessing uow.session after explicit rollback raises UnitOfWorkStateError."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        _ = uow.session  # Valid while ACTIVE
        await uow.rollback()
        with pytest.raises(UnitOfWorkStateError, match="already been rolled back"):
            _ = uow.session


@pytest.mark.asyncio
async def test_uow_cannot_obtain_another_transaction_after_finalization() -> None:
    """Verify neither commit nor rollback nor session access allows new work on finalized UoW."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        await uow.commit()

    with pytest.raises(UnitOfWorkClosedError):
        _ = uow.session
    with pytest.raises(UnitOfWorkClosedError):
        await uow.commit()
    with pytest.raises(UnitOfWorkClosedError):
        await uow.rollback()


@pytest.mark.asyncio
async def test_fresh_uow_from_factory_works_after_previous_finalized() -> None:
    """Verify obtaining a fresh UoW from factory operates normally after prior UoW finalized."""
    session_factory = MockSessionFactory()
    factory = create_unit_of_work_factory(session_factory)  # type: ignore[arg-type]

    async with factory() as uow1:
        s1 = uow1.session
        await uow1.commit()

    async with factory() as uow2:
        s2 = uow2.session
        await uow2.commit()

    assert s1 is not s2
    assert uow1.state == UnitOfWorkState.CLOSED
    assert uow2.state == UnitOfWorkState.CLOSED


@pytest.mark.asyncio
async def test_exit_finalization_call_counts() -> None:
    """Verify commit and rollback exit call counts adhere to exact finalization invariants."""
    factory = MockSessionFactory()

    # Commit + exit: commit=1, rollback=0, close=1
    uow_commit = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]
    async with uow_commit:
        s_commit = uow_commit.session
        await uow_commit.commit()
    assert s_commit.commit.call_count == 1
    assert s_commit.rollback.call_count == 0
    assert s_commit.close.call_count == 1

    # Rollback + exit: commit=0, rollback=1, close=1 (no second rollback)
    uow_rb = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]
    async with uow_rb:
        s_rb = uow_rb.session
        await uow_rb.rollback()
    assert s_rb.commit.call_count == 0
    assert s_rb.rollback.call_count == 1
    assert s_rb.close.call_count == 1


@pytest.mark.asyncio
async def test_application_exception_remains_primary_when_close_fails() -> None:
    """Verify application exception propagates as primary even when session.close raises."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class AppError(Exception):
        pass

    class CleanupCloseError(RuntimeError):
        pass

    with pytest.raises(AppError, match="primary business error") as exc_info:
        async with uow:
            s = uow.session
            s.close.side_effect = CleanupCloseError("sensitive_raw_sql_error_detail")
            raise AppError("primary business error")

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    # Verify no manual cyclic or mutated __context__
    assert exc_info.value.__context__ is not exc_info.value
    # Verify note is present and sanitized: contains only exception type name, no raw detail
    notes = getattr(exc_info.value, "__notes__", [])
    expected_note = "Secondary UnitOfWork session close cleanup failed: CleanupCloseError"
    assert any(expected_note in n for n in notes)
    assert not any("sensitive_raw_sql_error_detail" in n for n in notes)


@pytest.mark.asyncio
async def test_application_exception_remains_primary_when_rollback_fails() -> None:
    """Verify application exception propagates as primary even when session.rollback raises."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class AppError(Exception):
        pass

    class CleanupRollbackError(RuntimeError):
        pass

    with pytest.raises(AppError, match="primary business error") as exc_info:
        async with uow:
            s = uow.session
            s.rollback.side_effect = CleanupRollbackError("sensitive_rollback_db_leak")
            raise AppError("primary business error")

    assert s.rollback.call_count == 1
    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    notes = getattr(exc_info.value, "__notes__", [])
    expected_note = "Secondary UnitOfWork session rollback cleanup failed: CleanupRollbackError"
    assert any(expected_note in n for n in notes)
    assert not any("sensitive_rollback_db_leak" in n for n in notes)


@pytest.mark.asyncio
async def test_commit_failure_remains_primary_when_rollback_fails() -> None:
    """Verify commit failure propagates as primary even when secondary rollback raises."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class CommitError(RuntimeError):
        pass

    class RollbackCleanupError(RuntimeError):
        pass

    with pytest.raises(CommitError, match="primary commit failure") as exc_info:
        async with uow:
            s = uow.session
            s.commit.side_effect = CommitError("primary commit failure")
            s.rollback.side_effect = RollbackCleanupError("secondary rollback failure")
            await uow.commit()

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    notes = getattr(exc_info.value, "__notes__", [])
    expected_note = "Secondary UnitOfWork session rollback cleanup failed: RollbackCleanupError"
    assert any(expected_note in n for n in notes)


@pytest.mark.asyncio
async def test_commit_failure_remains_primary_when_close_fails() -> None:
    """Verify commit failure propagates as primary even when secondary session.close raises."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class CommitError(RuntimeError):
        pass

    class CloseCleanupError(RuntimeError):
        pass

    with pytest.raises(CommitError, match="primary commit failure") as exc_info:
        async with uow:
            s = uow.session
            s.commit.side_effect = CommitError("primary commit failure")
            s.close.side_effect = CloseCleanupError("secondary close failure")
            await uow.commit()

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    notes = getattr(exc_info.value, "__notes__", [])
    expected_note = "Secondary UnitOfWork session close cleanup failed: CloseCleanupError"
    assert any(expected_note in n for n in notes)


@pytest.mark.asyncio
async def test_commit_failure_with_both_rollback_and_close_failure_preserves_commit_error() -> None:
    """Verify commit failure propagates as primary when both rollback and close cleanup fail."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class CommitError(RuntimeError):
        pass

    class RollbackCleanupError(RuntimeError):
        pass

    class CloseCleanupError(RuntimeError):
        pass

    with pytest.raises(CommitError, match="primary commit failure") as exc_info:
        async with uow:
            s = uow.session
            s.commit.side_effect = CommitError("primary commit failure")
            s.rollback.side_effect = RollbackCleanupError("rollback failed")
            s.close.side_effect = CloseCleanupError("close failed")
            await uow.commit()

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    notes = getattr(exc_info.value, "__notes__", [])
    rb_note = "Secondary UnitOfWork session rollback cleanup failed: RollbackCleanupError"
    close_note = "Secondary UnitOfWork session close cleanup failed: CloseCleanupError"
    assert any(rb_note in n for n in notes)
    assert any(close_note in n for n in notes)


@pytest.mark.asyncio
async def test_no_primary_exception_close_failure_propagates() -> None:
    """Verify session.close failure propagates when there is no primary application exception."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class CloseCleanupError(RuntimeError):
        pass

    with pytest.raises(CloseCleanupError, match="close failed on clean exit"):
        async with uow:
            s = uow.session
            s.close.side_effect = CloseCleanupError("close failed on clean exit")
            await uow.commit()

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED


@pytest.mark.asyncio
async def test_clean_uncommitted_exit_rollback_failure_propagates_and_closes_session() -> None:
    """Verify uncommitted clean exit with failing rollback propagates and closes session."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class RollbackCleanupError(RuntimeError):
        pass

    with pytest.raises(RollbackCleanupError, match="uncommitted rollback failed"):
        async with uow:
            s = uow.session
            s.rollback.side_effect = RollbackCleanupError("uncommitted rollback failed")

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED


@pytest.mark.asyncio
async def test_explicit_rollback_failure_closes_session_and_leaves_uow_unusable() -> None:
    """Verify explicit rollback failure sets FAILED then CLOSED, closes session exactly once."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class RollbackError(RuntimeError):
        pass

    with pytest.raises(RollbackError, match="explicit rollback failed"):
        async with uow:
            s = uow.session
            s.rollback.side_effect = RollbackError("explicit rollback failed")
            try:
                await uow.rollback()
            except RollbackError:
                # Inside context block: rollback failed, state is FAILED, not ROLLED_BACK or CLOSED
                assert uow.state == UnitOfWorkState.FAILED
                with pytest.raises(UnitOfWorkStateError, match="transaction has failed"):
                    _ = uow.session
                with pytest.raises(UnitOfWorkStateError, match="transaction has failed"):
                    await uow.commit()
                with pytest.raises(UnitOfWorkStateError, match="transaction has failed"):
                    await uow.rollback()
                raise

    # After context exit: session.close was called exactly once, state transitioned to CLOSED
    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    with pytest.raises(UnitOfWorkClosedError):
        _ = uow.session


@pytest.mark.asyncio
async def test_explicit_rollback_failure_with_close_failure_preserves_rollback_error() -> None:
    """Verify explicit rollback failure remains primary even when session.close raises."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class RollbackError(RuntimeError):
        pass

    class CloseCleanupError(RuntimeError):
        pass

    with pytest.raises(RollbackError, match="explicit rollback failed") as exc_info:
        async with uow:
            s = uow.session
            s.rollback.side_effect = RollbackError("explicit rollback failed")
            s.close.side_effect = CloseCleanupError("close also failed")
            await uow.rollback()

    assert s.close.call_count == 1
    assert uow.state == UnitOfWorkState.CLOSED
    notes = getattr(exc_info.value, "__notes__", [])
    expected_note = "Secondary UnitOfWork session close cleanup failed: CloseCleanupError"
    assert any(expected_note in n for n in notes)
    # Verify no raw error text in notes
    assert not any("close also failed" in n for n in notes)


@pytest.mark.asyncio
async def test_sanitized_cleanup_notes_contain_no_raw_db_error_strings() -> None:
    """Verify that sensitive database details in cleanup exceptions are never leaked into notes."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class SensitiveDatabaseError(RuntimeError):
        pass

    sensitive_raw_message = (
        "FATAL: password authentication failed for user 'postgres_admin' "
        "at postgresql://postgres_admin:SecretPassword123@db.prod.internal:5432/core_db "
        "SELECT * FROM credential_keys WHERE secret='top_secret_token'"
    )

    with pytest.raises(RuntimeError, match="business operation failed") as exc_info:
        async with uow:
            s = uow.session
            s.close.side_effect = SensitiveDatabaseError(sensitive_raw_message)
            raise RuntimeError("business operation failed")

    notes = getattr(exc_info.value, "__notes__", [])
    assert len(notes) == 1
    assert "Secondary UnitOfWork session close cleanup failed: SensitiveDatabaseError" == notes[0]
    # Reconfirm absolutely zero sensitive raw strings leaked
    assert "postgres_admin" not in notes[0]
    assert "SecretPassword123" not in notes[0]
    assert "top_secret_token" not in notes[0]
    assert "SELECT" not in notes[0]


@pytest.mark.asyncio
async def test_no_manual_cyclic_exception_chaining() -> None:
    """Verify that __context__ and __cause__ are never manually mutated or cyclic."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    class PrimaryError(Exception):
        pass

    class CloseError(RuntimeError):
        pass

    with pytest.raises(PrimaryError) as exc_info:
        async with uow:
            s = uow.session
            s.close.side_effect = CloseError("cleanup failure")
            raise PrimaryError("primary error")

    err = exc_info.value
    # Verify __cause__ and __context__ were not manually assigned
    assert err.__cause__ is None
    assert err.__context__ is not err  # Not cyclic


@pytest.mark.asyncio
async def test_uow_exception_hierarchy() -> None:
    """Verify UnitOfWork exception inheritance."""
    assert issubclass(UnitOfWorkStateError, UnitOfWorkError)
    assert issubclass(UnitOfWorkNotActiveError, UnitOfWorkStateError)
    assert issubclass(UnitOfWorkClosedError, UnitOfWorkStateError)
