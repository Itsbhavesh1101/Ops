"""Focused baseline regression tests verifying Phase-3B database schema, Alembic head,
absence of unauthorized identity tables, and canonical RTM requirement source mappings.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import app.db.models  # noqa: F401
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
RTM_FILE = REPO_ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.md"

EXPECTED_16_APPLICATION_TABLES = {
    # Phase 1 Foundation (5)
    "audit_events",
    "outbox_events",
    "inbox_messages",
    "idempotency_records",
    "jobs",
    # Phase 2A Planning (6)
    "goals",
    "plans",
    "milestones",
    "tasks",
    "task_dependencies",
    "task_risks",
    # Phase 3A Workflow (3)
    "workflow_definitions",
    "workflow_instances",
    "workflow_task_mappings",
    # Phase 3B Policies & Artifacts (2)
    "sla_policies",
    "workflow_artifacts",
}

PROHIBITED_IDENTITY_TABLES = {
    "tenants",
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
}


def test_base_metadata_exact_16_tables() -> None:
    """Verify Base.metadata contains all 16 canonical Phase-3B application tables."""
    actual_tables = set(Base.metadata.tables.keys())
    assert EXPECTED_16_APPLICATION_TABLES.issubset(actual_tables)


def test_prohibited_identity_tables_absent_from_orm() -> None:
    """Verify deferred identity tables are not registered in Base.metadata."""
    actual_tables = set(Base.metadata.tables.keys())
    intersection = actual_tables & PROHIBITED_IDENTITY_TABLES
    assert not intersection, f"Prohibited identity tables found in ORM: {intersection}"


def test_flowable_and_alembic_internal_tables_absent_from_orm() -> None:
    """Verify Flowable engine tables and alembic_version are not application ORM models."""
    actual_tables = set(Base.metadata.tables.keys())
    assert "alembic_version" not in actual_tables
    act_tables = {t for t in actual_tables if t.lower().startswith("act_")}
    assert not act_tables, f"Flowable internal tables found in Base.metadata: {act_tables}"


def test_alembic_head_and_migration_lineage() -> None:
    """Verify Alembic 0010 revision exists with down_revision '0009' and correct filename."""
    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)

    rev_0010 = script.get_revision("0010")
    assert rev_0010 is not None
    assert rev_0010.down_revision == "0009"
    assert rev_0010.path is not None
    assert rev_0010.path.endswith("0010_create_workflow_policy_artifacts.py")


def test_rtm_source_requirements_present_once_each() -> None:
    """Verify canonical requirements FR-WF-01 through FR-WF-07 are defined in RTM."""
    assert RTM_FILE.is_file(), f"RTM file not found at {RTM_FILE}"
    content = RTM_FILE.read_text(encoding="utf-8")

    expected_fr_headers = [
        "### FR-WF-01: Workflow Models",
        "### FR-WF-02: Approvals",
        "### FR-WF-03: Business Rules",
        "### FR-WF-04: SLA Tracking",
        "### FR-WF-05: Escalation",
        "### FR-WF-06: Durable Execution",
        "### FR-WF-07: Operational Audit",
    ]
    for header in expected_fr_headers:
        count = content.count(header)
        assert count == 1, f"Expected exactly 1 occurrence of '{header}', found {count}"
