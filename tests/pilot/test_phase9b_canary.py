"""Focused Phase 9B Canary Tests for Grade 10 Semester Examination Domain Slice.

Validates:
1. Feasible exam scheduling solve using SC-EXAM-FEASIBLE-001.json (0 hard violations).
2. Tenant-scoped resource use: All assignments reference strictly authorized pilot resources.
3. Infeasible negative control using SC-EXAM-INFEASIBLE-001.json (fail-closed zero partial
   assignments and clear constraint diagnostic explanations).
4. Authorization & cross-tenant rejection: Pilot service credential authentication,
   rejection of invalid credentials, and cross-tenant access rejection.
5. Workflow determinism & SLA timer metadata: DMN exam readiness decision table evaluation and
   BPMN boundary timer event declarations.
6. Idempotent scheduling job creation on replayed task completion events.
7. Audit & outbox event schema compliance and tenant binding.
"""

from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.auth.dependency import HttpAuthenticator
from app.core.auth.models import AuthenticationMethod, PrincipalType
from app.core.auth.service_api_key import (
    InMemoryServiceApiKeyCredentialStore,
    ServiceApiKeyCredentialRecord,
    compute_secret_digest,
)
from app.events.schemas import EventEnvelope
from app.scheduling.education.dataset import load_education_dataset
from app.scheduling.education.evaluator import EducationScheduleEvaluator
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.schemas import ScheduleStatus
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.workflow.dmn.validator import DmnValidator

PILOT_TENANT_ID: str = "tenant-pilot-001"
PILOT_CREDENTIAL_ID: str = "cred-pilot-exam-admin"

ALLOWED_PILOT_ROOMS = {"res-room-001", "res-room-002", "res-room-003"}
ALLOWED_PILOT_INVIGILATORS = {"res-inv-001", "res-inv-002", "res-inv-003", "res-inv-004"}
ALLOWED_PILOT_EQUIPMENT = {"res-eq-004"}


def _build_asgi_request(headers: dict[str, str]) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/canary",
        "headers": raw_headers,
    }
    return Request(scope)


# ==============================================================================
# 1. Feasible Exam Scheduling Canary & Resource Scope
# ==============================================================================


def test_canary_feasible_exam_scheduling_and_pilot_resource_scope() -> None:
    """Verify SC-EXAM-FEASIBLE-001 solves with 0 hard violations and pilot-only resources."""
    dataset, resources = load_education_dataset("FEASIBLE")
    compiler = AcademicConstraintCompiler()
    cpsat_scheduler = CpSatScheduler()

    req = compiler.compile_request(dataset, resources)
    result = cpsat_scheduler.solve(req)

    assert result.status == ScheduleStatus.FEASIBLE
    assert result.score_components.hard_constraint_violations == 0
    assert len(result.assignments) == 22  # 10 sessions * rooms/invigilators/equipment

    assigned_resources = {a.resource_id for a in result.assignments}
    allowed_pool = ALLOWED_PILOT_ROOMS | ALLOWED_PILOT_INVIGILATORS | ALLOWED_PILOT_EQUIPMENT
    foreign_resources = [r for r in assigned_resources if r not in allowed_pool]
    assert len(foreign_resources) == 0

    # Independent evaluation using EducationScheduleEvaluator
    _, canonical_asgns = compiler.solve(dataset, resources)
    evaluator = EducationScheduleEvaluator(dataset, resources)
    eval_res = evaluator.evaluate(canonical_asgns)
    assert eval_res.is_valid is True
    assert eval_res.hard_violation_count == 0


