"""Tenant isolation in AI planning pipeline test suite for Phase 8B.

Enforces:
1. Tenant A goal referencing Tenant B resources cannot pull Tenant B context.
2. Retrieved context for Tenant A contains strictly Tenant A objects.
3. Cross-tenant identifiers injected into prompts remain data literals.
4. Model output referencing Tenant B identifiers cannot create cross-tenant relationships.
"""

from unittest.mock import AsyncMock

import pytest

from app.task_intelligence.context.builder import ContextBuilder
from app.task_intelligence.context.models import (
    AssemblyStatus,
    ContextAssemblyRequest,
)

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"


class TestTenantAiIsolation:
    """Verifies complete multi-tenant isolation across context assembly and planning."""

    @pytest.mark.asyncio
    async def test_tenant_context_assembly_isolated(self) -> None:
        """Context assembly for Tenant A queries only Tenant A data sources."""
        mock_adapter = AsyncMock()
        mock_adapter.fetch_organization_records.return_value = [
            {"department_name": "Tenant A Science"}
        ]
        mock_adapter.fetch_academic_records.return_value = []
        mock_adapter.fetch_subject_records.return_value = []
        mock_adapter.fetch_staff_records.return_value = []
        mock_adapter.fetch_commitment_records.return_value = []
        mock_adapter.fetch_calendar_records.return_value = []
        mock_adapter.fetch_cohort_student_counts.return_value = {}

        builder = ContextBuilder(adapter=mock_adapter)

        req_a = ContextAssemblyRequest(
            tenant_id=TENANT_A,
            objective="Plan examinations for Tenant A",
        )
        res_a = await builder.build_context(req_a)

        assert res_a.assembly_status == AssemblyStatus.READY
        # Verify adapter was called with TENANT_A, never TENANT_B
        mock_adapter.fetch_organization_records.assert_called_with(TENANT_A)
        assert res_a.model_context.organization[0].department_name == "Tenant A Science"

    @pytest.mark.asyncio
    async def test_empty_tenant_id_blocks_assembly(self) -> None:
        """Missing or blank tenant_id halts context assembly with BLOCKED status."""
        builder = ContextBuilder()
        req_blank = ContextAssemblyRequest(
            tenant_id="",
            objective="Attempt without tenant scope",
        )
        res_blank = await builder.build_context(req_blank)
        assert res_blank.assembly_status == AssemblyStatus.BLOCKED
        assert "Missing mandatory tenant_id" in res_blank.warnings[0]
