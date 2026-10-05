"""Canonical Context Builder implementation (Phase 2B).

Conforms to E7-CTR-CB-001:
- Provider-neutral context assembly and data minimization
- Default deny: only the 28 PLANNER_CONTEXT_ALLOWED fields in model_context
- Students strictly AGGREGATE_ONLY (no individual student rows)
- Staff mapped to opaque reference tokens (no raw upstream primary keys)
- Distinct separation: model_context vs validation_context
- Exact assembly statuses: READY, DEGRADED, BLOCKED, REQUIRES_CLARIFICATION
"""

from typing import Any

from app.task_intelligence.context.adapter import (
    AdapterAccessDeniedError,
    ContextSourceAdapter,
    DatabaseAccessBlockedError,
    SyntheticContextAdapter,
)
from app.task_intelligence.context.allowlist import (
    generate_opaque_staff_token,
    sanitize_staff_display_name,
)
from app.task_intelligence.context.models import (
    AssemblyStatus,
    ContextAssemblyRequest,
    ContextAssemblyResult,
    ContextCategory,
    ModelContext,
    ModelVisibleAcademic,
    ModelVisibleCalendar,
    ModelVisibleCommitment,
    ModelVisibleOrganization,
    ModelVisibleStaff,
    ModelVisibleSubject,
    OmissionRecord,
    ProvenanceRecord,
    ValidationContext,
)