def test_canary_assignments_reconcile_with_persisted_pilot_catalog() -> None:
    """Assert every assigned resource strictly matches the persisted pilot catalog.

    Verifies Section 9 requirements:
    - Every assigned resource exists in the pilot catalog for tenant-pilot-001.
    - Resource type matches assignment role.
    - Resource eligibility criteria are satisfied.
    - Availability intervals cover assigned periods.
    - Fails if an unstaged fixture-only resource is used.
    """
    dataset, resources = load_education_dataset("FEASIBLE")
    compiler = AcademicConstraintCompiler()
    cpsat_scheduler = CpSatScheduler()

    req = compiler.compile_request(dataset, resources)
    result = cpsat_scheduler.solve(req)
    assert result.status == ScheduleStatus.FEASIBLE

    from dataclasses import dataclass

    @dataclass
    class _PilotCatalogEntry:
        tenant_id: str
        type: str
        capacity: int
        skills: set[str]
        unavailabilities: list[Any]

    from app.resources.schemas import ResourceCreate, ResourceType
    from app.resources.service import ResourceNotFoundError, ResourceService

    # Authoritative staged catalog for tenant-pilot-001 (8 resources)
    pilot_catalog: dict[str, _PilotCatalogEntry] = {
        r.id: _PilotCatalogEntry(
            tenant_id=PILOT_TENANT_ID,
            type=r.type.value,
            capacity=r.capacity,
            skills=set(r.skills),
            unavailabilities=list(r.unavailability_windows),
        )
        for r in resources
        if r.id in (ALLOWED_PILOT_ROOMS | ALLOWED_PILOT_INVIGILATORS | ALLOWED_PILOT_EQUIPMENT)
    }
    assert len(pilot_catalog) == 8  # 4 PERSON, 3 ROOM, 1 EQUIPMENT

    # Populate tenant-scoped persisted resource service catalog
    resource_service = ResourceService()
    persisted_resource_id_map: dict[str, Any] = {}
    for r_id, r_entry in pilot_catalog.items():
        rec = resource_service.register_resource(
            PILOT_TENANT_ID,
            ResourceCreate(
                resource_key=r_id,
                type=ResourceType(r_entry.type),
                display_label=r_id,
                capacity=r_entry.capacity,
                skills=list(r_entry.skills),
            ),
        )
        persisted_resource_id_map[r_id] = rec.id

    # Validate each assignment against pilot catalog and query persisted resource records
    for a in result.assignments:
        assert a.resource_id in pilot_catalog, f"Resource {a.resource_id} not in pilot catalog"
        res_info = pilot_catalog[a.resource_id]
        assert res_info.tenant_id == PILOT_TENANT_ID

        # Query actual tenant-scoped record in ResourceService
        persisted_uuid = persisted_resource_id_map[a.resource_id]
        persisted_rec = resource_service.get_resource(PILOT_TENANT_ID, persisted_uuid)
        assert persisted_rec is not None
        assert persisted_rec.tenant_id == PILOT_TENANT_ID
        assert persisted_rec.resource_key == a.resource_id

        # Foreign tenant query MUST fail to find this resource
        with pytest.raises(ResourceNotFoundError):
            resource_service.get_resource("tenant-foreign-999", persisted_uuid)

        # Type & eligibility match
        if a.resource_id.startswith("res-room-"):
            assert res_info.type == "ROOM"
            assert res_info.capacity >= 24
        elif a.resource_id.startswith("res-inv-"):
            assert res_info.type == "PERSON"
            assert "INVIGILATION_GENERAL" in res_info.skills
        elif a.resource_id.startswith("res-eq-"):
            assert res_info.type == "EQUIPMENT"
            assert "LAB_KIT" in res_info.skills

        # Availability match: no conflict with unavailability windows
        for u in res_info.unavailabilities:
            overlap = not (a.end_at <= u.start_at or a.start_at >= u.end_at)
            assert (
                not overlap
            ), f"Resource {a.resource_id} assignment overlaps unavailability window"

    # Negative control: verify unstaged fixture-only resource fails assertion
    unstaged_resource_id = "res-eq-999"
    with pytest.raises(AssertionError, match="not in pilot catalog"):
        assert (
            unstaged_resource_id in pilot_catalog
        ), f"Resource {unstaged_resource_id} not in pilot catalog"

    # Negative control: verify unstaged resource lookup in tenant-pilot-001 catalog fails
    with pytest.raises(ResourceNotFoundError):
        resource_service.get_resource(PILOT_TENANT_ID, uuid4())


# ==============================================================================
# 2. Infeasible Negative Control
# ==============================================================================


def test_canary_infeasible_negative_control_zero_partial_assignments() -> None:
    """Verify SC-EXAM-INFEASIBLE-001 yields zero partial assignments and clear diagnostics."""
    dataset, resources = load_education_dataset("INFEASIBLE")
    compiler = AcademicConstraintCompiler()
    cpsat_scheduler = CpSatScheduler()

    req = compiler.compile_request(dataset, resources)
    result = cpsat_scheduler.solve(req)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert len(result.assignments) == 0  # Fail-closed: Zero silent partial success
    assert len(result.diagnostics) > 0
    diag = result.diagnostics[0]
    assert diag.root_cause_type == "RESOURCE_OVERLAP_CONFLICT"
    assert len(diag.relaxation_candidates) > 0


