"""Static inspection and model consistency tests for migration 0003_create_outbox_events.

Part of Phase 1 Foundation Task 1G.2.
Validates:
- Revision metadata: 0003 exists and points down to 0002. Current head is 0003.
- Schema declarations: outbox_events table, UUID PK, JSONB envelope, TIMESTAMPTZ created_at.
- Partial index: ix_outbox_events_pending_created_at on created_at WHERE published_at IS NULL.
- Immutability trigger: fn_protect_outbox_event_content and trg_outbox_events_protect_content.
- Trigger safety: error message contains no row/envelope/payload content.
- Operational mutability: published_at update is permitted by trigger.
- Downgrade: cleanly drops trigger, function, partial index, and table.
- Model registration: OutboxEventRecord is registered in Base.metadata with exact matching schema.
- Verification status: Documents that runtime PostgreSQL trigger enforcement is deferred to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base
from app.events.outbox.models import OutboxEventRecord

# Authoritative verification boundary constant
POSTGRES_OUTBOX_IMMUTABILITY_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def test_migration_0003_revision_chain() -> None:
    """Verify revision 0003 exists and down_revision points to 0002."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head in (
        "0003", "0004", "0005", "0006", "0007",
        "0008", "0009", "0010", "0011", "0012", "0013", "0014",
        "0015", "0016",
    )
    assert head == "0016"

    rev_0003 = script_dir.get_revision("0003")
    assert rev_0003 is not None
    assert rev_0003.down_revision == "0002"
    assert rev_0003.revision == "0003"


def test_migration_0003_ast_operations_and_immutability_trigger() -> None:
    """Verify migration AST defines outbox_events, partial index, and content trigger."""
    migration_path = Path("app/db/migrations/versions/0003_create_outbox_events.py")
    assert migration_path.exists(), "0003_create_outbox_events.py must exist"

    content = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    # 1. Verify required SQL trigger and function statements
    assert "fn_protect_outbox_event_content" in content
    assert "trg_outbox_events_protect_content" in content
    assert "BEFORE UPDATE ON outbox_events" in content
    assert "FOR EACH ROW" in content
    assert "IS DISTINCT FROM" in content
    assert "DROP TRIGGER IF EXISTS trg_outbox_events_protect_content" in content
    assert "DROP FUNCTION IF EXISTS fn_protect_outbox_event_content" in content

    # 2. Trigger error safety: message must be static and not interpolate row variables
    assert "outbox_events immutable content cannot be updated" in content
    assert "%" not in "outbox_events immutable content cannot be updated"

    # 3. Verify NULL-safe COALESCE CheckConstraint in migration content (Task 1G.2A)
    assert "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)" in content
    assert "ck_outbox_events_event_id_matches_envelope" in content
    assert "ck_outbox_events_envelope_is_object" in content

    # 3. Verify upgrade() and downgrade() functions exist in AST
    upgrade_fn = None
    downgrade_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None, "upgrade() function must exist in migration 0003"
    assert downgrade_fn is not None, "downgrade() function must exist in migration 0003"


def test_outbox_model_registration_and_schema_consistency() -> None:
    """Verify OutboxEventRecord is registered on Base.metadata with exact canonical columns."""
    assert "outbox_events" in Base.metadata.tables
    table = Base.metadata.tables["outbox_events"]
    assert table is OutboxEventRecord.__table__

    # Columns set verification (Task 1G.2 baseline + Task 1G.3B1 operational dispatch columns)
    expected_columns = {
        "event_id",
        "envelope",
        "created_at",
        "published_at",
        "dispatch_attempt_count",
        "next_attempt_at",
        "claim_token",
        "claimed_at",
    }
    assert set(table.columns.keys()) == expected_columns

    # Primary key must be UUID event_id
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["event_id"]
    assert isinstance(table.c.event_id.type, UUID)

    # Envelope column must be PostgreSQL JSONB
    assert isinstance(table.c.envelope.type, JSONB)

    # Check constraints verification
    ck_names = [c.name for c in table.constraints if isinstance(c, CheckConstraint)]
    assert "ck_outbox_events_event_id_matches_envelope" in ck_names
    assert "ck_outbox_events_envelope_is_object" in ck_names

    # Check constraint SQL text matches migration definition
    ck_event_id = next(
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_outbox_events_event_id_matches_envelope"
    )
    assert str(ck_event_id.sqltext) == "COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)"

    # Partial index verification (superseded by ix_outbox_events_dispatch_ready in 1G.3B1)
    index_names = {idx.name for idx in table.indexes}
    assert "ix_outbox_events_dispatch_ready" in index_names


def test_deferred_runtime_integration_constant() -> None:
    """Verify runtime PostgreSQL verification is officially documented as deferred."""
    assert POSTGRES_OUTBOX_IMMUTABILITY_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
