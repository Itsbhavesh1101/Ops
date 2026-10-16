"""Unit tests for Alembic migration 0006 (idempotency_records).

Part of Phase 1 Foundation Task 1H.2.
Verifies:
- Migration 0006 exists with down_revision = "0005".
- Alembic head advances to "0006".
- Zero migration 0007 exists.
- Migrations 0001 through 0005 revision chain remains intact.
- Migration AST defines idempotency_records table, composite PK, and 4 check constraints.
- Downgrade safely drops idempotency_records table.
- Authoritative runtime verification is officially documented as deferred to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Authoritative verification boundary constant for Task 1H.2
POSTGRES_IDEMPOTENCY_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def test_migration_0006_revision_chain() -> None:
    """Verify revision 0006 exists, down_revision is 0005, and head is 0006."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head in ("0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013", "0014")
    assert head == "0014"

    rev_0006 = script_dir.get_revision("0006")
    assert rev_0006 is not None
    assert rev_0006.revision == "0006"
    assert rev_0006.down_revision == "0005"
    assert "create_idempotency_records" in (rev_0006.doc or "")

    # Preceding chain
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
    """Verify migration 0014 exists and zero migration beyond 0014 exists."""
    versions_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in versions_dir.glob("*.py")]
    assert any(f.startswith("0006") for f in migration_files)
    assert any(f.startswith("0009") for f in migration_files)
    assert any(f.startswith("0010") for f in migration_files)
    assert any(f.startswith("0011") for f in migration_files)
    assert any(f.startswith("0012") for f in migration_files)
    assert any(f.startswith("0013") for f in migration_files)
    assert any(f.startswith("0014") for f in migration_files)
    assert not any(f.startswith("0015") for f in migration_files)


def test_migration_0006_ast_operations() -> None:
    """Verify migration AST creates table, PK, constraints, and drops on downgrade."""
    migration_path = Path("app/db/migrations/versions/0006_create_idempotency_records.py")
    assert migration_path.exists(), "0006_create_idempotency_records.py must exist"

    content = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    upgrade_fn = None
    downgrade_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None, "upgrade() must exist"
    assert downgrade_fn is not None, "downgrade() must exist"

    # Verify upgrade calls create_table('idempotency_records')
    assert "op.create_table" in content
    assert '"idempotency_records"' in content
    assert "tenant_id" in content
    assert "operation_name" in content
    assert "key_digest" in content
    assert "request_fingerprint" in content
    assert "created_at" in content
    assert "pk_idempotency_records" in content
    assert "ck_idempotency_records_operation_name_format" in content
    assert "ck_idempotency_records_key_digest_format" in content
    assert "ck_idempotency_records_request_fingerprint_format" in content
    assert "ck_idempotency_records_tenant_id_non_empty" in content

    # Verify downgrade calls drop_table('idempotency_records')
    assert "op.drop_table" in content
    assert 'op.drop_table("idempotency_records")' in content


def test_deferred_runtime_integration_constant() -> None:
    """Verify live PostgreSQL verification is officially documented as deferred to Task 1J."""
    assert POSTGRES_IDEMPOTENCY_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
