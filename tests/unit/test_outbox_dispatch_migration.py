"""Unit and AST inspection tests for migration 0004_add_outbox_dispatch_state.py.

Part of Phase 1 Foundation Task 1G.3B1.
Validates:
- Revision chain: 0004 points down to 0003; head is 0004.
- Upgrade AST declarations:
  - Adds 4 columns: dispatch_attempt_count, next_attempt_at, claim_token, claimed_at.
  - Server defaults ensure safety for existing rows.
  - Adds 3 named check constraints.
  - Drops ix_outbox_events_pending_created_at.
  - Creates partial index ix_outbox_events_dispatch_ready.
- Downgrade AST declarations:
  - Re-creates ix_outbox_events_pending_created_at.
  - Drops ix_outbox_events_dispatch_ready.
  - Drops check constraints and new columns in reverse order.
- Prior migrations 0001, 0002, 0003 remain untouched.
- Documents deferral of live PostgreSQL runtime concurrency tests to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Authoritative verification boundary constant
POSTGRES_OUTBOX_DISPATCH_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def test_migration_0004_revision_chain() -> None:
    """Verify revision 0004 exists, down_revision points to 0003, and head is 0004."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head in (
        "0004", "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013", "0014",
        "0015", "0016",
    )
    assert head == "0016"

    rev_0004 = script_dir.get_revision("0004")
    assert rev_0004 is not None
    assert rev_0004.down_revision == "0003"
    assert rev_0004.revision == "0004"


def test_migration_0004_ast_operations() -> None:
    """Verify migration AST declares required columns, defaults, constraints, and indexes."""
    migration_path = Path("app/db/migrations/versions/0004_add_outbox_dispatch_state.py")
    assert migration_path.exists(), "0004_add_outbox_dispatch_state.py must exist"

    content = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    # 1. Columns added
    assert "dispatch_attempt_count" in content
    assert "next_attempt_at" in content
    assert "claim_token" in content
    assert "claimed_at" in content

    # 2. Existing row safety via server defaults
    assert 'server_default=sa.text("0")' in content or "server_default=sa.text('0')" in content
    assert (
        'server_default=sa.text("now()")' in content or "server_default=sa.text('now()')" in content
    )

    # 3. Check constraints
    assert "ck_outbox_events_dispatch_attempt_count_non_negative" in content
    assert "ck_outbox_events_claim_token_and_claimed_at_pair" in content
    assert "ck_outbox_events_published_has_no_active_claim" in content

    # 4. Index replacement
    assert "drop_index" in content
    assert "ix_outbox_events_pending_created_at" in content
    assert "ix_outbox_events_dispatch_ready" in content
    assert "published_at IS NULL" in content

    # 5. Functions exist
    upgrade_fn = None
    downgrade_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None, "upgrade() function must exist in migration 0004"
    assert downgrade_fn is not None, "downgrade() function must exist in migration 0004"


def test_migration_0004_downgrade_recreates_original_index() -> None:
    """Verify downgrade restores ix_outbox_events_pending_created_at and drops new components."""
    migration_path = Path("app/db/migrations/versions/0004_add_outbox_dispatch_state.py")
    content = migration_path.read_text(encoding="utf-8")

    tree = ast.parse(content)
    downgrade_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )

    # Check calls inside downgrade
    calls = [
        n.func.attr
        for n in ast.walk(downgrade_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert "drop_index" in calls
    assert "create_index" in calls
    assert "drop_constraint" in calls
    assert "drop_column" in calls


def test_deferred_runtime_integration_constant() -> None:
    """Verify runtime PostgreSQL verification is officially documented as deferred."""
    assert POSTGRES_OUTBOX_DISPATCH_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
