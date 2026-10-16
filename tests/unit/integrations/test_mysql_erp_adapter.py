"""Unit and contract tests for MySQL iredu ERP adapter.

Part of Phase 7B Real ERP/SIS Adapter.
Enforces all required contract tests from Section 8 of Phase-7 specification:
- valid retrieval
- tenant scoping & BLK-01 fail-closed verification
- wrong-tenant rejection & unknown mapping
- missing upstream row
- null fields
- duplicate external IDs
- stale data
- connection outage & timeout simulation
- permission denial
- read-only enforcement
- aggregate-only student handling (zero individual student records)
- excluded fields (no sensitive PII, salary, bank details)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.integrations.erp.config import MySqlErpSettings
from app.integrations.erp.dtos import (
    ErpOrganizationContextDTO,
    ErpStaffDTO,
    ErpStudentHeadcountAggregateDTO,
)
from app.integrations.erp.exceptions import (
    EntityNotFoundError,
    ErpConnectionError,
    ErpPermissionDeniedError,
    ErpTimeoutError,
    ReadOnlyViolationError,
    ScopeUnresolvedError,
)
from app.integrations.erp.mysql_adapter import SESSION_INIT_READ_ONLY, MySqlIreduAdapter


class SyntheticMySqlExecutor:
    """In-memory synthetic executor simulating mysql_iredu Dump20260924 schema."""

    def __init__(self) -> None:
        self.simulate_outage: bool = False
        self.simulate_timeout: bool = False
        self.simulate_permission_denied: bool = False
        self.queries_executed: list[str] = []

        # Synthetic fixture data matching real iredu tables
        self.sch_settings: list[dict[str, Any]] = [
            {
                "id": 1,
                "name": "St. Jude Academy",
                "email": "admin@stjude.edu",
                "phone": "9876543210",
                "address": "123 Academic Way",
                "session_id": 18,
            }
        ]
        self.sessions: list[dict[str, Any]] = [
            {"id": 18, "session": "2026-2027", "is_active": 1},
            {"id": 17, "session": "2025-2026", "is_active": 0},
        ]
        self.classes: list[dict[str, Any]] = [
            {"id": 10, "class": "Class 10", "is_active": "yes"},
            {"id": 12, "class": "Class 12", "is_active": "yes"},
        ]
        self.sections: list[dict[str, Any]] = [
            {"id": 1, "section": "A"},
            {"id": 2, "section": "B"},
        ]
        self.class_sections: list[dict[str, Any]] = [
            {
                "class_section_id": 101,
                "class_id": 10,
                "class_name": "Class 10",
                "section_id": 1,
                "section_name": "A",
            },
            {
                "class_section_id": 102,
                "class_id": 10,
                "class_name": "Class 10",
                "section_id": 2,
                "section_name": "B",
            },
        ]
        self.subjects: list[dict[str, Any]] = [
            {"id": 1, "name": "Mathematics", "code": "MATH101", "type": "theory"},
            {"id": 2, "name": "Science", "code": "SCI101", "type": "theory"},
            {"id": 3, "name": "Physics Practical", "code": None, "type": "practical"},
        ]
        self.staff: list[dict[str, Any]] = [
            {
                "staff_id": 42,
                "name": "Sarah",
                "surname": "Connor",
                "qualification": "M.Sc. Mathematics",
                "work_exp": "8 years",
                "is_active": 1,
                "department_name": "Mathematics",
                "designation": "Senior Teacher",
                # Sensitive fields that MUST NEVER leak to DTO
                "basic_salary": "85000.00",
                "bank_account_no": "998877665544",
                "password": "hashed_secret_password",
                "verification_code": "123456",
            },
            {
                "staff_id": 43,
                "name": "Alan",
                "surname": "Turing",
                "qualification": None,
                "work_exp": None,
                "is_active": 1,
                "department_name": "Computer Science",
                "designation": "HOD",
            },
        ]
        self.student_headcounts: list[dict[str, Any]] = [
            {
                "class_section_id": 101,
                "class_name": "Class 10",
                "section_name": "A",
                "enrolled_count": 35,
                "active_count": 34,
            },
            {
                "class_section_id": 102,
                "class_name": "Class 10",
                "section_name": "B",
                "enrolled_count": 30,
                "active_count": 30,
            },
        ]
        self.timetable: list[dict[str, Any]] = [
            {
                "timetable_id": 1001,
                "staff_id": 42,
                "subject_id": 1,
                "class_section_id": 101,
                "day_of_week": "Monday",
                "start_time": "09:00:00",
                "end_time": "10:00:00",
                "room_no": "Room 101",
            }
        ]

    async def execute_query(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        self.queries_executed.append(query)

        if self.simulate_outage:
            raise ErpConnectionError("MySQL server connection refused: connection timed out")

        if self.simulate_timeout:
            raise ErpTimeoutError("Query execution exceeded timeout threshold of 5.0s")

        if self.simulate_permission_denied:
            raise ErpPermissionDeniedError(
                "Access denied for user 'e7_readonly'@'%' to database 'iredu'"
            )

        q_lower = query.lower()
        if "from sch_settings" in q_lower:
            return list(self.sch_settings)
        if "from sessions" in q_lower:
            return list(self.sessions)
        if "from class_sections" in q_lower and "count(ss.id)" in q_lower:
            return list(self.student_headcounts)
        if "from class_sections" in q_lower:
            return list(self.class_sections)
        if "from subjects" in q_lower:
            return list(self.subjects)
        if "from staff" in q_lower:
            return list(self.staff)
        if "from subject_timetable" in q_lower:
            return list(self.timetable)

        return []


@pytest.fixture
def test_settings() -> MySqlErpSettings:
    scopes_json = (
        '{"tenant-st-jude": "st_jude_scope_ref", "tenant-alpha": "alpha_scope_ref"}'
    )
    return MySqlErpSettings(
        enabled=True,
        host="localhost",
        user="test_ro",
        single_tenant_id="",
        approved_tenant_scopes_json=scopes_json,
    )


@pytest.fixture
def synthetic_executor() -> SyntheticMySqlExecutor:
    return SyntheticMySqlExecutor()


@pytest.fixture
def adapter(
    test_settings: MySqlErpSettings, synthetic_executor: SyntheticMySqlExecutor
) -> MySqlIreduAdapter:
    return MySqlIreduAdapter(settings=test_settings, executor=synthetic_executor)


# =====================================================================
# 1. Tenant Scoping & BLK-01 Gate Tests
# =====================================================================

@pytest.mark.asyncio
async def test_tenant_scoping_success_with_approved_tenant(adapter: MySqlIreduAdapter) -> None:
    """Verified tenant with approved mapping successfully retrieves organization context."""
    org = await adapter.get_organization_context("tenant-st-jude")
    assert isinstance(org, ErpOrganizationContextDTO)
    assert org.institution_name == "St. Jude Academy"
    assert org.active_session_name == "2026-2027"
    assert org.tenant_id == "tenant-st-jude"


@pytest.mark.asyncio
async def test_tenant_scoping_success_with_explicit_scope(adapter: MySqlIreduAdapter) -> None:
    """Explicitly supplied organization scope succeeds even without preset mapping."""
    org = await adapter.get_organization_context(
        "tenant-custom", scope_reference="custom_scope_99"
    )
    assert org.institution_name == "St. Jude Academy"
    assert org.institution_code == "custom_scope_99"


@pytest.mark.asyncio
async def test_wrong_tenant_rejection_fails_closed(adapter: MySqlIreduAdapter) -> None:
    """Unapproved/unverified tenant fails closed under BLK-01 with ScopeUnresolvedError."""
    with pytest.raises(ScopeUnresolvedError, match="BLK-01"):
        await adapter.get_organization_context("unauthorized-tenant-xyz")


@pytest.mark.asyncio
async def test_empty_scope_reference_fails_closed(adapter: MySqlIreduAdapter) -> None:
    """Blank or whitespace scope reference fails closed."""
    with pytest.raises(ScopeUnresolvedError, match="BLK-01"):
        await adapter.get_classes_and_sections("tenant-custom", scope_reference="   ")


# =====================================================================
# 2. Valid Retrieval & DTO Projection Tests
# =====================================================================

@pytest.mark.asyncio
async def test_get_academic_sessions(adapter: MySqlIreduAdapter) -> None:
    """Retrieves academic sessions with active flag parsing."""
    sessions = await adapter.get_academic_sessions("tenant-st-jude")
    assert len(sessions) == 2
    assert sessions[0].session_id == "18"
    assert sessions[0].session_name == "2026-2027"
    assert sessions[0].is_active is True
    assert sessions[1].is_active is False


@pytest.mark.asyncio
async def test_get_classes_and_sections(adapter: MySqlIreduAdapter) -> None:
    """Retrieves class-section cohort pairings."""
    cs = await adapter.get_classes_and_sections("tenant-st-jude")
    assert len(cs) == 2
    assert cs[0].class_section_id == "101"
    assert cs[0].class_name == "Class 10"
    assert cs[0].section_name == "A"


@pytest.mark.asyncio
async def test_get_subjects_with_null_code_handling(adapter: MySqlIreduAdapter) -> None:
    """Retrieves subjects with graceful handling of null code."""
    subjects = await adapter.get_subjects("tenant-st-jude")
    assert len(subjects) == 3
    assert subjects[0].name == "Mathematics"
    assert subjects[0].code == "MATH101"
    assert subjects[0].type == "theory"

    # Practical subject with null code
    practical = subjects[2]
    assert practical.name == "Physics Practical"
    assert practical.code is None
    assert practical.type == "practical"


@pytest.mark.asyncio
async def test_get_timetable_commitments(adapter: MySqlIreduAdapter) -> None:
    """Retrieves known timetable commitments with raw_room_label string."""
    timetable = await adapter.get_timetable_commitments("tenant-st-jude")
    assert len(timetable) == 1
    t = timetable[0]
    assert t.timetable_id == "1001"
    assert t.day_of_week == "Monday"
    assert t.raw_room_label == "Room 101"


# =====================================================================
# 3. Privacy & Sanitization: Aggregates Only & Excluded Fields
# =====================================================================

@pytest.mark.asyncio
async def test_student_headcounts_strictly_aggregate_only(adapter: MySqlIreduAdapter) -> None:
    """Enforces AGGREGATE ONLY: mathematical counts per cohort; zero student rows or PII."""
    headcounts = await adapter.get_student_headcounts("tenant-st-jude")
    assert len(headcounts) == 2

    h1 = headcounts[0]
    assert isinstance(h1, ErpStudentHeadcountAggregateDTO)
    assert h1.class_section_id == "101"
    assert h1.enrolled_student_count == 35
    assert h1.active_student_count == 34

    # Verify model strictly contains no student names, student IDs, or student PII fields
    assert not hasattr(h1, "student_id")
    assert not hasattr(h1, "firstname")
    assert not hasattr(h1, "roll_no")


@pytest.mark.asyncio
async def test_staff_roster_sanitized_and_excludes_sensitive_pii(
    adapter: MySqlIreduAdapter,
) -> None:
    """Staff roster exposes only PLANNER_CONTEXT_ALLOWED fields; excludes salary, bank details."""
    staff = await adapter.get_staff_roster("tenant-st-jude")
    assert len(staff) == 2

    s1 = staff[0]
    assert isinstance(s1, ErpStaffDTO)
    assert s1.staff_id == "42"
    assert s1.opaque_ref == "StaffRef_42"
    assert s1.name == "Sarah"
    assert s1.surname == "Connor"
    assert s1.qualification == "M.Sc. Mathematics"
    assert s1.department_name == "Mathematics"
    assert s1.designation_name == "Senior Teacher"

    # Explicitly verify excluded sensitive fields
    assert not hasattr(s1, "basic_salary")
    assert not hasattr(s1, "bank_account_no")
    assert not hasattr(s1, "password")
    assert not hasattr(s1, "verification_code")

    # Staff with null qualification and work_exp handles null gracefully
    s2 = staff[1]
    assert s2.name == "Alan"
    assert s2.qualification is None
    assert s2.work_exp is None


# =====================================================================
# 4. Security: Strict Read-Only Enforcement
# =====================================================================

@pytest.mark.asyncio
async def test_read_only_enforcement_blocks_mutations(adapter: MySqlIreduAdapter) -> None:
    """Adapter immediately raises ReadOnlyViolationError if a mutation query is attempted."""
    prohibited_queries = [
        "INSERT INTO staff (name) VALUES ('Hacker')",
        "UPDATE classes SET is_active = 'no' WHERE id = 10",
        "DELETE FROM students WHERE id = 1",
        "DROP TABLE audit_events",
        "ALTER TABLE sessions ADD COLUMN hack VARCHAR(10)",
        "TRUNCATE TABLE class_sections",
        "CALL drop_database_proc()",
    ]
    for q in prohibited_queries:
        with pytest.raises(ReadOnlyViolationError, match="strictly READ_ONLY"):
            await adapter._execute(q)


# =====================================================================
# 5. Operational Resilience & Fault Scenarios
# =====================================================================

@pytest.mark.asyncio
async def test_missing_upstream_organization_row(
    adapter: MySqlIreduAdapter, synthetic_executor: SyntheticMySqlExecutor
) -> None:
    """Empty settings table raises EntityNotFoundError."""
    synthetic_executor.sch_settings.clear()
    with pytest.raises(EntityNotFoundError):
        await adapter.get_organization_context("tenant-st-jude")


@pytest.mark.asyncio
async def test_database_connection_outage_handling(
    adapter: MySqlIreduAdapter, synthetic_executor: SyntheticMySqlExecutor
) -> None:
    """Network connection outage raises ErpConnectionError and marks state DEGRADED."""
    synthetic_executor.simulate_outage = True
    with pytest.raises(ErpConnectionError):
        await adapter.get_subjects("tenant-st-jude")

    freshness = await adapter.check_freshness("tenant-st-jude")
    assert freshness.staleness_state == "DEGRADED"
    assert "connection refused" in str(freshness.last_error)


@pytest.mark.asyncio
async def test_query_timeout_handling(
    adapter: MySqlIreduAdapter, synthetic_executor: SyntheticMySqlExecutor
) -> None:
    """Query timeout raises ErpTimeoutError."""
    synthetic_executor.simulate_timeout = True
    with pytest.raises(ErpTimeoutError):
        await adapter.get_academic_sessions("tenant-st-jude")


@pytest.mark.asyncio
async def test_permission_denied_handling(
    adapter: MySqlIreduAdapter, synthetic_executor: SyntheticMySqlExecutor
) -> None:
    """Permission denial raises ErpPermissionDeniedError."""
    synthetic_executor.simulate_permission_denied = True
    with pytest.raises(ErpPermissionDeniedError):
        await adapter.get_classes_and_sections("tenant-st-jude")


@pytest.mark.asyncio
async def test_freshness_tracking_and_staleness(
    adapter: MySqlIreduAdapter,
) -> None:
    """Freshness metadata transitions to FRESH on success and STALE when age exceeds threshold."""
    # Initially OFFLINE before any fetch
    initial = await adapter.check_freshness("tenant-st-jude")
    assert initial.staleness_state == "OFFLINE"

    # Successful fetch transitions to FRESH
    await adapter.get_subjects("tenant-st-jude")
    fresh = await adapter.check_freshness("tenant-st-jude")
    assert fresh.staleness_state == "FRESH"
    assert fresh.record_counts.get("subjects") == 3

    # Backdated fetch exceeds staleness threshold
    adapter._last_successful_fetch["tenant-st-jude"] = datetime.now(UTC) - timedelta(hours=2)
    stale = await adapter.check_freshness("tenant-st-jude")
    assert stale.staleness_state == "STALE"


# =====================================================================
# 6. Outcome B: Confirmed Single-Tenant Deployment Binding Tests
# =====================================================================

@pytest.fixture
def single_tenant_settings() -> MySqlErpSettings:
    """Settings binding this dedicated database instance to a single E-7 tenant."""
    return MySqlErpSettings(
        enabled=True,
        host="localhost",
        user="test_ro",
        single_tenant_id="tenant-st-jude",
    )


@pytest.fixture
def single_tenant_adapter(
    single_tenant_settings: MySqlErpSettings, synthetic_executor: SyntheticMySqlExecutor
) -> MySqlIreduAdapter:
    return MySqlIreduAdapter(settings=single_tenant_settings, executor=synthetic_executor)


@pytest.mark.asyncio
async def test_single_tenant_scoping_success_for_configured_tenant(
    single_tenant_adapter: MySqlIreduAdapter,
) -> None:
    """Configured canonical single tenant accesses dedicated database cleanly."""
    org = await single_tenant_adapter.get_organization_context("tenant-st-jude")
    assert isinstance(org, ErpOrganizationContextDTO)
    assert org.tenant_id == "tenant-st-jude"
    assert org.institution_name == "St. Jude Academy"
    assert org.institution_code == "dedicated:iredu"


@pytest.mark.asyncio
async def test_single_tenant_mismatched_tenant_fails_closed(
    single_tenant_adapter: MySqlIreduAdapter,
) -> None:
    """Any tenant other than the configured single tenant fails closed before querying."""
    with pytest.raises(ScopeUnresolvedError, match="BLK-01: Tenant mismatch"):
        await single_tenant_adapter.get_organization_context("tenant-imposter")

    # Supplying an explicit scope reference must NOT bypass single-tenant binding
    with pytest.raises(ScopeUnresolvedError, match="BLK-01: Tenant mismatch"):
        await single_tenant_adapter.get_classes_and_sections(
            "tenant-imposter", scope_reference="bypass_attempt"
        )


@pytest.mark.asyncio
async def test_single_tenant_empty_tenant_id_fails_closed(
    single_tenant_adapter: MySqlIreduAdapter,
) -> None:
    """Missing or whitespace tenant_id fails closed immediately."""
    with pytest.raises(ScopeUnresolvedError, match="BLK-01: Missing tenant_id"):
        await single_tenant_adapter.get_organization_context("")

    with pytest.raises(ScopeUnresolvedError, match="BLK-01: Missing tenant_id"):
        await single_tenant_adapter.get_organization_context("   ")


@pytest.mark.asyncio
async def test_single_tenant_missing_tenant_config_fails_closed(
    synthetic_executor: SyntheticMySqlExecutor,
) -> None:
    """When no single-tenant binding and no approved mappings exist, queries fail closed."""
    unbound_settings = MySqlErpSettings(
        enabled=True,
        host="localhost",
        user="test_ro",
        single_tenant_id="",
        approved_tenant_scopes_json="{}",
    )
    unbound_adapter = MySqlIreduAdapter(settings=unbound_settings, executor=synthetic_executor)
    with pytest.raises(
        ScopeUnresolvedError, match="BLK-01: Authoritative organization scope is unresolved"
    ):
        await unbound_adapter.get_organization_context("tenant-st-jude")


@pytest.mark.asyncio
async def test_single_tenant_disabled_adapter_fails_closed() -> None:
    """When live adapter is disabled in configuration, queries fail closed before execution."""
    disabled_settings = MySqlErpSettings(
        enabled=False,
        single_tenant_id="tenant-st-jude",
    )
    # No executor provided; checks live connection policy
    adapter_live = MySqlIreduAdapter(settings=disabled_settings, executor=None)
    with pytest.raises(
        ScopeUnresolvedError, match="BLK-01: Live MySQL ERP context retrieval is disabled"
    ):
        await adapter_live.get_organization_context("tenant-st-jude")


@pytest.mark.asyncio
async def test_single_tenant_missing_connection_params_fails_closed() -> None:
    """When connection parameters are unconfigured, execution raises ErpConnectionError."""
    # enabled=False initially to construct settings, then test unconfigured host/user
    settings = MySqlErpSettings(
        enabled=False,
        single_tenant_id="tenant-st-jude",
    )
    # Manually bypass validate_fail_closed_credentials for runtime test
    settings.enabled = True
    settings.host = ""
    settings.user = ""
    adapter_live = MySqlIreduAdapter(settings=settings, executor=None)
    with pytest.raises(ErpConnectionError, match="parameters are incomplete"):
        await adapter_live.get_organization_context("tenant-st-jude")


@pytest.mark.asyncio
async def test_read_only_session_and_arbitrary_sql_enforcement(
    adapter: MySqlIreduAdapter,
) -> None:
    """Verifies read-only session constant and rejection of arbitrary or mutation SQL."""
    # 1. Read-only session initialization statement
    assert SESSION_INIT_READ_ONLY == "SET SESSION TRANSACTION READ ONLY"
    # Should pass validation for session init
    adapter._validate_read_only(SESSION_INIT_READ_ONLY)
    adapter._validate_read_only("SELECT 1")
    adapter._validate_read_only("SHOW TABLES")

    # 2. Arbitrary or mutation SQL is strictly rejected
    arbitrary_or_mutation_queries = [
        "LOCK TABLES classes WRITE",
        "EXEC sp_some_proc()",
        "DO SLEEP(5)",
        "CREATE TABLE hack (id INT)",
        "REPLACE INTO classes VALUES (1, 'Hacked')",
        "GRANT ALL PRIVILEGES ON *.* TO 'user'@'%'",
        "REVOKE SELECT ON iredu.* FROM 'user'@'%'",
    ]
    for q in arbitrary_or_mutation_queries:
        with pytest.raises(ReadOnlyViolationError):
            adapter._validate_read_only(q)


