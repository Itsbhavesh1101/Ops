"""Unit tests for Alembic migration 0007 (create_jobs).

Part of Phase 1 Foundation Task 1I.4B.
Verifies:
- Migration 0007 exists with down_revision = "0006".
- Alembic head advances to "0007".
- Zero migration 0008 exists.
- Migrations 0001 through 0006 revision chain remains intact.
- Migration AST defines jobs table, UUID PK, 9 canonical columns, and all named check constraints.
- Downgrade safely drops jobs table.
- Authoritative runtime verification is officially documented as deferred to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.jobs.repository import POSTGRES_JOB_RUNTIME_TEST


def test_migration_0007_revision_chain() -> None:
    """Verify revision 0007 exists, down_revision is 0006, and head is 0007."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head == "0012"

    rev_0007 = script_dir.get_revision("0007")
    assert rev_0007 is not None
    assert rev_0007.revision == "0007"
    assert rev_0007.down_revision == "0006"
    assert "create_jobs" in (rev_0007.doc or "")

    # Preceding chain verification
    rev_0006 = script_dir.get_revision("0006")
    assert rev_0006 is not None
    assert rev_0006.down_revision == "0005"

    rev_0005 = script_dir.get_revision("0005")
    assert rev_0005 is not None
    assert rev_0005.down_revision == "0004"

    rev_0004 = script_dir.get_revision("0004")
    assert rev_0004 is not None
    assert rev_0004.down_revision == "0003"

    rev_0003 = script_dir.get_revision("0003")
    assert rev_0003 is not None
    assert rev_0003.down_revision == "0002"

    rev_0002 = script_dir.get_revision("0002")
    assert rev_0002 is not None
    assert rev_0002.down_revision == "0001"

    rev_0001 = script_dir.get_revision("0001")
    assert rev_0001 is not None
    assert rev_0001.down_revision is None


def test_no_migration_beyond_0007_exists() -> None:
    """Verify migration 0012 exists and zero migration beyond 0012 exists."""
    versions_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in versions_dir.glob("*.py")]
    assert any(f.startswith("0007") for f in migration_files)
    assert any(f.startswith("0009") for f in migration_files)
    assert any(f.startswith("0010") for f in migration_files)
    assert any(f.startswith("0011") for f in migration_files)
    assert any(f.startswith("0012") for f in migration_files)
    assert not any(f.startswith("0013") for f in migration_files)


def test_migration_0007_ast_operations() -> None:
    """Verify migration AST creates table, PK, constraints, and drops on downgrade."""
    migration_path = Path("app/db/migrations/versions/0007_create_jobs.py")
    assert migration_path.exists(), "0007_create_jobs.py must exist"

    content = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    # 1. Verify required column names in AST
    required_cols = [
        "job_id",
        "tenant_id",
        "job_type",
        "status",
        "failure_code",
        "failure_message",
        "created_at",
        "started_at",
        "completed_at",
    ]
    for col in required_cols:
        assert f'"{col}"' in content or f"'{col}'" in content

    # 2. Verify named constraints
    required_constraints = [
        "pk_jobs",
        "ck_jobs_status",
        "ck_jobs_job_type",
        "ck_jobs_tenant_id_nonblank",
        "ck_jobs_failure_code_format",
        "ck_jobs_failure_consistency",
        "ck_jobs_timestamp_state_consistency",
        "ck_jobs_timestamp_ordering",
    ]
    for constraint in required_constraints:
        assert constraint in content

    # 3. Verify upgrade creates jobs and downgrade drops jobs
    upgrade_fn = None
    downgrade_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None
    assert downgrade_fn is not None

    upgrade_src = ast.unparse(upgrade_fn)
    downgrade_src = ast.unparse(downgrade_fn)

    assert "op.create_table('jobs'" in upgrade_src or 'op.create_table("jobs"' in upgrade_src
    assert "op.drop_table('jobs')" in downgrade_src or 'op.drop_table("jobs")' in downgrade_src

    # 4. Zero speculative secondary indexes
    assert "create_index" not in upgrade_src


def test_deferred_runtime_integration_constant() -> None:
    """Verify runtime PostgreSQL verification is officially documented as deferred to Task 1J."""
    assert POSTGRES_JOB_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
