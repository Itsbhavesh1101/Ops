"""Unit tests for Phase 1 Task 1D Alembic migration environment.

Verifies:
- alembic.ini exists and contains no plaintext secret or database URL.
- env.py imports Base.metadata as target_metadata.
- env.py dynamically resolves database URL from Settings or E7_DATABASE_URL.
- ScriptDirectory correctly loads migrations from app/db/migrations.
- Initial revision (0001_phase_1_persistence_baseline.py) exists and contains empty operations.
- Zero tables are created in upgrade() and zero tables are dropped in downgrade().
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import Settings
from app.db import DatabaseConfigurationError
from app.db.base import Base


def test_alembic_ini_exists_and_uncredentialed() -> None:
    """Verify alembic.ini exists and does not contain hardcoded credentials or real URLs."""
    ini_path = Path("alembic.ini")
    assert ini_path.exists(), "alembic.ini must exist at repository root"

    content = ini_path.read_text(encoding="utf-8")
    assert "script_location = app/db/migrations" in content

    # Verify sqlalchemy.url is blank or placeholder only
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("sqlalchemy.url"):
            val = stripped.split("=", 1)[1].strip()
            assert val == "" or val.startswith("<"), (
                f"alembic.ini sqlalchemy.url must be blank or placeholder, found: {val}"
            )

    # Negative credential assertions
    assert "password" not in content.lower() or "#" in content
    assert "postgres:" not in content
    assert "localhost:5432" not in content


def test_alembic_env_targets_base_metadata() -> None:
    """Verify env.py configures target_metadata pointing to Base.metadata."""
    import app.db.migrations.env as migration_env

    assert migration_env.target_metadata is Base.metadata


def test_alembic_url_resolution_from_settings() -> None:
    """Verify get_url() resolves dynamically from application Settings."""
    import app.db.migrations.env as migration_env

    test_url = "postgresql+asyncpg://migrator:secret123@db.internal:5432/test_db"
    mock_settings = Settings(database_url=test_url)

    with patch("app.db.migrations.env.get_settings", return_value=mock_settings):
        resolved = migration_env.get_url()
        assert resolved == test_url


def test_alembic_url_resolution_from_environment() -> None:
    """Verify get_url() falls back to E7_DATABASE_URL when Settings.database_url is unset."""
    import app.db.migrations.env as migration_env

    test_url = "postgresql+asyncpg://env_user:env_pass@db.internal:5432/env_db"
    mock_settings = Settings(database_url=None)

    with patch("app.db.migrations.env.get_settings", return_value=mock_settings):
        with patch.dict("os.environ", {"E7_DATABASE_URL": test_url}):
            resolved = migration_env.get_url()
            assert resolved == test_url


def test_alembic_url_resolution_missing_fails_clearly() -> None:
    """Verify get_url() raises DatabaseConfigurationError when URL is not provided."""
    import app.db.migrations.env as migration_env

    mock_settings = Settings(database_url=None)

    with patch("app.db.migrations.env.get_settings", return_value=mock_settings):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises((DatabaseConfigurationError, RuntimeError)) as exc_info:
                migration_env.get_url()
            assert "Database URL is not configured for Alembic migrations" in str(exc_info.value)


@pytest.mark.parametrize(
    "forbidden_url,expected_scheme",
    [
        ("sqlite:///test.db", "sqlite"),
        ("sqlite+aiosqlite:///test.db", "sqlite+aiosqlite"),
        ("mysql://user:pass@localhost:3306/db", "mysql"),
        ("mysql+asyncmy://user:pass@localhost:3306/db", "mysql+asyncmy"),
        ("mariadb+asyncmy://user:pass@localhost:3306/db", "mariadb+asyncmy"),
        ("postgresql://user:pass@localhost:5432/db", "postgresql"),
    ],
)
def test_alembic_rejects_unsupported_schemes(forbidden_url: str, expected_scheme: str) -> None:
    """Verify Alembic get_url() rejects sqlite, mysql, mariadb, and sync postgresql schemes."""
    import app.db.migrations.env as migration_env

    mock_settings = Settings(database_url=forbidden_url)

    with patch("app.db.migrations.env.get_settings", return_value=mock_settings):
        with pytest.raises(DatabaseConfigurationError) as exc_info:
            migration_env.get_url()
        msg = str(exc_info.value)
        assert f"Unsupported database URL scheme '{expected_scheme}'" in msg
        # Negative credential leak assertion
        assert "pass" not in msg
        assert "localhost" not in msg


def test_alembic_raw_env_cannot_bypass_validation() -> None:
    """Verify raw E7_DATABASE_URL in os.environ cannot bypass canonical scheme validation."""
    import app.db.migrations.env as migration_env

    mock_settings = Settings(database_url=None)

    with patch("app.db.migrations.env.get_settings", return_value=mock_settings):
        with patch.dict("os.environ", {"E7_DATABASE_URL": "sqlite:///bypassed.db"}):
            with pytest.raises(DatabaseConfigurationError) as exc_info:
                migration_env.get_url()
            assert "Unsupported database URL scheme 'sqlite'" in str(exc_info.value)


def test_alembic_script_directory_loads_baseline_revision() -> None:
    """Verify ScriptDirectory discovers revisions 0001 through 0009, with head 0009."""
    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)

    head = script_dir.get_current_head()
    assert head == "0016"

    rev_0001 = script_dir.get_revision("0001")
    assert rev_0001 is not None
    assert rev_0001.down_revision is None
    assert "phase_1_persistence_baseline" in (rev_0001.doc or "")

    rev_0002 = script_dir.get_revision("0002")
    assert rev_0002 is not None
    assert rev_0002.down_revision == "0001"
    assert "create_audit_events" in (rev_0002.doc or "")

    rev_0003 = script_dir.get_revision("0003")
    assert rev_0003 is not None
    assert rev_0003.down_revision == "0002"
    assert "create_outbox_events" in (rev_0003.doc or "")

    rev_0004 = script_dir.get_revision("0004")
    assert rev_0004 is not None
    assert rev_0004.down_revision == "0003"
    assert "add_outbox_dispatch_state" in (rev_0004.doc or "")

    rev_0005 = script_dir.get_revision("0005")
    assert rev_0005 is not None
    assert rev_0005.down_revision == "0004"
    assert "create_inbox_messages" in (rev_0005.doc or "")

    rev_0006 = script_dir.get_revision("0006")
    assert rev_0006 is not None
    assert rev_0006.down_revision == "0005"
    assert "create_idempotency_records" in (rev_0006.doc or "")

    rev_0007 = script_dir.get_revision("0007")
    assert rev_0007 is not None
    assert rev_0007.down_revision == "0006"
    assert "create_jobs" in (rev_0007.doc or "")

    # Phase-2A baseline migration 0008
    rev_0008 = script_dir.get_revision("0008")
    assert rev_0008 is not None
    assert rev_0008.down_revision == "0007"
    assert "create_planning_domain" in (rev_0008.doc or "")

    # Phase-3A workflow runtime core migration 0009
    rev_0009 = script_dir.get_revision("0009")
    assert rev_0009 is not None
    assert rev_0009.down_revision == "0008"
    assert "create_workflow_runtime_core" in (rev_0009.doc or "")

    # Phase-3B workflow policy artifacts migration 0010
    rev_0010 = script_dir.get_revision("0010")
    assert rev_0010 is not None
    assert rev_0010.down_revision == "0009"
    assert "create_workflow_policy_artifacts" in (rev_0010.doc or "")

    # Phase-4A generic scheduling core migration 0011
    rev_0011 = script_dir.get_revision("0011")
    assert rev_0011 is not None
    assert rev_0011.down_revision == "0010"
    assert "create_generic_scheduling_core" in (rev_0011.doc or "")

    # Phase-4B generic scheduling publish, repair, calendar migration 0012
    rev_0012 = script_dir.get_revision("0012")
    assert rev_0012 is not None
    assert rev_0012.down_revision == "0011"
    assert "schedule_publish_repair_calendar" in (rev_0012.doc or "")

    # Phase-7 calendar watch channels migration 0013
    rev_0013 = script_dir.get_revision("0013")
    assert rev_0013 is not None
    assert rev_0013.down_revision == "0012"
    assert "calendar_watch_channels" in (rev_0013.doc or "")

    # Phase-8D persistent service credentials migration 0014
    rev_0014 = script_dir.get_revision("0014")
    assert rev_0014 is not None
    assert rev_0014.down_revision == "0013"
    assert "service_api_credentials" in (rev_0014.doc or "")

    # Phase DQ-4 Option C two-stage notification outbox migration 0015
    rev_0015 = script_dir.get_revision("0015")
    assert rev_0015 is not None
    assert rev_0015.down_revision == "0014"
    assert "create_notification_outbox" in (rev_0015.doc or "")

    # Phase DQ-4 AD-DQ4-02 persistent task SLA state migration 0016
    rev_0016 = script_dir.get_revision("0016")
    assert rev_0016 is not None
    assert rev_0016.down_revision == "0015"
    assert "create_task_sla_states" in (rev_0016.doc or "")

    # Verify no migration 0017 exists
    revisions = [rev.revision for rev in script_dir.walk_revisions()]
    assert "0017" not in revisions
    assert "0016" in revisions
    assert "0015" in revisions
    assert "0014" in revisions
    assert "0013" in revisions
    assert "0012" in revisions
    assert "0011" in revisions
    assert "0010" in revisions
    assert "0009" in revisions
    assert "0008" in revisions


def test_initial_migration_contains_zero_table_mutations() -> None:
    """Verify AST of initial migration contains pass in upgrade/downgrade and zero table calls."""
    versions_dir = Path("app/db/migrations/versions")
    migration_files = list(versions_dir.glob("*_phase_1_persistence_baseline.py"))
    assert len(migration_files) == 1, f"Expected 1 baseline migration, found: {migration_files}"

    migration_file = migration_files[0]
    tree = ast.parse(migration_file.read_text(encoding="utf-8"))

    upgrade_fn = None
    downgrade_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                upgrade_fn = node
            elif node.name == "downgrade":
                downgrade_fn = node

    assert upgrade_fn is not None, "upgrade() function must exist in baseline migration"
    assert downgrade_fn is not None, "downgrade() function must exist in baseline migration"

    # Inspect all function calls in upgrade and downgrade
    def get_call_names(fn_node: ast.FunctionDef) -> list[str]:
        names: list[str] = []
        for n in ast.walk(fn_node):
            if isinstance(n, ast.Call):
                if isinstance(n.func, ast.Attribute):
                    names.append(n.func.attr)
                elif isinstance(n.func, ast.Name):
                    names.append(n.func.id)
        return names

    upgrade_calls = get_call_names(upgrade_fn)
    downgrade_calls = get_call_names(downgrade_fn)

    # Invariant: zero table operations in baseline migration
    forbidden_calls = {"create_table", "drop_table", "add_column", "drop_column"}
    for call in upgrade_calls:
        assert call not in forbidden_calls, (
            f"upgrade() must contain 0 table mutations, found: {call}"
        )
    for call in downgrade_calls:
        assert call not in forbidden_calls, (
            f"downgrade() must contain 0 table mutations, found: {call}"
        )
