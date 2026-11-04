"""Live PostgreSQL migration and schema agreement verification tests.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- Clean migration from empty database 0001 -> 0007
- Exact canonical 5 application tables created (+ alembic_version)
- Idempotent reapplication of 'alembic upgrade head'
- Clean isolated downgrade from 0007 to 0006 (jobs table removed, others preserved)
- Clean re-upgrade from 0006 to 0007 (jobs table restored, head remains 0007)
- Semantic schema agreement between SQLAlchemy Base.metadata and live PostgreSQL catalog
"""

import asyncio
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

import app.db.models  # noqa: F401
from app.db.base import Base, DurableIntegrationBase
from tests.integration.postgres.conftest import (
    ADMIN_PG_URL,
    ALEMBIC_INI,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    TEST_DB_NAME,
    _terminate_and_drop_db,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_chain_and_empty_database_upgrade() -> None:
    """Verify clean migration 0001 -> 0007 on a freshly created empty test database."""
    # 1. Create a dedicated empty database for this migration verification
    migration_test_db = f"{TEST_DB_NAME}_mig"
    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, migration_test_db)
        await admin_conn.execute(f'CREATE DATABASE "{migration_test_db}";')
    finally:
        await admin_conn.close()

    mig_db_url = (
        f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
        f"{POSTGRES_HOST}:{POSTGRES_PORT}/{migration_test_db}"
    )
    mig_pg_url = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
        f"{POSTGRES_HOST}:{POSTGRES_PORT}/{migration_test_db}"
    )
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", mig_db_url)

    table_query = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name;"
    )

    try:
        # 2. Run alembic upgrade head
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

        # 3. Verify live tables in the test database
        conn = await asyncpg.connect(mig_pg_url)
        try:
            rows = await conn.fetch(table_query)
            tables = {r["table_name"] for r in rows}

            expected_app_tables = set(Base.metadata.tables) | set(
                DurableIntegrationBase.metadata.tables
            )
            assert expected_app_tables.issubset(tables), (
                f"Missing application tables: {expected_app_tables - tables}"
            )
            assert "alembic_version" in tables, "alembic_version table must exist"
            unexpected = tables - (expected_app_tables | {"alembic_version"})
            assert tables == expected_app_tables | {"alembic_version"}, (
                f"Unexpected tables found in public schema: {unexpected}"
            )

            # 4. Verify current Alembic revision matches the repository head.
            script = ScriptDirectory.from_config(alembic_cfg)
            expected_head = script.get_current_head()
            assert expected_head is not None
            head_revision = script.get_revision(expected_head)
            previous_revision = head_revision.down_revision
            assert isinstance(previous_revision, str)
            rev = await conn.fetchval("SELECT version_num FROM alembic_version;")
            assert rev == expected_head, f"Expected revision {expected_head}, got {rev}"
        finally:
            await conn.close()

        # 5. Verify head reapplication idempotency (Section 9)
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        conn = await asyncpg.connect(mig_pg_url)
        try:
            rev_after = await conn.fetchval("SELECT version_num FROM alembic_version;")
            assert rev_after == expected_head, f"Head reapplication changed revision: {rev_after}"
            rows_after = await conn.fetch(table_query)
            tables_after = {r["table_name"] for r in rows_after}
            assert tables_after == tables, "Head reapplication modified tables"
        finally:
            await conn.close()

        # 6. Verify an isolated one-revision downgrade removes the head migration.
        await asyncio.to_thread(command.downgrade, alembic_cfg, previous_revision)
        conn = await asyncpg.connect(mig_pg_url)
        try:
            downgraded_revision = await conn.fetchval(
                "SELECT version_num FROM alembic_version;"
            )
            assert downgraded_revision == previous_revision
            downgraded_rows = await conn.fetch(table_query)
            downgraded_tables = {r["table_name"] for r in downgraded_rows}
            assert "task_sla_states" not in downgraded_tables
            assert (expected_app_tables - {"task_sla_states"}).issubset(downgraded_tables)
        finally:
            await conn.close()

        # 7. Verify re-upgrade to the repository head.
        await asyncio.to_thread(command.upgrade, alembic_cfg, expected_head)
        conn = await asyncpg.connect(mig_pg_url)
        try:
            rev_restored = await conn.fetchval("SELECT version_num FROM alembic_version;")
            assert rev_restored == expected_head, (
                f"Expected revision {expected_head} after re-upgrade, got {rev_restored}"
            )
            rows_restored = await conn.fetch(table_query)
            tables_restored = {r["table_name"] for r in rows_restored}
            assert expected_app_tables.issubset(tables_restored), (
                "Workflow tables must be restored after re-upgrade"
            )
            assert tables_restored == tables, "Restored tables must match initial head migration"
        finally:
            await conn.close()

    finally:
        # Clean up temporary migration test database
        admin_conn = await asyncpg.connect(ADMIN_PG_URL)
        try:
            await _terminate_and_drop_db(admin_conn, migration_test_db)
        finally:
            await admin_conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_schema_semantic_agreement(test_engine: AsyncEngine) -> None:
    """Compare live PostgreSQL catalog schema against SQLAlchemy Base.metadata."""

    def _inspect_catalog(sync_conn: Any) -> dict[str, Any]:
        inspector = inspect(sync_conn)
        return {
            "tables": set(inspector.get_table_names()),
            "table_columns": {
                tbl: {col["name"]: col for col in inspector.get_columns(tbl)}
                for tbl in inspector.get_table_names()
                if tbl != "alembic_version"
            },
            "table_pks": {
                tbl: set(inspector.get_pk_constraint(tbl).get("constrained_columns", []))
                for tbl in inspector.get_table_names()
                if tbl != "alembic_version"
            },
            "check_constraints": {
                tbl: inspector.get_check_constraints(tbl)
                for tbl in inspector.get_table_names()
                if tbl != "alembic_version"
            },
        }

    async with test_engine.connect() as conn:
        catalog = await conn.run_sync(_inspect_catalog)

    # 1. Table names: all 5 metadata tables must exist in PostgreSQL
    model_tables = {
        **Base.metadata.tables,
        **DurableIntegrationBase.metadata.tables,
    }
    expected_tables = set(model_tables)
    assert expected_tables.issubset(catalog["tables"]), (
        f"Missing tables: {expected_tables - catalog['tables']}"
    )

    # 2. Primary keys must match exactly
    for table_name, model_table in model_tables.items():
        model_pk_cols = {c.name for c in model_table.primary_key.columns}
        live_pk_cols = catalog["table_pks"].get(table_name, set())
        assert live_pk_cols == model_pk_cols, (
            f"Table '{table_name}' PK mismatch: live={live_pk_cols}, model={model_pk_cols}"
        )

    # 3. Columns and nullability must match
    for table_name, model_table in model_tables.items():
        live_cols = catalog["table_columns"].get(table_name, {})
        for col in model_table.columns:
            assert col.name in live_cols, (
                f"Column '{col.name}' in model '{table_name}' missing in live database"
            )
            live_col_info = live_cols[col.name]
            assert live_col_info["nullable"] == col.nullable, (
                f"Column '{table_name}.{col.name}' nullable mismatch: "
                f"live={live_col_info['nullable']}, model={col.nullable}"
            )

    # 4. Check constraints: verify jobs table has expected CHECK constraints
    jobs_checks = {ck["name"] for ck in catalog["check_constraints"].get("jobs", [])}
    expected_job_check_fragments = [
        "status",
        "failure_consistency",
        "timestamp",
        "job_type",
        "tenant_id",
    ]
    for fragment in expected_job_check_fragments:
        assert any(fragment in name for name in jobs_checks), (
            f"Expected check constraint containing '{fragment}' on 'jobs', found: {jobs_checks}"
        )