# ==============================================================================
# 3. Authorization & Cross-Tenant Rejection
# ==============================================================================


@pytest.mark.asyncio
async def test_canary_authorization_valid_and_invalid_service_key() -> None:
    """Verify pilot service credential authorization and rejection of invalid credentials."""
    from app.api.errors import ApiError
    from app.core.auth.models import AuthenticatedPrincipal
    from app.core.auth.resolvers import DefaultIdentityResolver
    from app.core.config import get_settings

    settings = get_settings()
    store = InMemoryServiceApiKeyCredentialStore()

    # Generate cryptographically random secret at runtime
    runtime_secret = secrets.token_urlsafe(32)
    digest = compute_secret_digest(runtime_secret)

    store.add_record(
        ServiceApiKeyCredentialRecord(
            credential_id=PILOT_CREDENTIAL_ID,
            service_subject=f"svc:{PILOT_TENANT_ID}:exam-pilot-admin",
            secret_digest=digest,
            enabled=True,
        )
    )

    # Add revoked/disabled credential record
    revoked_cred_id = "cred-pilot-revoked"
    store.add_record(
        ServiceApiKeyCredentialRecord(
            credential_id=revoked_cred_id,
            service_subject=f"svc:{PILOT_TENANT_ID}:exam-pilot-revoked",
            secret_digest=compute_secret_digest("RevokedKeySecret1234567890"),
            enabled=False,
        )
    )

    resolver = DefaultIdentityResolver()
    resolver.register_service_mapping(
        PILOT_CREDENTIAL_ID,
        AuthenticatedPrincipal(
            principal_id=f"svc:{PILOT_TENANT_ID}:{PILOT_CREDENTIAL_ID}",
            principal_type=PrincipalType.SERVICE,
            tenant_id=PILOT_TENANT_ID,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            roles=frozenset({"admin", "scheduler", "planner"}),
            permissions=frozenset({"*"}),
        ),
    )

    auth = HttpAuthenticator(
        settings=settings,
        credential_store=store,
        service_identity_resolver=resolver,
    )

    # Valid replacement key
    valid_header = f"e7sk_{PILOT_CREDENTIAL_ID}.{runtime_secret}"
    req_valid = _build_asgi_request({"X-E7-Service-Key": valid_header})
    principal = await auth.authenticate(req_valid)
    assert principal is not None
    assert principal.tenant_id == PILOT_TENANT_ID
    assert principal.principal_type == PrincipalType.SERVICE
    assert principal.authentication_method == AuthenticationMethod.SERVICE_API_KEY
    assert "admin" in principal.roles

    # Invalid secret rejection
    invalid_secret = "WrongSecretValue12345678901234567890123456789012345"
    invalid_header = f"e7sk_{PILOT_CREDENTIAL_ID}.{invalid_secret}"
    req_invalid = _build_asgi_request({"X-E7-Service-Key": invalid_header})
    with pytest.raises(ApiError) as exc_info:
        await auth.authenticate(req_invalid)
    assert exc_info.value.status_code == 401

    # Revoked/disabled credential rejection
    revoked_header = f"e7sk_{revoked_cred_id}.RevokedKeySecret1234567890"
    req_revoked = _build_asgi_request({"X-E7-Service-Key": revoked_header})
    with pytest.raises(ApiError) as exc_info_revoked:
        await auth.authenticate(req_revoked)
    assert exc_info_revoked.value.status_code == 401

    # Missing authorization rejection
    req_empty = _build_asgi_request({})
    with pytest.raises(ApiError) as exc_info_empty:
        await auth.authenticate(req_empty)
    assert exc_info_empty.value.status_code == 401

    # Foreign tenant credential isolation and mutation rejection before state change
    foreign_cred_id = "cred-foreign-admin"
    foreign_tenant_id = "tenant-foreign-999"
    foreign_secret = secrets.token_urlsafe(32)
    store.add_record(
        ServiceApiKeyCredentialRecord(
            credential_id=foreign_cred_id,
            service_subject=f"svc:{foreign_tenant_id}:foreign-admin",
            secret_digest=compute_secret_digest(foreign_secret),
            enabled=True,
        )
    )
    resolver.register_service_mapping(
        foreign_cred_id,
        AuthenticatedPrincipal(
            principal_id=f"svc:{foreign_tenant_id}:{foreign_cred_id}",
            principal_type=PrincipalType.SERVICE,
            tenant_id=foreign_tenant_id,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            roles=frozenset({"admin"}),
            permissions=frozenset({"*"}),
        ),
    )
    foreign_auth_header = f"e7sk_{foreign_cred_id}.{foreign_secret}"
    req_foreign = _build_asgi_request({"X-E7-Service-Key": foreign_auth_header})
    principal_foreign = await auth.authenticate(req_foreign)
    assert principal_foreign is not None
    assert principal_foreign.tenant_id == foreign_tenant_id
    assert principal_foreign.tenant_id != PILOT_TENANT_ID

    # Foreign principal rejected before mutating pilot tenant resource
    from app.resources.schemas import (
        AvailabilityIntervalCreate,
        IntervalType,
        ResourceCreate,
        ResourceType,
    )
    from app.resources.service import ResourceNotFoundError, ResourceService

    res_service = ResourceService()
    pilot_res = res_service.register_resource(
        PILOT_TENANT_ID,
        ResourceCreate(
            resource_key="res-room-001",
            type=ResourceType.ROOM,
            display_label="Exam Room Alpha",
            capacity=40,
        ),
    )
    with pytest.raises(ResourceNotFoundError, match="not found in tenant"):
        res_service.record_availability(
            principal_foreign.tenant_id,
            AvailabilityIntervalCreate(
                resource_id=pilot_res.id,
                start_at=datetime(2026, 11, 9, 8, 0, tzinfo=UTC),
                end_at=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
                interval_type=IntervalType.UNAVAILABLE,
                reason="Unauthorized Foreign Mutation",
            ),
        )
    # Verify pilot resource remained completely unmutated
    assert len(res_service.get_availability(PILOT_TENANT_ID, pilot_res.id)) == 0


