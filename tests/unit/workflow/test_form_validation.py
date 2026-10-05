"""Unit tests for form-js schema validation, component whitelisting, and submission screening."""

import pytest

from app.workflow.forms.validator import (
    FormInvalidSubmissionError,
    FormSecurityError,
    FormValidator,
)

SAMPLE_FORM = {
    "schemaVersion": 1,
    "id": "Form_ExpenseApproval",
    "type": "default",
    "components": [
        {
            "id": "Field_Amount",
            "key": "amount",
            "type": "number",
            "label": "Expense Amount",
            "validate": {"required": True, "min": 0.0, "max": 100000.0},
        },
        {
            "id": "Field_Category",
            "key": "category",
            "type": "select",
            "label": "Expense Category",
            "values": [
                {"label": "Travel", "value": "TRAVEL"},
                {"label": "Equipment", "value": "EQUIPMENT"},
                {"label": "Software", "value": "SOFTWARE"},
            ],
            "validate": {"required": True},
        },
        {
            "id": "Field_Justification",
            "key": "justification",
            "type": "textfield",
            "label": "Business Justification",
            "validate": {"required": True, "maxLength": 500},
        },
        {
            "id": "Field_Approved",
            "key": "approved",
            "type": "checkbox",
            "label": "I approve this request",
            "validate": {"required": False},
        },
    ],
}


def test_form_validator_parses_valid_form():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    assert form_def.id == "Form_ExpenseApproval"
    assert len(form_def.components) == 4
    assert {c.key for c in form_def.components} == {
        "amount",
        "category",
        "justification",
        "approved",
    }


def test_form_validator_rejects_unwhitelisted_component():
    malicious_form = {
        "schemaVersion": 1,
        "id": "Form_Attack",
        "components": [
            {
                "id": "Field_RawHtml",
                "key": "html_snippet",
                "type": "customHtml",
                "label": "Injected Component",
            }
        ],
    }
    validator = FormValidator()
    with pytest.raises(FormSecurityError, match="Unwhitelisted component type 'customHtml'"):
        validator.validate_form_schema(malicious_form)


def test_form_validator_rejects_script_injection_in_label():
    xss_form = {
        "schemaVersion": 1,
        "id": "Form_XSS",
        "components": [
            {
                "id": "Field_1",
                "key": "comments",
                "type": "textfield",
                "label": "Comments <script>alert(1)</script>",
            }
        ],
    }
    validator = FormValidator()
    with pytest.raises(FormSecurityError, match="Embedded script pattern detected"):
        validator.validate_form_schema(xss_form)


def test_form_derive_variable_policy():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)
    policy = validator.derive_variable_policy(form_def)

    assert policy.allowed_keys == frozenset({"amount", "category", "justification", "approved"})


def test_form_validate_valid_submission():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    submission = {
        "amount": 250.75,
        "category": "TRAVEL",
        "justification": "Flight to client on-site meeting",
        "approved": True,
    }
    cleaned = validator.validate_submission(form_def, submission)
    assert cleaned["amount"] == 250.75
    assert cleaned["category"] == "TRAVEL"
    assert cleaned["approved"] is True


def test_form_submission_rejects_missing_required_field():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    submission = {
        "amount": 250.75,
        # category missing
        "justification": "Valid justification",
    }
    with pytest.raises(FormInvalidSubmissionError, match="Missing required field 'category'"):
        validator.validate_submission(form_def, submission)


def test_form_submission_rejects_invalid_enum_option():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    submission = {
        "amount": 100.0,
        "category": "INVALID_CATEGORY",
        "justification": "Testing invalid selection",
    }
    with pytest.raises(
        FormInvalidSubmissionError, match="Value 'INVALID_CATEGORY' is not an allowed option"
    ):
        validator.validate_submission(form_def, submission)


def test_form_submission_rejects_number_out_of_bounds():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    submission = {
        "amount": 200000.0,  # Max is 100000.0
        "category": "EQUIPMENT",
        "justification": "Buying a datacenter",
    }
    with pytest.raises(FormInvalidSubmissionError, match="exceeds maximum allowed 100000.0"):
        validator.validate_submission(form_def, submission)


def test_form_submission_rejects_undeclared_variables():
    validator = FormValidator()
    form_def = validator.validate_form_schema(SAMPLE_FORM)

    submission = {
        "amount": 50.0,
        "category": "SOFTWARE",
        "justification": "IDE license",
        "injected_admin_flag": True,  # Not in form schema
    }
    with pytest.raises(
        FormInvalidSubmissionError, match="Undeclared variable 'injected_admin_flag'"
    ):
        validator.validate_submission(form_def, submission)
