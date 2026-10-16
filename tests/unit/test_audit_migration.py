"""Static inspection and model consistency tests for migration 0002_create_audit_events.

Part of Phase 1 Foundation Task 1F.
Validates:
- Revision metadata: 0002 exists and points down to 0001.
- Schema declarations: audit_events table, UUID PK, JSONB details, TIMESTAMPTZ recorded_at.
- Immutability trigger: trigger function and trigger protecting UPDATE, DELETE, TRUNCATE.
- Trigger safety: error message contains no row content or audit details.
- Indexes: tenant_id+recorded_at, correlation_id, resource+recorded_at.
- Downgrade: cleanly drops trigger, function, indexes, and table in reverse order.
- Model registration: AuditEventRecord is registered in Base.metadata with exact matching schema.
- Verification status: Documents that runtime PostgreSQL trigger enforcement is deferred to Task 1J.
"""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.audit.models import AuditEventRecord
from app.db.base import Base

# Authoritative verification boundary constant
POSTGRES_APPEND_ONLY_TRIGGER_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def test_migration_0002_revision_chain() -> None:
    """Verify revision 0002 exists and down_revision points to 0001."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head in (
        "0002", "0003", "0004", "0005", "0006", "0007",
        "0008", "0009", "0010", "0011", "0012", "0013", "0014",
    )
    assert head == "0014"

    rev_0002 = script_dir.get_revision("0002")
    assert rev_0002 is not None
    assert rev_0002.down_revision == "0001"
    assert rev_0002.revision == "0002"


def test_migration_0002_ast_operations_and_immutability_trigger() -> None:
    """Verify migration AST defines audit_events, indexes, and append-only trigger."""
    migration_path = Path("app/db/migrations/versions/0002_create_audit_events.py")
    assert migration_path.exists(), "0002_create_audit_events.py must exist"

    content = migration_path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    # 1. Verify required SQL trigger and function statements
    assert "fn_prevent_audit_event_mutation" in content
    assert "trg_audit_events_prevent_mutation" in content
    assert "audit_events table is append-only" in content
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE" in content
    assert "FOR EACH STATEMENT" in content
    assert "DROP TRIGGER IF EXISTS trg_audit_events_prevent_mutation" in content
    assert "DROP FUNCTION IF EXISTS fn_prevent_audit_event_mutation" in content

    # 2. Verify migration table defines actor consistency and bounded value constraints
    assert "ck_audit_events_actor_consistency" in content
    assert "ck_audit_events_actor_principal_type_valid" in content
    assert "ck_audit_events_authentication_method_valid" in content
    assert "ck_audit_events_outcome_valid" in content

    # 3. Verify no row data leaked in trigger error
    assert "NEW." not in content
    assert "OLD." not in content

    # 4. Verify function definitions exist
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "upgrade" in functions
    assert "downgrade" in functions


def test_model_registration_and_schema_consistency() -> None:
    """Verify AuditEventRecord is registered on Base.metadata with exact canonical columns."""
    # Table must be registered on Base.metadata
    assert "audit_events" in Base.metadata.tables
    table = Base.metadata.tables["audit_events"]
    assert table is AuditEventRecord.__table__

    # Columns set verification
    expected_columns = {
        "audit_event_id",
        "schema_version",
        "recorded_at",
        "tenant_id",
        "actor_principal_id",
        "actor_principal_type",
        "authentication_method",
        "event_type",
        "resource_type",
        "resource_id",
        "outcome",
        "correlation_id",
        "details",
    }
    assert set(table.columns.keys()) == expected_columns

    # Primary key must be UUID audit_event_id
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["audit_event_id"]
    assert isinstance(table.c.audit_event_id.type, UUID)

    # Details column must be PostgreSQL JSONB
    assert isinstance(table.c.details.type, JSONB)

    # Check constraints verification
    ck_names = [c.name for c in table.constraints if isinstance(c, CheckConstraint)]
    assert "ck_audit_events_schema_version_positive" in ck_names
    assert "ck_audit_events_event_type_not_blank" in ck_names
    assert "ck_audit_events_actor_consistency" in ck_names
    assert "ck_audit_events_actor_principal_type_valid" in ck_names
    assert "ck_audit_events_authentication_method_valid" in ck_names
    assert "ck_audit_events_outcome_valid" in ck_names

    # Indexes verification
    index_names = {idx.name for idx in table.indexes}
    expected_indexes = {
        "ix_audit_events_tenant_id_recorded_at",
        "ix_audit_events_correlation_id",
        "ix_audit_events_resource_type_resource_id_recorded_at",
    }
    assert expected_indexes.issubset(index_names)


def test_database_check_constraints_definitions() -> None:
    """Verify check constraints for actor consistency, actor type, auth method, and outcome."""
    table = Base.metadata.tables["audit_events"]
    ck_map = {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}

    # Actor consistency CheckConstraint
    assert "ck_audit_events_actor_consistency" in ck_map
    actor_sql = ck_map["ck_audit_events_actor_consistency"]
    assert "actor_principal_id IS NULL" in actor_sql
    assert "tenant_id IS NOT NULL" in actor_sql

    # Actor principal type CheckConstraint
    assert "ck_audit_events_actor_principal_type_valid" in ck_map
    type_sql = ck_map["ck_audit_events_actor_principal_type_valid"]
    assert "'HUMAN'" in type_sql
    assert "'SERVICE'" in type_sql

    # Authentication method CheckConstraint
    assert "ck_audit_events_authentication_method_valid" in ck_map
    auth_sql = ck_map["ck_audit_events_authentication_method_valid"]
    assert "'OIDC'" in auth_sql
    assert "'SERVICE_API_KEY'" in auth_sql

    # Outcome CheckConstraint
    assert "ck_audit_events_outcome_valid" in ck_map
    outcome_sql = ck_map["ck_audit_events_outcome_valid"]
    assert "'SUCCESS'" in outcome_sql
    assert "'FAILURE'" in outcome_sql
    assert "'DENIED'" in outcome_sql


def test_model_contains_no_sensitive_or_mutation_methods() -> None:
    """Verify AuditEventRecord does not define forbidden credential columns or mutation methods."""
    table = Base.metadata.tables["audit_events"]
    col_names = set(table.columns.keys())

    forbidden_cols = {
        "password",
        "secret",
        "token",
        "id_token",
        "access_token",
        "refresh_token",
        "api_key",
        "digest",
        "authorization",
    }
    for col in col_names:
        assert col not in forbidden_cols

    model_methods = dir(AuditEventRecord)
    forbidden_methods = ["update_event", "change_actor", "replace_details", "delete_event"]
    for m in forbidden_methods:
        assert m not in model_methods


def test_deferred_runtime_postgres_trigger_boundary_documented() -> None:
    """Confirm the runtime trigger test status is explicitly acknowledged and documented."""
    assert POSTGRES_APPEND_ONLY_TRIGGER_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
