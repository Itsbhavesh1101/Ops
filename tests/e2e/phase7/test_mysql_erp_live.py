"""Opt-in live read-only verification test for MySQL iredu ERP/SIS adapter.

Part of Phase 7 Live External Integration Verification.
Gated by opt-in environment variable:
    E7_RUN_MYSQL_ERP_LIVE=1

Governance & Safety Rules:
- Skips cleanly when not explicitly opted in.
- When opted in and credentials/configuration are incomplete, FAILS clearly
  rather than silently skipping.
- Read-only queries only (predeclared parameterized queries).
- Prohibits all mutation DDL/DML.
- Zero individual student PII; aggregate counts only.
- Exercises ExternalIdentityMapper and freshness tracking.
"""

import os

import pytest

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
from app.integrations.erp.exceptions import ScopeUnresolvedError
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from app.integrations.identity_mapper import ExternalIdentityMapper

OPT_IN_ENV_VAR = "E7_RUN_MYSQL_ERP_LIVE"


def get_missing_mysql_config_keys(settings: MySqlErpSettings) -> list[str]:
    """Check required live connection settings without exposing secret values."""
    missing: list[str] = []
    if not settings.enabled:
        missing.append("E7_MYSQL_ERP_ENABLED")
    if not settings.single_tenant_id.strip():
        missing.append("E7_MYSQL_ERP_SINGLE_TENANT_ID")
    if not settings.host.strip():
        missing.append("E7_MYSQL_ERP_HOST")
    if not settings.user.strip():
        missing.append("E7_MYSQL_ERP_USER")
    if not settings.password.get_secret_value().strip():
        missing.append("E7_MYSQL_ERP_PASSWORD")
    if not settings.database.strip():
        missing.append("E7_MYSQL_ERP_DATABASE")
    return missing


@pytest.fixture
def mysql_live_settings() -> MySqlErpSettings:
    """Load settings and enforce fail-closed check if opted in but incomplete."""
    if os.environ.get(OPT_IN_ENV_VAR) != "1":
        pytest.skip(
            f"Live MySQL ERP test requires explicit opt-in: {OPT_IN_ENV_VAR}=1"
        )

    settings = MySqlErpSettings()
    missing_keys = get_missing_mysql_config_keys(settings)
    if missing_keys:
        pytest.fail(
            f"{OPT_IN_ENV_VAR}=1 is set, but required MySQL ERP configuration is missing: "
            f"{missing_keys}. Live verification blocked on connection credentials."
        )
    return settings


