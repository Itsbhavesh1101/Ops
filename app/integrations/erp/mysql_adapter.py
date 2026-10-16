"""Production MySQL iredu ERP/SIS Adapter implementation.

Part of Phase 7B Real ERP/SIS Adapter.
Enforces:
- Strictly READ_ONLY execution (SET SESSION TRANSACTION READ ONLY + mutation prevention).
- BLK-01 / UBQ-TI-01 fail-closed scoping predicate verification.
- Exactly 28 PLANNER_CONTEXT_ALLOWED fields visibility.
- Strictly AGGREGATE_ONLY student headcount queries (zero student PII).
- Predeclared parameterized SQL queries only (no arbitrary or LLM-generated SQL).
- Freshness tracking and state management.
"""

import re
from datetime import UTC, datetime
from typing import Any

from app.integrations.erp.adapter import ErpSisAdapter, QueryExecutor
from app.integrations.erp.config import MySqlErpSettings
from app.integrations.erp.dtos import (
    ErpAcademicSessionDTO,
    ErpClassSectionDTO,
    ErpFreshnessMetadataDTO,
    ErpOrganizationContextDTO,
    ErpStaffDTO,
    ErpStudentHeadcountAggregateDTO,
    ErpSubjectDTO,
    ErpTimetableCommitmentDTO,
)
from app.integrations.erp.exceptions import (
    EntityNotFoundError,
    ErpConnectionError,
    ErpPermissionDeniedError,
    ErpTimeoutError,
    ReadOnlyViolationError,
    ScopeUnresolvedError,
)

# Read-only session initialization and mutation pattern definitions
SESSION_INIT_READ_ONLY = "SET SESSION TRANSACTION READ ONLY"

_MUTATION_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|CALL)\b",
    re.IGNORECASE,
)
_ALLOWED_READ_PREFIXES = ("SELECT", "SET SESSION TRANSACTION READ ONLY", "SHOW")


# Predeclared parameterized queries matching Dump20260924 schema
_QUERY_ORGANIZATION = (
    "SELECT id, name, email, phone, address, session_id "
    "FROM sch_settings LIMIT 1"
)

_QUERY_SESSIONS = (
    "SELECT id, session, is_active "
    "FROM sessions "
    "ORDER BY id ASC"
)

_QUERY_CLASSES_SECTIONS = (
    "SELECT cs.id AS class_section_id, c.id AS class_id, c.class AS class_name, "
    "s.id AS section_id, s.section AS section_name "
    "FROM class_sections cs "
    "JOIN classes c ON cs.class_id = c.id "
    "JOIN sections s ON cs.section_id = s.id "
    "WHERE c.is_active = 'yes' OR c.is_active = '1' "
    "ORDER BY c.id, s.id"
)

_QUERY_SUBJECTS = (
    "SELECT id, name, code, type "
    "FROM subjects "
    "ORDER BY id ASC"
)

# Staff roster: strictly sanitized fields only (zero sensitive PII, zero payroll/bank info)
_QUERY_STAFF_ROSTER = (
    "SELECT st.id AS staff_id, st.name, st.surname, st.qualification, st.work_exp, "
    "st.is_active, d.department_name, des.designation "
    "FROM staff st "
    "LEFT JOIN department d ON st.department = d.id "
    "LEFT JOIN staff_designation des ON st.designation = des.id "
    "WHERE st.is_active = 1 "
    "ORDER BY st.id ASC"
)

# Students: strictly mathematical aggregation only (zero student records, zero individual PII)
_QUERY_STUDENT_HEADCOUNTS = (
    "SELECT cs.id AS class_section_id, c.class AS class_name, sec.section AS section_name, "
    "COUNT(ss.id) AS enrolled_count, "
    "COALESCE(SUM(CASE WHEN ss.is_active = 'yes' OR ss.is_active = '1' "
    "THEN 1 ELSE 0 END), 0) AS active_count "
    "FROM class_sections cs "
    "JOIN classes c ON cs.class_id = c.id "
    "JOIN sections sec ON cs.section_id = sec.id "
    "LEFT JOIN student_session ss ON ss.class_id = c.id AND ss.section_id = sec.id "
    "GROUP BY cs.id, c.class, sec.section "
    "ORDER BY cs.id ASC"
)

