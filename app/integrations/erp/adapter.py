"""Provider-neutral ERP/SIS Adapter protocol and query executor abstractions.

Part of Phase 7B Real ERP/SIS Adapter.
"""

from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class QueryExecutor(Protocol):
    """Protocol for executing parameterized queries against an underlying database."""

    async def execute_query(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        """Execute a read-only parameterized query and return matching records."""
        ...


@runtime_checkable
class ErpSisAdapter(Protocol):
    """Protocol defining provider-neutral ERP/SIS domain context retrieval."""

    async def get_organization_context(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> ErpOrganizationContextDTO:
        """Fetch organization and active academic session context."""
        ...

    async def get_academic_sessions(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpAcademicSessionDTO]:
        """Fetch academic session records."""
        ...

    async def get_classes_and_sections(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpClassSectionDTO]:
        """Fetch class and section cohort pairings."""
        ...

    async def get_subjects(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpSubjectDTO]:
        """Fetch academic subjects catalog."""
        ...

    async def get_staff_roster(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpStaffDTO]:
        """Fetch sanitized staff roster."""
        ...

    async def get_student_headcounts(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpStudentHeadcountAggregateDTO]:
        """Fetch student headcounts per class-section (AGGREGATE ONLY)."""
        ...

    async def get_timetable_commitments(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> list[ErpTimetableCommitmentDTO]:
        """Fetch known recurring weekly timetable commitments."""
        ...

    async def check_freshness(
        self, tenant_id: str, scope_reference: str | None = None
    ) -> ErpFreshnessMetadataDTO:
        """Fetch data freshness tracking metadata."""
        ...


__all__ = [
    "ErpSisAdapter",
    "QueryExecutor",
]