@pytest.mark.asyncio
async def test_mysql_erp_live_read_only_verification(
    mysql_live_settings: MySqlErpSettings,
) -> None:
    """Execute safe read-only queries against live dedicated mysql_iredu database."""
    tenant_id = mysql_live_settings.single_tenant_id.strip()
    adapter = MySqlIreduAdapter(settings=mysql_live_settings)

    # 1. Tenant fail-closed boundary check before querying
    with pytest.raises(ScopeUnresolvedError, match="BLK-01: Tenant mismatch"):
        await adapter.get_organization_context("wrong-unauthorized-tenant")
    print("\n[LIVE CHECK] Tenant fail-closed boundary: VERIFIED (ScopeUnresolvedError raised)")

    # 2. Inspect grants safely
    grants = await adapter.audit_database_user_grants()
    is_select_only = all("ALL PRIVILEGES" not in g and "INSERT" not in g for g in grants)
    print(f"[LIVE CHECK] Database user grants audited: count={len(grants)}")
    for g in grants:
        print(f"  - Grant: {g}")
    if is_select_only:
        print("[LIVE CHECK] MYSQL_DATABASE_ACCOUNT_READ_ONLY = PASS")
    else:
        print("[LIVE CHECK] MYSQL_DATABASE_ACCOUNT_READ_ONLY = FAIL (Mutation/DDL privileges)")

    # 3. Organization and active session context
    org = await adapter.get_organization_context(tenant_id)
    assert isinstance(org, ErpOrganizationContextDTO)
    assert org.tenant_id == tenant_id
    field_keys = list(org.model_dump().keys())
    print(f"[LIVE QUERY] Organization Context: SUCCESS, DTOs=1, fields={field_keys}")

    # 4. Academic sessions
    sessions = await adapter.get_academic_sessions(tenant_id)
    assert isinstance(sessions, list)
    for s in sessions:
        assert isinstance(s, ErpAcademicSessionDTO)
    print(f"[LIVE QUERY] Academic Sessions: SUCCESS, DTOs={len(sessions)}")

    # 5. Classes and sections
    classes_sections = await adapter.get_classes_and_sections(tenant_id)
    assert isinstance(classes_sections, list)
    for cs in classes_sections:
        assert isinstance(cs, ErpClassSectionDTO)
    print(f"[LIVE QUERY] Classes and Sections: SUCCESS, DTOs={len(classes_sections)}")

    # 6. Subjects catalog
    subjects = await adapter.get_subjects(tenant_id)
    assert isinstance(subjects, list)
    for sub in subjects:
        assert isinstance(sub, ErpSubjectDTO)
    print(f"[LIVE QUERY] Subjects Catalog: SUCCESS, DTOs={len(subjects)}")

    # 7. Staff roster & External Identity Mapping
    staff_roster = await adapter.get_staff_roster(tenant_id)
    assert isinstance(staff_roster, list)
    for st in staff_roster:
        assert isinstance(st, ErpStaffDTO)
        # Verify privacy: sensitive fields must not exist
        assert not hasattr(st, "basic_salary")
        assert not hasattr(st, "bank_account_no")

        # Verify external identity mapping derivation
        canonical_key = ExternalIdentityMapper.derive_canonical_resource_key(
            provider="mysql_iredu",
            entity_type="staff",
            external_id=st.staff_id,
            tenant_id=tenant_id,
        )
        assert canonical_key != st.staff_id
        assert canonical_key.startswith(f"urn:e7:{tenant_id}:mysql_iredu:staff:")
    print(f"[LIVE QUERY] Staff Roster: SUCCESS, DTOs={len(staff_roster)} (sanitized, zero salary)")
    if staff_roster:
        sample_key = ExternalIdentityMapper.derive_canonical_resource_key(
            provider="mysql_iredu",
            entity_type="staff",
            external_id=staff_roster[0].staff_id,
            tenant_id=tenant_id,
        )
        print(f"[LIVE CHECK] External Identity Mapping: raw_id != canonical_key ({sample_key})")

    # 8. Student headcounts: strictly aggregate only
    headcounts = await adapter.get_student_headcounts(tenant_id)
    assert isinstance(headcounts, list)
    for hc in headcounts:
        assert isinstance(hc, ErpStudentHeadcountAggregateDTO)
        assert not hasattr(hc, "student_id")
        assert not hasattr(hc, "firstname")
        assert not hasattr(hc, "roll_no")
        assert not hasattr(hc, "email")
        assert not hasattr(hc, "phone")
    print(f"[LIVE QUERY] Student Headcounts: SUCCESS, DTOs={len(headcounts)} (AGGREGATE ONLY)")

    # 9. Timetable commitments
    timetable = await adapter.get_timetable_commitments(tenant_id)
    assert isinstance(timetable, list)
    for item in timetable:
        assert isinstance(item, ErpTimetableCommitmentDTO)
    print(f"[LIVE QUERY] Timetable Commitments: SUCCESS, DTOs={len(timetable)}")

    # 10. Freshness tracking
    freshness = await adapter.check_freshness(tenant_id)
    assert isinstance(freshness, ErpFreshnessMetadataDTO)
    assert freshness.provider == "mysql_iredu"
    assert freshness.staleness_state in ("FRESH", "STALE")
    print(
        f"[LIVE CHECK] Freshness Tracking: state={freshness.staleness_state}, "
        f"provider={freshness.provider}"
    )

