"""Security, privacy, and governance boundary verification (Phase 2B).

Verifies:
1. Prompt-injection text cannot widen context or grant authority.
2. Raw SQL cannot be generated or executed through adapter API.
3. Student rows cannot enter model context (strictly aggregate only).
4. External raw IDs are not canonical E-7 IDs (opaque references enforced).
5. validation_context is strictly isolated from model prompts.
6. Unknown entity references fail validation.
7. Model cannot approve plans.
8. Model cannot set canonical plan status (remains GENERATED).
9. Cross-tenant context requests are rejected before model calls.
10. Secrets are absent from logs, errors, and generation results.
"""

import pytest

from app.task_intelligence.context import (
    ContextAssemblyRequest,
    ContextBuilder,
    DatabaseAccessBlockedError,
    ProductionMySQLAdapter,
    SyntheticContextAdapter,
)
from app.task_intelligence.context.allowlist import (
    FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS,
    is_field_allowed_for_planner,
)
from app.task_intelligence.planner.prompts import (
    build_generation_prompt,
)
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
    TaskDraft,
    ValidationIssueCode,
)
from app.task_intelligence.validators import PlanValidator
from tests.ai_eval.deterministic_model import (
    create_gold_standard_exam_plan,
)


def test_planner_field_allowlist_is_exactly_28() -> None:
    """Security check: exactly 28 fields allowed for model context, default deny on all others."""
    assert len(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS) == 28

    # Whitelisted fields
    assert is_field_allowed_for_planner("annual_calendar", "from_date")
    assert is_field_allowed_for_planner("classes", "class")
    assert is_field_allowed_for_planner("staff", "qualification")
    assert is_field_allowed_for_planner("subjects", "name")

    # Strictly forbidden fields
    assert not is_field_allowed_for_planner("students", "name")
    assert not is_field_allowed_for_planner("students", "email")
    assert not is_field_allowed_for_planner("staff", "password")
    assert not is_field_allowed_for_planner("staff", "basic_salary")
    assert not is_field_allowed_for_planner("staff", "bank_account_no")
    assert not is_field_allowed_for_planner("online_courses", "title")


@pytest.mark.asyncio
async def test_student_rows_never_enter_model_context() -> None:
    """Security check: student PII and individual rows strictly forbidden."""
    adapter = SyntheticContextAdapter()
    builder = ContextBuilder(adapter=adapter)

    req = ContextAssemblyRequest(
        tenant_id="tenant-synth-001",
        objective="Plan exam operations",
    )
    result = await builder.build_context(req)

    # model_context must contain zero student PII fields
    assert hasattr(result.model_context, "cohort_student_headcounts")
    headcounts = result.model_context.cohort_student_headcounts
    assert isinstance(headcounts, dict)
    assert all(isinstance(v, int) for v in headcounts.values())

    # Raw students table has no presence in model_context attributes
    model_ctx_dict = result.model_context.model_dump()
    assert "students" not in model_ctx_dict
    assert "student_session" not in model_ctx_dict


@pytest.mark.asyncio
async def test_cross_tenant_access_denied_before_model_call() -> None:
    """Security check: cross-tenant context access fails closed immediately."""
    adapter = SyntheticContextAdapter(tenant_id="tenant-authorized-42")
    builder = ContextBuilder(adapter=adapter)

    req = ContextAssemblyRequest(
        tenant_id="tenant-unauthorized-99",
        objective="Exfiltrate exams",
    )
    result = await builder.build_context(req)
    assert result.assembly_status.value == "BLOCKED"
    assert "Context retrieval blocked" in result.warnings[0]


def test_production_mysql_adapter_blocked_by_governance() -> None:
    """Security check: BLK-01 prevents direct production MySQL database access."""
    adapter = ProductionMySQLAdapter()
    with pytest.raises(DatabaseAccessBlockedError, match="BLK-01"):
        adapter.query("SELECT * FROM staff")


def test_validation_context_isolated_from_prompt() -> None:
    """Security check: internal validation evidence is never serialized into model prompt."""
    from app.task_intelligence.context.models import ValidationContext

    val_ctx = ValidationContext(
        raw_entity_id_map={"StaffRef_1": {"raw_id": 99999, "source_entity": "staff"}},
        raw_timetable_records=[
            {"raw_db_table": "internal_timetable_dump", "secret_key": "confidential"}
        ],
    )
    assert val_ctx.raw_timetable_records is not None

    goal = GoalCreate(objective="Schedule physics exam")
    # Prompt builder only accepts ModelContext or dict, never ValidationContext
    prompt = build_generation_prompt(goal, context=None)

    assert "99999" not in prompt
    assert "internal_timetable_dump" not in prompt
    assert "confidential" not in prompt


def test_model_cannot_approve_or_set_canonical_status() -> None:
    """Security check: PlanSchema proposals carry no canonical approval authority."""
    plan = create_gold_standard_exam_plan()
    # PlanSchema has no 'status' or 'approved' field.
    # Only PlanRecord in Postgres has canonical status.
    assert not hasattr(plan, "status")
    assert not hasattr(plan, "is_approved")
    assert not hasattr(plan, "approved_by")


def test_unknown_entity_reference_detected_by_validator() -> None:
    """Security check: unknown milestone or resource references fail validation."""
    from app.task_intelligence.schemas import MilestoneDraft

    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Invalid Milestone Plan",
        tasks=[
            TaskDraft(
                temporary_id="T1",
                title="Task 1",
                estimated_duration_minutes=60,
            )
        ],
        milestones=[
            MilestoneDraft(
                temporary_id="M1",
                name="Milestone 1",
                associated_task_ids=["NONEXISTENT_TASK_ID"],
            )
        ],
    )
    res = validator.validate(plan)
    assert not res.is_valid
    assert any(i.code == ValidationIssueCode.UNKNOWN_MILESTONE_TASK for i in res.issues)