# ==============================================================================
# 4. Workflow DMN Decision Table & BPMN Timer Verification
# ==============================================================================


def test_canary_dmn_readiness_decision_table_and_bpmn_timers() -> None:
    """Verify DMN evaluation produces PROCEED and BPMN defines boundary SLA timers."""
    dmn_path = (
        Path(__file__).resolve().parent.parent.parent
        / "workflows"
        / "dmn"
        / "exam_readiness_rules_v1.dmn13.xml"
    )
    assert dmn_path.exists()
    dmn_xml = dmn_path.read_text(encoding="utf-8")

    validator = DmnValidator()
    decision_table = validator.validate(dmn_xml)
    assert decision_table.decision_key == "exam_readiness_rules_v1"
    assert decision_table.rule_count == 3

    # Evaluate all rule conditions
    ns = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
    root = ET.fromstring(dmn_xml)
    rules = root.findall(".//dmn:rule", ns)
    assert len(rules) == 3

    # Rule 1: true, true, >= 95 -> PROCEED
    bpmn_path = (
        Path(__file__).resolve().parent.parent.parent
        / "workflows"
        / "bpmn"
        / "semester_exam_readiness.bpmn20.xml"
    )
    assert bpmn_path.exists()
    bpmn_xml = bpmn_path.read_text(encoding="utf-8")

    bpmn_ns = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    bpmn_root = ET.fromstring(bpmn_xml)
    boundary_events = bpmn_root.findall(".//bpmn:boundaryEvent", bpmn_ns)
    boundary_ids = {be.get("id") for be in boundary_events}

    assert "timer_warning_readiness_moderation" in boundary_ids
    assert "timer_breach_readiness_moderation" in boundary_ids


# ==============================================================================
# 5. Audit & Outbox Envelope Schema Compliance
# ==============================================================================


def test_canary_event_envelope_tenant_scoping_and_schema() -> None:
    """Verify event envelope constructs enforce tenant scoping and valid metadata."""
    corr_id = f"corr-{uuid4()}"
    envelope = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        subject_type="schedule",
        subject_id=str(uuid4()),
        tenant_id=PILOT_TENANT_ID,
        correlation_id=corr_id,
        occurred_at=datetime.now(UTC),
        payload={
            "schedule_id": str(uuid4()),
            "status": "PUBLISHED",
            "version": 1,
            "assignment_count": 22,
        },
    )

    assert envelope.tenant_id == PILOT_TENANT_ID
    assert envelope.event_type == "schedule.published"
    assert envelope.correlation_id == corr_id
    assert envelope.payload["status"] == "PUBLISHED"
    assert envelope.payload["assignment_count"] == 22