# Timetable: known commitments only; room_no treated as unconstrained label
_QUERY_TIMETABLE = (
    "SELECT st.id AS timetable_id, st.staff_id, st.subject_group_subject_id AS subject_id, "
    "cs.id AS class_section_id, st.day AS day_of_week, st.start_time, st.end_time, st.room_no "
    "FROM subject_timetable st "
    "LEFT JOIN class_sections cs ON st.class_id = cs.class_id AND st.section_id = cs.section_id "
    "ORDER BY st.id ASC"
)


class MySqlIreduAdapter(ErpSisAdapter):
    """Production-grade MySQL iredu ERP adapter enforcing security and governance."""

    def __init__(
        self,
        settings: MySqlErpSettings | None = None,
        executor: QueryExecutor | None = None,
    ) -> None:
        self._settings = settings or MySqlErpSettings()
        self._executor = executor
        self._last_successful_fetch: dict[str, datetime] = {}
        self._last_error: dict[str, str] = {}
        self._record_counts: dict[str, int] = {}

    def _verify_tenant_scope(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> str:
        """Enforce single-tenant binding and BLK-01 governance.

        Security model (Outcome B - Confirmed Single-Tenant Deployment):
        - The upstream mysql_iredu database is dedicated to exactly one institution.
        - E-7 explicitly binds one canonical tenant_id to this database instance
          via single_tenant_id.
        - The configured canonical tenant is allowed to access this dedicated database.
        - Mismatched, unapproved, or missing tenants FAIL CLOSED before issuing any queries.
        """
        if not tenant_id or not tenant_id.strip():
            raise ScopeUnresolvedError("BLK-01: Missing tenant_id. Access is strictly blocked.")

        clean_tenant = tenant_id.strip()

        # 1. Single-tenant deployment binding (Outcome B)
        single_tenant = self._settings.single_tenant_id.strip()
        if single_tenant:
            if clean_tenant == single_tenant:
                return (
                    scope_reference.strip()
                    if scope_reference and scope_reference.strip()
                    else f"dedicated:{self._settings.database}"
                )
            approved_scopes = self._settings.approved_tenant_scopes
            if not approved_scopes or clean_tenant not in approved_scopes:
                raise ScopeUnresolvedError(
                    f"BLK-01: Tenant mismatch. This dedicated mysql_iredu instance is bound "
                    f"exclusively to canonical tenant '{single_tenant}', not '{clean_tenant}'."
                )

        # 2. Approved mapping table or explicit scope fallback (Outcome C / legacy test fixture)
        resolved_scope = scope_reference or self._settings.approved_tenant_scopes.get(clean_tenant)
        if not resolved_scope or not resolved_scope.strip():
            raise ScopeUnresolvedError(
                f"BLK-01: Authoritative organization scope is unresolved for "
                f"tenant '{clean_tenant}'. "
                "Live MySQL context retrieval is strictly blocked pending scoping resolution."
            )
        return resolved_scope.strip()

    def _validate_read_only(self, query: str) -> None:
        """Verify that the query is strictly read-only and contains no mutation keywords."""
        if _MUTATION_PATTERN.search(query):
            raise ReadOnlyViolationError(
                f"Prohibited mutation detected in query: '{query[:50]}...'. "
                "The ERP adapter is strictly READ_ONLY."
            )
        clean = query.strip().upper()
        if not any(clean.startswith(prefix) for prefix in _ALLOWED_READ_PREFIXES):
            raise ReadOnlyViolationError(
                f"Arbitrary or non-read query disallowed: '{query[:50]}...'. "
                "The ERP adapter permits only predeclared read-only queries."
            )

    async def _execute(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        """Execute a read-only query using the configured executor or fail closed."""
        self._validate_read_only(query)

        if self._executor is not None:
            return await self._executor.execute_query(query, params)

        # In absence of an injected executor, check live connection configuration
        if not self._settings.enabled:
            raise ScopeUnresolvedError(
                "BLK-01: Live MySQL ERP context retrieval is disabled in configuration. "
                "Direct extraction remains blocked under BLK-01 governance."
            )

        if not self._settings.host or not self._settings.user:
            raise ErpConnectionError(
                "MySQL ERP connection parameters are incomplete or unconfigured."
            )

        try:
            import aiomysql
        except ImportError as exc:
            raise ErpConnectionError(
                "aiomysql driver is not installed. Live MySQL execution requires aiomysql."
            ) from exc

        try:
            conn = await aiomysql.connect(
                host=self._settings.host,
                port=self._settings.port,
                user=self._settings.user,
                password=self._settings.password.get_secret_value(),
                db=self._settings.database,
                connect_timeout=self._settings.timeout_seconds,
                autocommit=True,
            )
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(SESSION_INIT_READ_ONLY)
                    await cur.execute(query, params or ())
                    rows = await cur.fetchall()
                    return list(rows)
            finally:
                conn.close()
        except TimeoutError as exc:
            raise ErpTimeoutError(
                f"MySQL connection timed out after {self._settings.timeout_seconds}s"
            ) from exc
        except Exception as exc:
            err_type = type(exc).__name__
            if "Timeout" in err_type:
                raise ErpTimeoutError(f"MySQL query timed out: {exc}") from exc
            if "Permission" in err_type or "AccessDenied" in err_type:
                raise ErpPermissionDeniedError(f"MySQL permission denied: {exc}") from exc
            raise ErpConnectionError(f"MySQL connection failed: {exc}") from exc

    async def audit_database_user_grants(self) -> list[str]:
        """Safely audit the connected MySQL user grants without exposing secrets."""
        rows = await self._execute("SHOW GRANTS FOR CURRENT_USER()")
        grants: list[str] = []
        for r in rows:
            if isinstance(r, dict):
                grants.extend(str(v) for v in r.values())
            else:
                grants.append(str(r))
        return grants

    def _record_success(self, tenant_id: str, count_key: str, count_value: int) -> None:
        """Update freshness tracking metadata upon successful retrieval."""
        now = datetime.now(UTC)
        self._last_successful_fetch[tenant_id] = now
        self._last_error.pop(tenant_id, None)
        self._record_counts[count_key] = count_value

    def _record_failure(self, tenant_id: str, error_msg: str) -> None:
        """Update freshness tracking metadata upon retrieval failure."""
        self._last_error[tenant_id] = error_msg

    async def get_organization_context(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> ErpOrganizationContextDTO:
        """Fetch organization and active session metadata."""
        scope = self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            org_rows = await self._execute(_QUERY_ORGANIZATION)
            if not org_rows:
                raise EntityNotFoundError("No organization settings row found in sch_settings.")

            org_data = org_rows[0]
            session_id = str(org_data.get("session_id") or "1")

            # Look up session name
            session_rows = await self._execute(_QUERY_SESSIONS)
            session_name = "Academic Session"
            for s in session_rows:
                if str(s.get("id")) == session_id:
                    session_name = str(s.get("session") or session_name)
                    break

            result = ErpOrganizationContextDTO(
                tenant_id=tenant_id,
                institution_name=str(org_data.get("name") or "Institutional Academy"),
                institution_code=scope,
                active_session_id=session_id,
                active_session_name=session_name,
            )
            self._record_success(tenant_id, "organization", 1)
            return result
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_academic_sessions(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpAcademicSessionDTO]:
        """Fetch academic sessions."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_SESSIONS)
            sessions = [
                ErpAcademicSessionDTO(
                    session_id=str(r.get("id")),
                    session_name=str(r.get("session")),
                    is_active=bool(r.get("is_active") in (1, "1", "yes", True)),
                )
                for r in rows
            ]
            self._record_success(tenant_id, "sessions", len(sessions))
            return sessions
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_classes_and_sections(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpClassSectionDTO]:
        """Fetch class and section pairings."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_CLASSES_SECTIONS)
            class_sections = [
                ErpClassSectionDTO(
                    class_section_id=str(r.get("class_section_id")),
                    class_id=str(r.get("class_id")),
                    class_name=str(r.get("class_name")),
                    section_id=str(r.get("section_id")),
                    section_name=str(r.get("section_name")),
                )
                for r in rows
            ]
            self._record_success(tenant_id, "class_sections", len(class_sections))
            return class_sections
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_subjects(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpSubjectDTO]:
        """Fetch academic subjects."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_SUBJECTS)
            subjects = [
                ErpSubjectDTO(
                    subject_id=str(r.get("id")),
                    name=str(r.get("name")),
                    code=str(r.get("code")) if r.get("code") else None,
                    type=str(r.get("type", "theory")).lower(),
                )
                for r in rows
            ]
            self._record_success(tenant_id, "subjects", len(subjects))
            return subjects
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_staff_roster(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpStaffDTO]:
        """Fetch sanitized staff roster."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_STAFF_ROSTER)
            staff_list = [
                ErpStaffDTO(
                    staff_id=str(r.get("staff_id")),
                    opaque_ref=f"StaffRef_{r.get('staff_id')}",
                    name=str(r.get("name")),
                    surname=str(r.get("surname", "")),
                    qualification=str(r.get("qualification")) if r.get("qualification") else None,
                    work_exp=str(r.get("work_exp")) if r.get("work_exp") else None,
                    department_name=(
                        str(r.get("department_name")) if r.get("department_name") else None
                    ),
                    designation_name=(
                        str(r.get("designation")) if r.get("designation") else None
                    ),
                    is_active=bool(r.get("is_active") in (1, "1", True)),
                )
                for r in rows
            ]
            self._record_success(tenant_id, "staff", len(staff_list))
            return staff_list
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_student_headcounts(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpStudentHeadcountAggregateDTO]:
        """Fetch student headcounts per class-section (AGGREGATE ONLY)."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_STUDENT_HEADCOUNTS)
            headcounts = [
                ErpStudentHeadcountAggregateDTO(
                    class_section_id=str(r.get("class_section_id")),
                    class_name=str(r.get("class_name")),
                    section_name=str(r.get("section_name")),
                    enrolled_student_count=int(r.get("enrolled_count", 0)),
                    active_student_count=int(r.get("active_count", 0)),
                )
                for r in rows
            ]
            self._record_success(tenant_id, "student_headcounts", len(headcounts))
            return headcounts
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def get_timetable_commitments(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpTimetableCommitmentDTO]:
        """Fetch known recurring weekly timetable commitments."""
        self._verify_tenant_scope(tenant_id, scope_reference)
        try:
            rows = await self._execute(_QUERY_TIMETABLE)
            commitments = [
                ErpTimetableCommitmentDTO(
                    timetable_id=str(r.get("timetable_id")),
                    staff_id=str(r.get("staff_id")),
                    subject_id=str(r.get("subject_id")),
                    class_section_id=str(r.get("class_section_id")),
                    day_of_week=str(r.get("day_of_week")),
                    start_time=str(r.get("start_time")),
                    end_time=str(r.get("end_time")),
                    raw_room_label=str(r.get("room_no")) if r.get("room_no") else None,
                )
                for r in rows
            ]
            self._record_success(tenant_id, "timetable_commitments", len(commitments))
            return commitments
        except Exception as exc:
            self._record_failure(tenant_id, str(exc))
            raise

    async def check_freshness(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> ErpFreshnessMetadataDTO:
        """Inspect and return current freshness tracking metadata."""
        last_fetch = self._last_successful_fetch.get(tenant_id)
        last_err = self._last_error.get(tenant_id)
        now = datetime.now(UTC)

        if last_err:
            state = "DEGRADED"
        elif not last_fetch:
            state = "OFFLINE"
        else:
            age = (now - last_fetch).total_seconds()
            if age > self._settings.staleness_threshold_seconds:
                state = "STALE"
            else:
                state = "FRESH"

        return ErpFreshnessMetadataDTO(
            provider="mysql_iredu",
            tenant_id=tenant_id,
            last_successful_fetch=last_fetch,
            source_timestamp=last_fetch,
            staleness_state=state,
            last_error=last_err,
            record_counts=dict(self._record_counts),
        )


__all__ = ["MySqlIreduAdapter"]
