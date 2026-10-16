"""Focused baseline regression tests verifying Phase-4A database schema, Alembic head,
absence of unauthorized identity tables, and canonical RTM requirement source mappings.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import app.db.models  # noqa: F401
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
RTM_FILE = REPO_ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.md"

EXPECTED_24_APPLICATION_TABLES = {
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
    # Phase 4A Generic Scheduling Core (7)
    "resources",
    "resource_availabilities",
    "constraint_definitions",
    "solver_profiles",
    "schedule_jobs",
    "schedules",
    "schedule_assignments",
    # Phase 4B Calendar Boundary (1)
    "calendar_links",
    # Phase 7 Calendar Watch (1)
    "calendar_watch_channels",
    # Phase 8D Service Credentials Persistence (1)
    "service_api_credentials",
}

PROHIBITED_IDENTITY_TABLES = {
    "tenants",
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
}


def test_base_metadata_exact_24_tables() -> None:
    """Verify Base.metadata contains exactly the canonical application tables."""
    actual_tables = set(Base.metadata.tables.keys())
    assert actual_tables == EXPECTED_24_APPLICATION_TABLES
    assert len(actual_tables) == 26


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
    """Verify Alembic head is exactly '0014' with down_revision '0013' and correct filename."""
    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == ["0014"], f"Expected single head ['0014'], got {heads}"

    rev_0014 = script.get_revision("0014")
    assert rev_0014 is not None
    assert rev_0014.down_revision == "0013"
    assert rev_0014.path is not None
    assert rev_0014.path.endswith("0014_service_api_credentials.py")

    rev_0013 = script.get_revision("0013")
    assert rev_0013 is not None
    assert rev_0013.down_revision == "0012"
    assert rev_0013.path is not None
    assert rev_0013.path.endswith("0013_calendar_watch_channels.py")

    rev_0012 = script.get_revision("0012")
    assert rev_0012 is not None
    assert rev_0012.down_revision == "0011"
    assert rev_0012.path is not None
    assert rev_0012.path.endswith("0012_schedule_publish_repair_calendar.py")

    rev_0011 = script.get_revision("0011")
    assert rev_0011 is not None
    assert rev_0011.down_revision == "0010"
    assert rev_0011.path is not None
    assert rev_0011.path.endswith("0011_create_generic_scheduling_core.py")

    all_revs = [rev.revision for rev in script.walk_revisions()]
    assert not any("0015" in r for r in all_revs), "Migration 0015 must not exist"


def test_rtm_source_requirements_present_once_each() -> None:
    """Verify requirements FR-SC-01..06 are defined in RTM with canonical semantics."""
    assert RTM_FILE.is_file(), f"RTM file not found at {RTM_FILE}"
    content = RTM_FILE.read_text(encoding="utf-8")

    expected_fr_headers = [
        "FR-SC-01: Constraint Model",
        "FR-SC-02: Schedule Generation",
        "FR-SC-03: Optimization",
        "FR-SC-04: Incremental Rescheduling",
        "FR-SC-05: Calendar Sync",
        "FR-SC-06: Conflict Explanation",
    ]
    for req in expected_fr_headers:
        assert req in content, f"Expected requirement header '{req}' in RTM"

    # Verify exact interim statuses
    assert "### FR-SC-01: Constraint Model" in content
    assert "### FR-SC-02: Schedule Generation" in content
    assert "### FR-SC-03: Optimization" in content
    assert "### FR-SC-04: Incremental Rescheduling" in content
    assert "### FR-SC-05: Calendar Sync" in content
    assert "### FR-SC-06: Conflict Explanation" in content


def test_no_minimal_unsat_core_overclaim_in_repo() -> None:
    """Verify that unsupported 'minimal unsat core' terminology is absent from repo."""
    prohibited = ["minimal unsat core", "minimal unsatisfiable core", "minimum unsat core"]
    this_file = Path(__file__).resolve()
    for path in REPO_ROOT.rglob("*.py"):
        if (
            path.resolve() == this_file
            or ".git" in str(path)
            or ".venv" in str(path)
            or "node_modules" in str(path)
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for phrase in prohibited:
            assert phrase not in text, f"Found prohibited phrase '{phrase}' in {path}"

    for path in (REPO_ROOT / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for phrase in prohibited:
            assert phrase not in text, f"Found prohibited phrase '{phrase}' in {path}"


def test_no_invented_scheduling_events_in_outbox() -> None:
    """Verify app/scheduling does not emit invented domain/outbox event types."""
    from app import scheduling

    scheduling_dir = Path(scheduling.__file__).parent
    for py_file in scheduling_dir.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8", errors="ignore")
        # Assert no outbox publisher or unapproved event classes
        assert "OutboxPublisher" not in code, f"OutboxPublisher found in {py_file}"
        assert "ScheduleRequested" not in code, f"Invented event found in {py_file}"
        assert "ScheduleSolving" not in code, f"Invented event found in {py_file}"
        assert "ScheduleFeasible" not in code, f"Invented event found in {py_file}"
        assert "ScheduleInfeasible" not in code, f"Invented event found in {py_file}"


def test_schedule_lifecycle_phase_4a_cannot_publish_or_supersede() -> None:
    """Verify Phase 4A candidate allows FEASIBLE->REVIEW but strictly forbids PUBLISHED."""
    import pytest

    from app.scheduling.states import (
        InvalidStateTransitionError,
        ScheduleLifecycleStatus,
        validate_schedule_transition,
    )

    # Valid Phase 4 transitions
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.FEASIBLE, ScheduleLifecycleStatus.REVIEW
        )
        == ScheduleLifecycleStatus.REVIEW
    )
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.REVIEW, ScheduleLifecycleStatus.PUBLISHED
        )
        == ScheduleLifecycleStatus.PUBLISHED
    )
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.PUBLISHED, ScheduleLifecycleStatus.SUPERSEDED
        )
        == ScheduleLifecycleStatus.SUPERSEDED
    )

    # Illegal transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.FEASIBLE, ScheduleLifecycleStatus.PUBLISHED
        )

    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.PUBLISHED, ScheduleLifecycleStatus.REVIEW
        )

    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.SUPERSEDED, ScheduleLifecycleStatus.PUBLISHED
        )

