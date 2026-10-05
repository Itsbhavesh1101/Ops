"""Live PostgreSQL migration verification matrix for Migration 0012 (Phase 4B).

Proves:
1. Isolated temporary database creation
2. Upgrade to 0011 (23 application tables)
3. 0011 -> 0012 upgrade (24 application tables, calendar_links created, schedule columns added)
4. 0012 -> 0011 downgrade (23 application tables restored, calendar_links dropped cleanly)
5. 0011 -> 0012 re-upgrade (24 application tables restored)
6. Downgrade to base
7. Upgrade base -> head (all 24 tables created cleanly)
8. Repeat upgrade head (idempotency)
9. Semantic check: calendar_links table and schedules publication/repair columns exist.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
ADMIN_DB_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/postgres"
)


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


async def _async_get_tables(db_url: str) -> set[str]:
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        return {row["tablename"] for row in rows}
    finally:
        await conn.close()


async def _async_get_columns(db_url: str, table_name: str) -> set[str]:
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table_name,
        )
        return {row["column_name"] for row in rows}
    finally:
        await conn.close()


@pytest.mark.integration
def test_migration_0012_live_matrix() -> None:
    """Execute complete 0011 -> 0012 -> 0011 -> 0012 -> base -> head live cycle."""
    isolated_db_name = f"e7_sched_mig12_{uuid.uuid4().hex[:8]}"
    isolated_pg_url = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"
    alembic_db_url = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"

    async def create_db() -> None:
        admin_conn = await asyncpg.connect(ADMIN_DB_URL)
        try:
            await admin_conn.execute(f'CREATE DATABASE "{isolated_db_name}"')
        finally:
            await admin_conn.close()

    _run_async(create_db())

    phase_4a_23_tables = {
        # Phase 1 (5)
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
        # Phase 2A (6)
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        # Phase 3A (3)
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        # Phase 3B (2)
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
    }

    all_24_tables = phase_4a_23_tables | {"calendar_links"}

    try:
        alembic_cfg = Config(str(ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", alembic_db_url)

        # 1. Upgrade to 0011
        command.upgrade(alembic_cfg, "0011")
        tables_0011 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0011 == phase_4a_23_tables
        assert len(tables_0011) == 23

        # 2. Upgrade 0011 -> 0012
        command.upgrade(alembic_cfg, "0012")
        tables_0012 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0012 == all_24_tables
        assert len(tables_0012) == 24
        assert "calendar_links" in tables_0012

        # Verify new columns on schedules
        schedules_cols = _run_async(_async_get_columns(isolated_pg_url, "schedules"))
        assert "published_at" in schedules_cols
        assert "published_by" in schedules_cols
        assert "repair_reason" in schedules_cols
        assert "repair_scope" in schedules_cols

        # 3. Downgrade 0012 -> 0011
        command.downgrade(alembic_cfg, "0011")
        tables_downgraded = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_downgraded == phase_4a_23_tables
        assert len(tables_downgraded) == 23
        assert "calendar_links" not in tables_downgraded

        schedules_cols_down = _run_async(_async_get_columns(isolated_pg_url, "schedules"))
        assert "published_at" not in schedules_cols_down
        assert "published_by" not in schedules_cols_down
        assert "repair_reason" not in schedules_cols_down
        assert "repair_scope" not in schedules_cols_down

        # 4. Re-upgrade 0011 -> 0012
        command.upgrade(alembic_cfg, "0012")
        tables_reupgraded = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_reupgraded == all_24_tables
        assert len(tables_reupgraded) == 24

        # 5. Downgrade to base
        command.downgrade(alembic_cfg, "base")
        tables_base = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert len(tables_base) == 0

        # 6. Upgrade base -> head (which is 0012)
        command.upgrade(alembic_cfg, "head")
        tables_head = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_head == all_24_tables
        assert len(tables_head) == 24

        # 7. Repeat head (idempotency)
        command.upgrade(alembic_cfg, "head")
        tables_head_repeat = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_head_repeat == all_24_tables
        assert len(tables_head_repeat) == 24

        # 8. Structural checks on calendar_links
        cal_cols = _run_async(_async_get_columns(isolated_pg_url, "calendar_links"))
        expected_cal_cols = {
            "id",
            "tenant_id",
            "schedule_id",
            "assignment_id",
            "activity_id",
            "schedule_key",
            "link_kind",
            "provider",
            "external_calendar_id",
            "external_event_id",
            "sync_status",
            "sync_token",
            "etag",
            "provider_version",
            "provider_updated_at",
            "last_synced_at",
            "error_message",
            "created_at",
            "updated_at",
        }
        assert expected_cal_cols.issubset(cal_cols)

        # 9. Verify partial indexes include external_calendar_id
        async def _async_get_indexes(db_url: str, table_name: str) -> dict[str, str]:
            conn = await asyncpg.connect(db_url)
            try:
                rows = await conn.fetch(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = $1",
                    table_name,
                )
                return {row["indexname"]: row["indexdef"] for row in rows}
            finally:
                await conn.close()

        cal_indexes = _run_async(_async_get_indexes(isolated_pg_url, "calendar_links"))
        assert "uq_calendar_links_cursor" in cal_indexes
        assert "external_calendar_id" in cal_indexes["uq_calendar_links_cursor"]
        assert "uq_calendar_links_scope_ext_event" in cal_indexes
        assert "external_calendar_id" in cal_indexes["uq_calendar_links_scope_ext_event"]

    finally:
        async def drop_db() -> None:
            admin_conn = await asyncpg.connect(ADMIN_DB_URL)
            try:
                await admin_conn.execute(
                    f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{isolated_db_name}' AND pid <> pg_backend_pid()
                    """
                )
                await admin_conn.execute(f'DROP DATABASE IF EXISTS "{isolated_db_name}"')
            finally:
                await admin_conn.close()

        _run_async(drop_db())
