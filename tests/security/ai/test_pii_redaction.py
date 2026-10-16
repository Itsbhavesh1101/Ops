"""PII and sensitive data redaction verification suite for Phase 8B.

Enforces:
1. Strict exclusion of prohibited sensitive keys and values:
   - password, access_token, refresh_token, api_key, secret, cookie, authorization
   - salary, bank account, tax identifier, SSN, medical details
   - phone, personal email, home address
   - student name, student database ID
   - staff upstream primary key
2. Student information remains aggregate-only.
3. Staff identifiers remain opaque reference tokens (StaffRef_1).
4. Handles nested structures, lists, and mixed-case key variants.
"""


from app.task_intelligence.context.allowlist import (
    generate_opaque_staff_token,
    sanitize_staff_display_name,
)
from app.task_intelligence.context.models import (
    ModelContext,
    ModelVisibleStaff,
)
from app.task_intelligence.planner.prompts import (
    build_generation_prompt,
    scrub_sensitive_context,
)
from app.task_intelligence.schemas import GoalCreate


class TestPiiAndSensitiveDataRedaction:
    """Verifies that sensitive PII and confidential credentials never enter model context."""

    def test_prohibited_keys_scrubbed_from_nested_structures(self) -> None:
        """Verifies recursive scrubbing across nested dictionaries and lists with mixed casing."""
        raw_context = {
            "academic": {"grade": "Grade 10", "section": "A"},
            "sensitive_bundle": {
                "PASSWORD": "SuperSecretPassword123",
                "access_token": "eyJhbGciOiJIUzI1Ni...",
                "RefreshToken": "refresh_token_9999",
                "API_KEY": "sk-live-1234567890",
                "secret_credential": "top_secret_val",
                "Cookie": "session_id=abcd1234efgh",
                "Authorization": "Bearer token123",
                "Salary": 125000,
                "BankAccount": "9876543210",
                "tax_identifier": "TX-100200",
                "SSN": "000-12-3456",
                "medical_notes": "Allergic to penicillin",
                "Phone": "+1-555-0199",
                "Personal_Email": "teacher@personal-domain.com",
                "home_address": "123 Main Street, Cityville",
                "student_id": "STU_DB_4421",
                "student_name": "Johnny Doe",
                "staff_upstream_id": 99999,
            },
            "nested_list": [
                {"safe_field": "chemistry", "Salary": 75000},
                {"items": [{"api_key": "nested_key"}]},
            ],
        }

        scrubbed = scrub_sensitive_context(raw_context)

        # Safe fields preserved
        assert scrubbed["academic"]["grade"] == "Grade 10"
        assert scrubbed["nested_list"][0]["safe_field"] == "chemistry"

        # Check sensitive fields completely absent
        sensitive_bundle = scrubbed.get("sensitive_bundle", {})
        assert "PASSWORD" not in sensitive_bundle
        assert "access_token" not in sensitive_bundle
        assert "RefreshToken" not in sensitive_bundle
        assert "API_KEY" not in sensitive_bundle
        assert "secret_credential" not in sensitive_bundle
        assert "Cookie" not in sensitive_bundle
        assert "Authorization" not in sensitive_bundle
        assert "Salary" not in sensitive_bundle
        assert "BankAccount" not in sensitive_bundle
        assert "tax_identifier" not in sensitive_bundle
        assert "SSN" not in sensitive_bundle
        assert "medical_notes" not in sensitive_bundle
        assert "Phone" not in sensitive_bundle
        assert "Personal_Email" not in sensitive_bundle
        assert "home_address" not in sensitive_bundle
        assert "student_id" not in sensitive_bundle
        assert "student_name" not in sensitive_bundle
        assert "staff_upstream_id" not in sensitive_bundle

        # Nested list scrubbing
        assert "Salary" not in scrubbed["nested_list"][0]
        assert "api_key" not in scrubbed["nested_list"][1]["items"][0]

    def test_student_information_is_strictly_aggregate_only(self) -> None:
        """ModelContext permits only cohort aggregate counts, zero individual student records."""
        ctx = ModelContext(
            cohort_student_headcounts={
                "Class 10 - Section A": 35,
                "Class 10 - Section B": 38,
            }
        )
        assert hasattr(ctx, "cohort_student_headcounts")
        assert not hasattr(ctx, "students")
        assert not hasattr(ctx, "student_records")
        assert not hasattr(ctx, "student_names")

        goal = GoalCreate(objective="Plan examination seating")
        prompt = build_generation_prompt(goal, context=ctx)

        assert "Class 10 - Section A" in prompt
        assert "35" in prompt
        assert "student_id" not in prompt
        assert "student_name" not in prompt

    def test_staff_opaque_reference_tokens_enforced(self) -> None:
        """Staff identifiers must use opaque tokens without revealing upstream primary keys."""
        token1 = generate_opaque_staff_token(1)
        token2 = generate_opaque_staff_token(42)
        assert token1 == "StaffRef_1"
        assert token2 == "StaffRef_42"

        # Safe display name returns opaque token when obfuscate_name is True
        display = sanitize_staff_display_name(
            name="Alice",
            surname="Smith",
            opaque_ref=token1,
            obfuscate_name=True,
        )
        assert display == "StaffRef_1"
        assert "Alice" not in display

        # ModelVisibleStaff utilizes opaque token
        staff = ModelVisibleStaff(
            opaque_staff_ref=token1,
            qualification="M.Ed.",
            sanitized_display_name=display,
        )
        assert staff.opaque_staff_ref == "StaffRef_1"
        assert not hasattr(staff, "staff_id")
        assert not hasattr(staff, "employee_id")
        assert not hasattr(staff, "salary")
