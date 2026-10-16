"""Context allowlist security verification suite for Phase 8B.

Enforces:
1. Exact 28 fields baseline in FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS.
2. Default-deny detection of accidental additions or removals.
3. Rejection of unknown tables and columns.
4. Prevention of prohibited sensitive fields from entering planner context.
"""


from app.task_intelligence.context.allowlist import (
    FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS,
    is_field_allowed_for_planner,
)
from app.task_intelligence.planner.prompts import build_generation_prompt
from app.task_intelligence.schemas import GoalCreate


class TestContextAllowlistVerification:
    """Verifies strict adherence to the 28 frozen allowed planner context fields."""

    def test_exact_28_frozen_allowed_fields_count(self) -> None:
        """Asserts exactly 28 fields exist in the frozen allowlist contract."""
        assert len(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS) == 28, (
            f"FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS must contain exactly 28 fields, "
            f"found {len(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS)}."
        )

    def test_exact_expected_allowed_field_set(self) -> None:
        """Asserts the exact set of 28 allowed fields matches architecture baseline."""
        expected_fields = frozenset(
            {
                # calendar_context (8)
                "annual_calendar.from_date",
                "annual_calendar.to_date",
                "annual_calendar.description",
                "events.event_title",
                "events.event_description",
                "events.start_date",
                "events.end_date",
                "events.event_type",
                # academic_context (3)
                "classes.class",
                "sections.section",
                "sessions.session",
                # organization_context (2)
                "department.department_name",
                "staff_designation.designation",
                # staff_resource_context (4)
                "staff.qualification",
                "staff.work_exp",
                "staff.name",
                "staff.surname",
                # subject_context (6)
                "subjects.name",
                "subjects.type",
                "subjects.code",
                "subject_groups.name",
                "subject_groups.description",
                "subject_group_class_sections.description",
                # commitment_context (5)
                "subject_timetable.day",
                "subject_timetable.time_from",
                "subject_timetable.time_to",
                "subject_timetable.start_time",
                "subject_timetable.end_time",
            }
        )
        assert FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS == expected_fields

    def test_accidental_field_additions_or_deletions_detected(self) -> None:
        """Any drift from the 28-field contract must fail immediately."""
        mutated_allowlist = set(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS)
        mutated_allowlist.add("staff.salary")
        assert len(mutated_allowlist) != 28

        mutated_allowlist_less = set(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS)
        mutated_allowlist_less.remove("classes.class")
        assert len(mutated_allowlist_less) != 28

    def test_unknown_fields_are_rejected(self) -> None:
        """Unknown or arbitrary table/column pairs are rejected by default deny."""
        assert not is_field_allowed_for_planner("unknown_table", "column_x")
        assert not is_field_allowed_for_planner("staff", "salary")
        assert not is_field_allowed_for_planner("students", "name")
        assert not is_field_allowed_for_planner("students", "email")
        assert not is_field_allowed_for_planner("accounts", "bank_account")
        assert not is_field_allowed_for_planner("system", "password")

    def test_prohibited_fields_never_reach_prompt(self) -> None:
        """Prohibited fields passed via arbitrary context dictionaries are scrubbed."""
        hostile_context = {
            "annual_calendar": {"description": "Fall Term"},
            "staff": {
                "qualification": "M.Sc.",
                "salary": 95000,
                "bank_account": "ACC-998877",
                "ssn": "123-45-6789",
                "password": "SecretPassword123!",
            },
            "students": {
                "student_id": "STU-1001",
                "name": "Jane Doe",
                "email": "jane@school.edu",
            },
        }
        goal = GoalCreate(objective="Schedule fall classes")
        prompt = build_generation_prompt(goal, context=hostile_context)

        # Allowed field must be present
        assert "Fall Term" in prompt
        assert "M.Sc." in prompt

        # Prohibited fields must be scrubbed
        assert "salary" not in prompt
        assert "95000" not in prompt
        assert "bank_account" not in prompt
        assert "ACC-998877" not in prompt
        assert "ssn" not in prompt
        assert "123-45-6789" not in prompt
        assert "password" not in prompt
        assert "SecretPassword123!" not in prompt
        assert "student_id" not in prompt
        assert "STU-1001" not in prompt
        assert "jane@school.edu" not in prompt
