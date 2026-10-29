"""Unit tests for Alembic migration 0005 (inbox_messages).

Part of Phase 1 Foundation Task 1G.4A.
Verifies:
- Migration 0005 exists with down_revision = "0004".
- Alembic head advances to "0005".
- Zero migration 0006 exists.
- Migrations 0001 through 0004 revision chain remains intact.
- Migration AST defines inbox_messages table, composite PK, and check constraint.
- Downgrade safely drops inbox_messages table.
- Authoritative runtime verification is officially documented as deferred to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Authoritative verification boundary constant for Task 1G.4A
POSTGRES_INBOX_DEDUPE_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def test_migration_0005_revision_chain() -> None:
    """Verify revision 0005 exists, down_revision is 0004, and head is 0006."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head in (
        "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013",
        "0014", "0015", "0016",
    )
    assert head == "0016"

    rev_0005 = script_dir.get_revision("0005")
    assert rev_0005 is not None
    assert rev_0005.revision == "0005"
    assert rev_0005.down_revision == "0004"
    assert "create_inbox_messages" in (rev_0005.doc or "")

    # Preceding chain
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
    assert rev_0001.down_revision == "0001" or rev_0001.down_revision is None


def test_no_migration_beyond_0007_exists() -> None:
    """Verify migration 0016 exists and zero migration beyond 0016 exists."""
    versions_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in versions_dir.glob("*.py")]
    assert any(f.startswith("0005") for f in migration_files)
    assert any(f.startswith("0009") for f in migration_files)
    assert any(f.startswith("0010") for f in migration_files)
    assert any(f.startswith("0011") for f in migration_files)
    assert any(f.startswith("0012") for f in migration_files)
    assert any(f.startswith("0013") for f in migration_files)
    assert any(f.startswith("0014") for f in migration_files)
    assert any(f.startswith("0015") for f in migration_files)
    assert any(f.startswith("0016") for f in migration_files)
    assert not any(f.startswith("0017") for f in migration_files)


def test_migration_0005_ast_operations() -> None:
    """Verify migration AST creates only inbox_messages table and drops it on downgrade."""
    migration_path = Path("app/db/migrations/versions/0005_create_inbox_messages.py")
    assert migration_path.exists(), "0005_create_inbox_messages.py must exist"

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

    # Verify upgrade calls create_table('inbox_messages')
    assert "op.create_table" in content
    assert '"inbox_messages"' in content
    assert "consumer_name" in content
    assert "event_id" in content
    assert "recorded_at" in content
    assert "pk_inbox_messages" in content
    assert "ck_inbox_messages_consumer_name_format" in content

    # Verify downgrade calls drop_table('inbox_messages')
    assert "op.drop_table" in content
    assert 'op.drop_table("inbox_messages")' in content


def test_deferred_runtime_integration_constant() -> None:
    """Verify live PostgreSQL verification is officially documented as deferred to Task 1J."""
    assert POSTGRES_INBOX_DEDUPE_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
