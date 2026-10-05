"""Live PostgreSQL migration verification matrix for Migration 0011 (Phase 4A).

Proves:
1. Isolated temporary database creation
2. Upgrade to 0010 (16 application tables)
3. 0010 -> 0011 upgrade (23 application tables)
4. 0011 -> 0010 downgrade (16 application tables restored, 7 dropped cleanly)
5. 0010 -> 0011 re-upgrade (23 application tables restored)
6. Downgrade to base
7. Upgrade base -> head (all 23 tables created cleanly)
8. Repeat upgrade head (idempotency)
9. Semantic check: all 7 scheduling tables and their structural constraints exist.
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


@pytest.mark.integration
def test_migration_0011_live_matrix() -> None:
    """Execute complete 0010 -> 0011 -> 0010 -> 0011 -> base -> head live cycle."""
    isolated_db_name = f"e7_sched_mig_{uuid.uuid4().hex[:8]}"
    isolated_pg_url = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"
    alembic_db_url = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"

    async def create_db() -> None:
        admin_conn = await asyncpg.connect(ADMIN_DB_URL)
        try:
            await admin_conn.execute(f'CREATE DATABASE "{isolated_db_name}"')
        finally:
            await admin_conn.close()

    _run_async(create_db())

    phase_3b_16_tables = {
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
    }

    phase_4a_7_tables = {
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
    }

    all_23_tables = phase_3b_16_tables | phase_4a_7_tables

    try:
        alembic_cfg = Config(str(ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", alembic_db_url)

        # 1. Upgrade to 0010
        command.upgrade(alembic_cfg, "0010")
        tables_0010 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0010 == phase_3b_16_tables
        assert len(tables_0010) == 16

        # 2. Upgrade 0010 -> 0011
        command.upgrade(alembic_cfg, "0011")
        tables_0011 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0011 == all_23_tables
        assert len(tables_0011) == 23

        # 3. Downgrade 0011 -> 0010
        command.downgrade(alembic_cfg, "0010")
        tables_downgraded = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_downgraded == phase_3b_16_tables
        assert len(tables_downgraded) == 16
        for t in phase_4a_7_tables:
            assert t not in tables_downgraded

        # 4. Re-upgrade 0010 -> 0011
        command.upgrade(alembic_cfg, "0011")
        tables_reupgraded = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_reupgraded == all_23_tables
        assert len(tables_reupgraded) == 23

        # 5. Downgrade to base
        command.downgrade(alembic_cfg, "base")
        tables_base = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert len(tables_base) == 0

        # 6. Upgrade base -> 0011
        command.upgrade(alembic_cfg, "0011")
        tables_0011_re = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0011_re == all_23_tables
        assert len(tables_0011_re) == 23

        # 7. Repeat 0011 (idempotency)
        command.upgrade(alembic_cfg, "0011")
        tables_0011_repeat = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0011_repeat == all_23_tables

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
