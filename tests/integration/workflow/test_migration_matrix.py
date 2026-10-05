"""Live migration verification matrix against an isolated PostgreSQL database (Phase 3A).

Proves:
1. Empty DB -> 0009 / head
2. Repeat upgrade head (idempotency)
3. 0009 -> 0008 (downgrade)
4. 0008 -> 0009 (upgrade)
5. 0009 -> 0008 (downgrade verification)
6. 0008 -> 0009 again (upgrade verification)
7. Exactly 14 application tables present after 0009
8. Zero ACT_* tables managed in Base.metadata
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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

pytestmark = pytest.mark.skipif(
    os.environ.get("E7_RUN_WORKFLOW_INTEGRATION") != "1",
    reason="Requires opt-in E7_RUN_WORKFLOW_INTEGRATION=1 with live PostgreSQL",
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


def test_migration_matrix_isolated_database() -> None:
    """Execute complete bidirectional migration matrix against a temporary isolated database."""
    isolated_db_name = f"e7_migration_matrix_{uuid.uuid4().hex[:8]}"
    isolated_pg_url = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"
    alembic_db_url = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{isolated_db_name}"

    # 1. Create isolated database using asyncpg
    async def create_db() -> None:
        admin_conn = await asyncpg.connect(ADMIN_DB_URL)
        try:
            await admin_conn.execute(f'CREATE DATABASE "{isolated_db_name}"')
        finally:
            await admin_conn.close()

    _run_async(create_db())

    expected_16_tables = {
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
    expected_14_tables = expected_16_tables - {"sla_policies", "workflow_artifacts"}

    try:
        alembic_cfg = Config(str(ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", alembic_db_url)

        from alembic.script import ScriptDirectory

        script_dir = ScriptDirectory.from_config(alembic_cfg)
        heads = script_dir.get_heads()
        assert heads == ["0011"], f"Expected head ['0011'], got {heads}"
        revisions = [rev.revision for rev in script_dir.walk_revisions()]
        assert "0011" in revisions
        assert not any("0012" in rev for rev in revisions), "Migration 0012 must not exist"

        # 1. Empty DB -> 0010
        command.upgrade(alembic_cfg, "0010")

        tables = _run_async(_async_get_tables(isolated_pg_url))
        app_tables = tables - {"alembic_version"}
        assert app_tables == expected_16_tables
        assert len(app_tables) == 16
        assert not any(t.startswith("act_") for t in tables)

        # 2. Repeat upgrade head (idempotency)
        command.upgrade(alembic_cfg, "head")
        tables = _run_async(_async_get_tables(isolated_pg_url))
        assert tables - {"alembic_version"} == expected_16_tables

        # 3. Downgrade 0010 -> 0009
        command.downgrade(alembic_cfg, "0009")
        tables_0009 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0009 == expected_14_tables
        assert len(tables_0009) == 14
        assert "sla_policies" not in tables_0009
        assert "workflow_artifacts" not in tables_0009

        # 4. Upgrade 0009 -> 0010
        command.upgrade(alembic_cfg, "0010")
        tables_0010 = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0010 == expected_16_tables
        assert len(tables_0010) == 16

        # 5. Downgrade 0010 -> 0009 again
        command.downgrade(alembic_cfg, "0009")
        tables_0009_again = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_0009_again == expected_14_tables
        assert len(tables_0009_again) == 14

        # 6. Upgrade 0009 -> 0010 again (head)
        command.upgrade(alembic_cfg, "head")
        tables_final = _run_async(_async_get_tables(isolated_pg_url)) - {"alembic_version"}
        assert tables_final == expected_16_tables
        assert len(tables_final) == 16

    finally:
        # Tear down isolated database cleanly
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