class ContextBuilder:
    """Provider-neutral, privacy-safe Context Builder engine."""

    def __init__(self, adapter: ContextSourceAdapter | None = None) -> None:
        self._adapter = adapter or SyntheticContextAdapter()

    async def build_context(self, request: ContextAssemblyRequest) -> ContextAssemblyResult:
        """Assemble safe model context and validation context for a goal.

        Args:
            request: Validated context assembly request parameters.

        Returns:
            ContextAssemblyResult with strict model vs validation isolation.
        """
        # 1. Organization Scope Verification (BLK-01)
        if not request.tenant_id or not request.tenant_id.strip():
            return ContextAssemblyResult(
                assembly_status=AssemblyStatus.BLOCKED,
                warnings=["Missing mandatory tenant_id organization scope."],
                unresolved_questions=["Tenant scope is unverified; context assembly halted."],
            )

        model_ctx = ModelContext()
        validation_ctx = ValidationContext()
        provenance: list[ProvenanceRecord] = []
        omissions: list[OmissionRecord] = []
        warnings: list[str] = []
        unresolved: list[str] = []
        aggregates: dict[str, Any] = {}

        try:
            # Category 1: organization_context
            if ContextCategory.ORGANIZATION_CONTEXT in request.requested_categories:
                org_records = await self._adapter.fetch_organization_records(request.tenant_id)
                for rec in org_records:
                    model_ctx.organization.append(
                        ModelVisibleOrganization(
                            department_name=rec.get("department_name"),
                            designation=rec.get("designation"),
                        )
                    )
                    # Omit raw upstream id from model context
                    if "id" in rec:
                        omissions.append(
                            OmissionRecord(
                                field_path=f"department.id[{rec['id']}]",
                                reason="Raw primary key omitted; restricted to validation_context",
                                category=ContextCategory.ORGANIZATION_CONTEXT,
                            )
                        )
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="department/staff_designation",
                        record_count=len(org_records),
                    )
                )

            # Category 2: academic_context
            if ContextCategory.ACADEMIC_CONTEXT in request.requested_categories:
                academic_records = await self._adapter.fetch_academic_records(request.tenant_id)
                for rec in academic_records:
                    model_ctx.academic.append(
                        ModelVisibleAcademic(
                            session=rec.get("session"),
                            grade_level=rec.get("class"),
                            section=rec.get("section"),
                        )
                    )
                    if "id" in rec:
                        omissions.append(
                            OmissionRecord(
                                field_path=f"classes.id[{rec['id']}]",
                                reason="Raw database PK omitted from model context",
                                category=ContextCategory.ACADEMIC_CONTEXT,
                            )
                        )
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="classes/sections/sessions",
                        record_count=len(academic_records),
                    )
                )

            # Category 3: subject_context
            if ContextCategory.SUBJECT_CONTEXT in request.requested_categories:
                subject_records = await self._adapter.fetch_subject_records(request.tenant_id)
                for rec in subject_records:
                    model_ctx.subjects.append(
                        ModelVisibleSubject(
                            subject_name=rec.get("name"),
                            subject_type=rec.get("type"),
                            subject_code=rec.get("code"),
                            subject_group_name=rec.get("group_name"),
                            subject_group_description=rec.get("group_description"),
                            curricular_assignment_description=rec.get("assignment_description"),
                        )
                    )
                    if "id" in rec:
                        omissions.append(
                            OmissionRecord(
                                field_path=f"subjects.id[{rec['id']}]",
                                reason="Raw database PK omitted from model context",
                                category=ContextCategory.SUBJECT_CONTEXT,
                            )
                        )
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="subjects/subject_groups",
                        record_count=len(subject_records),
                    )
                )

            # Category 4: staff_resource_context (Sanitized & Opaque Tokens)
            if ContextCategory.STAFF_RESOURCE_CONTEXT in request.requested_categories:
                staff_records = await self._adapter.fetch_staff_records(request.tenant_id)
                for idx, rec in enumerate(staff_records, start=1):
                    opaque_token = generate_opaque_staff_token(idx)
                    raw_id = rec.get("raw_id", idx)

                    # Bind opaque token to raw PK strictly in validation context
                    validation_ctx.raw_entity_id_map[opaque_token] = {
                        "raw_id": raw_id,
                        "source_entity": "staff",
                    }
                    validation_ctx.authorized_resource_ids.add(opaque_token)

                    safe_display = sanitize_staff_display_name(
                        name=rec.get("name"),
                        surname=rec.get("surname"),
                        opaque_ref=opaque_token,
                        obfuscate_name=True,  # Strict privacy: mask personal names
                    )

                    model_ctx.staff_resources.append(
                        ModelVisibleStaff(
                            opaque_staff_ref=opaque_token,
                            qualification=rec.get("qualification"),
                            work_exp=rec.get("work_exp"),
                            sanitized_display_name=safe_display,
                        )
                    )

                    omissions.append(
                        OmissionRecord(
                            field_path=f"staff.raw_id[{raw_id}]",
                            reason="Raw personnel primary key replaced with provider-neutral token",
                            category=ContextCategory.STAFF_RESOURCE_CONTEXT,
                        )
                    )

                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="staff",
                        record_count=len(staff_records),
                    )
                )

            # Category 5: commitment_context (Informational busy periods)
            if ContextCategory.COMMITMENT_CONTEXT in request.requested_categories:
                commit_records = await self._adapter.fetch_commitment_records(request.tenant_id)
                for rec in commit_records:
                    model_ctx.commitments.append(
                        ModelVisibleCommitment(
                            day=rec.get("day"),
                            time_from=rec.get("time_from"),
                            time_to=rec.get("time_to"),
                            start_time=rec.get("start_time"),
                            end_time=rec.get("end_time"),
                        )
                    )
                    validation_ctx.raw_timetable_records.append(rec)
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="subject_timetable",
                        record_count=len(commit_records),
                    )
                )

            # Category 6: calendar_context (Informational holidays and events)
            if ContextCategory.CALENDAR_CONTEXT in request.requested_categories:
                cal_records = await self._adapter.fetch_calendar_records(request.tenant_id)
                for rec in cal_records:
                    model_ctx.calendar_entries.append(
                        ModelVisibleCalendar(
                            holiday_from_date=rec.get("from_date"),
                            holiday_to_date=rec.get("to_date"),
                            holiday_description=rec.get("description"),
                            event_title=rec.get("event_title"),
                            event_description=rec.get("event_description"),
                            event_start_date=rec.get("event_start_date"),
                            event_end_date=rec.get("event_end_date"),
                            event_type=rec.get("event_type"),
                        )
                    )
                    validation_ctx.raw_calendar_records.append(rec)
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="annual_calendar/events",
                        record_count=len(cal_records),
                    )
                )

            # Category 7: aggregate_context (Cohort student headcounts only)
            if ContextCategory.AGGREGATE_CONTEXT in request.requested_categories:
                headcounts = await self._adapter.fetch_cohort_student_counts(request.tenant_id)
                model_ctx.cohort_student_headcounts = headcounts
                aggregates["cohort_student_headcounts"] = headcounts
                provenance.append(
                    ProvenanceRecord(
                        source_system="mysql_iredu",
                        entity_name="students/student_session (AGGREGATE ONLY)",
                        record_count=sum(headcounts.values()),
                    )
                )
                omissions.append(
                    OmissionRecord(
                        field_path="students.*",
                        reason=(
                            "Individual student PII strictly forbidden; aggregated headcounts only"
                        ),
                        category=ContextCategory.AGGREGATE_CONTEXT,
                    )
                )

        except (AdapterAccessDeniedError, DatabaseAccessBlockedError) as exc:
            return ContextAssemblyResult(
                assembly_status=AssemblyStatus.BLOCKED,
                warnings=[f"Context retrieval blocked: {exc}"],
                unresolved_questions=[str(exc)],
            )
        except Exception as exc:
            return ContextAssemblyResult(
                assembly_status=AssemblyStatus.DEGRADED,
                warnings=[f"Partial context assembly failure: {exc}"],
                unresolved_questions=[str(exc)],
            )

        status = AssemblyStatus.READY
        if warnings:
            status = AssemblyStatus.DEGRADED

        return ContextAssemblyResult(
            assembly_status=status,
            model_context=model_ctx,
            validation_context=validation_ctx,
            aggregates=aggregates,
            provenance=provenance,
            omissions=omissions,
            warnings=warnings,
            unresolved_questions=unresolved,
        )
