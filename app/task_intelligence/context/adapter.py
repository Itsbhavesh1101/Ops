"""Context source adapter contract and synthetic implementation (Phase 2B).

Enforces:
- READ ONLY
- Parameterized approved retrieval methods
- No arbitrary SQL API or SELECT *
- No direct LLM database connection
- BLK-01: MYSQL_PRODUCTION_CONTEXT_RETRIEVAL = DISABLED_BY_GOVERNANCE
"""

from typing import Any, Protocol, runtime_checkable

from app.task_intelligence.constants import MYSQL_PRODUCTION_CONTEXT_RETRIEVAL


class AdapterAccessDeniedError(Exception):
    """Raised when an unauthorized or cross-tenant context access is attempted."""


class DatabaseAccessBlockedError(Exception):
    """Raised when an unapproved live database query is attempted under BLK-01."""


@runtime_checkable
class ContextSourceAdapter(Protocol):
    """Read-only parameterized adapter contract for Task Intelligence context."""

    async def fetch_organization_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_academic_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_subject_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_staff_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_commitment_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_calendar_records(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_cohort_student_counts(self, tenant_id: str) -> dict[str, int]:
        """Aggregate student headcounts only (FR-TI-01 / FR-TI-04).

        Zero individual student records.
        """
        ...


class SyntheticContextAdapter:
    """In-memory synthetic context adapter satisfying ContextSourceAdapter.

    Uses deterministic synthetic records to simulate ERP context safely
    without connecting to production databases.
    """

    def __init__(self, tenant_id: str = "tenant-synth-001") -> None:
        self._authorized_tenant_id = tenant_id

    def _verify_tenant(self, tenant_id: str) -> None:
        if not tenant_id or tenant_id != self._authorized_tenant_id:
            raise AdapterAccessDeniedError(
                f"Unauthorized tenant context request for '{tenant_id}'. "
                f"Expected authorized tenant '{self._authorized_tenant_id}'."
            )

    async def fetch_organization_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        return [
            {
                "id": 1,
                "department_name": "Examination Operations",
                "designation": "Exam Superintendent",
            },
            {
                "id": 2,
                "department_name": "Science & Mathematics",
                "designation": "Senior Faculty",
            },
            {
                "id": 3,
                "department_name": "Administrative Cell",
                "designation": "Operations Lead",
            },
        ]

    async def fetch_academic_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        return [
            {"id": 101, "session": "2026-2027", "class": "Grade 10", "section": "A"},
            {"id": 102, "session": "2026-2027", "class": "Grade 10", "section": "B"},
        ]

    async def fetch_subject_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        return [
            {
                "id": 201,
                "name": "Mathematics",
                "type": "Theory",
                "code": "MATH10",
                "group_name": "Core STEM",
                "group_description": "Grade 10 core analytical disciplines",
                "assignment_description": "Semester examination subject bundle",
            },
            {
                "id": 202,
                "name": "General Science",
                "type": "Practical",
                "code": "SCI10",
                "group_name": "Core STEM",
                "group_description": "Grade 10 laboratory science disciplines",
                "assignment_description": "Semester examination subject bundle",
            },
        ]

    async def fetch_staff_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        # Note: Raw DB PKs remain internal to adapter and are sanitized by ContextBuilder
        return [
            {
                "raw_id": 401,
                "name": "Sunita",
                "surname": "Sharma",
                "qualification": "M.Sc. Mathematics, B.Ed.",
                "work_exp": "12 years secondary examination administration",
            },
            {
                "raw_id": 402,
                "name": "Rajesh",
                "surname": "Verma",
                "qualification": "Ph.D. Physics",
                "work_exp": "15 years science laboratory oversight",
            },
            {
                "raw_id": 403,
                "name": "Anita",
                "surname": "Deshmukh",
                "qualification": "M.A. English, B.Ed.",
                "work_exp": "8 years invigilation coordination",
            },
        ]

    async def fetch_commitment_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        return [
            {
                "raw_id": 501,
                "day": "Monday",
                "time_from": "09:00",
                "time_to": "10:30",
                "start_time": "09:00:00",
                "end_time": "10:30:00",
            },
            {
                "raw_id": 502,
                "day": "Wednesday",
                "time_from": "11:00",
                "time_to": "12:30",
                "start_time": "11:00:00",
                "end_time": "12:30:00",
            },
        ]

    async def fetch_calendar_records(self, tenant_id: str) -> list[dict[str, Any]]:
        self._verify_tenant(tenant_id)
        return [
            {
                "raw_id": 601,
                "from_date": "2026-11-01T00:00:00+05:30",
                "to_date": "2026-11-01T23:59:59+05:30",
                "description": "Institutional Non-Working Day",
                "event_title": "Semester Readiness Briefing",
                "event_description": "Preparatory logistics alignment for upcoming examinations",
                "event_start_date": "2026-11-05T10:00:00+05:30",
                "event_end_date": "2026-11-05T12:00:00+05:30",
                "event_type": "operational",
            }
        ]

    async def fetch_cohort_student_counts(self, tenant_id: str) -> dict[str, int]:
        self._verify_tenant(tenant_id)
        # Aggregation only: Mathematical count of enrolled students per cohort
        # Zero student names, IDs, or individual rows
        return {
            "Grade 10 - Section A": 42,
            "Grade 10 - Section B": 40,
        }


class ProductionMySQLAdapter:
    """Production MySQL adapter stub enforcing BLK-01 governance gate.

    Prevents any unauthorized live connection attempts to the production MySQL database.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        raise DatabaseAccessBlockedError(
            f"Direct production MySQL database access is blocked by governance under BLK-01. "
            f"MYSQL_PRODUCTION_CONTEXT_RETRIEVAL = '{MYSQL_PRODUCTION_CONTEXT_RETRIEVAL}'."
        )
