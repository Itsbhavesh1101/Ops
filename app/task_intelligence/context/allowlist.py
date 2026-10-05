"""Frozen 28 PLANNER_CONTEXT_ALLOWED fields and field privacy enforcement.

Enforces:
1. Exact 28 fields baseline from Task 0G.2 / Task 0G.3 mapping.
2. Default deny for any field not explicitly whitelisted.
3. Students strictly AGGREGATE_ONLY (no names, emails, phones, IDs, or attendance).
4. Staff opaque reference mapping (raw upstream IDs never exposed to model).
5. Informational-only semantics for calendar/timetable.
"""

from typing import Final

# The exactly 28 frozen PLANNER_CONTEXT_ALLOWED fields
FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        # calendar_context (3 annual_calendar + 5 events = 8 fields)
        "annual_calendar.from_date",
        "annual_calendar.to_date",
        "annual_calendar.description",
        "events.event_title",
        "events.event_description",
        "events.start_date",
        "events.end_date",
        "events.event_type",
        # academic_context (3 fields)
        "classes.class",
        "sections.section",
        "sessions.session",
        # organization_context (2 fields)
        "department.department_name",
        "staff_designation.designation",
        # staff_resource_context (4 fields)
        "staff.qualification",
        "staff.work_exp",
        "staff.name",
        "staff.surname",
        # subject_context (6 fields)
        "subjects.name",
        "subjects.type",
        "subjects.code",
        "subject_groups.name",
        "subject_groups.description",
        "subject_group_class_sections.description",
        # commitment_context (5 fields)
        "subject_timetable.day",
        "subject_timetable.time_from",
        "subject_timetable.time_to",
        "subject_timetable.start_time",
        "subject_timetable.end_time",
    }
)

assert len(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS) == 28, (
    f"FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS must contain exactly 28 fields, "
    f"got {len(FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS)}"
)

# Forbidden sensitive PII patterns (default-deny enforcement)
FORBIDDEN_PII_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "salary",
        "bank",
        "tax",
        "ssn",
        "national_id",
        "phone",
        "email",
        "address",
        "medical",
        "guardian",
        "student_id",
        "biometric",
    }
)


def is_field_allowed_for_planner(table_name: str, column_name: str) -> bool:
    """Check whether a specific table and column pair is in the 28 allowed planner fields."""
    key = f"{table_name.lower().strip()}.{column_name.lower().strip()}"
    return key in FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS


def sanitize_staff_display_name(
    name: str | None,
    surname: str | None,
    opaque_ref: str,
    obfuscate_name: bool = True,
) -> str:
    """Format safe staff display string.

    If obfuscate_name is True, returns the opaque reference token (e.g., 'StaffRef_1')
    to prevent personal identifier exposure in model prompts.
    """
    if obfuscate_name:
        return opaque_ref
    parts = [p for p in (name, surname) if p and p.strip()]
    return " ".join(parts) if parts else opaque_ref


def generate_opaque_staff_token(index: int) -> str:
    """Generate provider-neutral opaque staff token without leaking upstream database PK."""
    return f"StaffRef_{index}"
