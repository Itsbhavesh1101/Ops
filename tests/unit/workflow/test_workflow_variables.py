"""Unit tests for workflow variable minimization, mandatory allowlisting, and security scrubbing."""

import pytest

from app.workflow.variables import (
    MAX_VARIABLE_STRING_LENGTH,
    PHASE_3A_FIXTURE_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
    WorkflowVariableValidationError,
    filter_workflow_variables,
)


def test_no_allowlist_fails_closed() -> None:
    """Verify that absence of an explicit allowlist strictly drops all variables."""
    raw = {
        "priority": "high",
        "decision": "approved",
        "comment": "All clear",
    }
    # Empty allowlist fails closed
    assert filter_workflow_variables(raw, allowed_keys=frozenset()) == {}


def test_filter_with_explicit_allowlist() -> None:
    """Verify allowlist restricts variables strictly to approved business fields."""
    raw = {
        "priority": "high",
        "decision": "approved",
        "unapproved_field": "harmless_looking_value",
        "custom_metadata": {"some": "data"},
    }
    allowlist = frozenset({"priority", "decision"})
    filtered = filter_workflow_variables(raw, allowed_keys=allowlist)
    assert "unapproved_field" not in filtered
    assert "custom_metadata" not in filtered
    assert filtered == {
        "priority": "high",
        "decision": "approved",
    }


def test_defense_in_depth_scrubs_internal_and_credentials_even_if_in_allowlist() -> None:
    """Verify internal engine keys and security tokens are stripped even if present in allowlist."""
    raw = {
        "_tenant_id": "tenant-1",
        "_business_reference": "ref-123",
        "flowable:initiator": "user-admin",
        "api_key": "secret-token-123",
        "user_password": "supersecretpassword",
        "auth_token": "bearer-xyz",
        "priority": "high",
        "decision": "approved",
    }
    # Adversary or misconfiguration attempts to allowlist sensitive/internal keys
    compromised_allowlist = frozenset(raw.keys())
    filtered = filter_workflow_variables(raw, allowed_keys=compromised_allowlist)
    assert "_tenant_id" not in filtered
    assert "_business_reference" not in filtered
    assert "flowable:initiator" not in filtered
    assert "api_key" not in filtered
    assert "user_password" not in filtered
    assert "auth_token" not in filtered
    assert filtered == {"priority": "high", "decision": "approved"}


def test_truncate_oversized_string_value() -> None:
    """Verify oversized strings are truncated to bounded length."""
    long_str = "x" * (MAX_VARIABLE_STRING_LENGTH + 500)
    raw = {"comment": long_str}
    filtered = filter_workflow_variables(raw, allowed_keys=frozenset({"comment"}))
    assert len(filtered["comment"]) == MAX_VARIABLE_STRING_LENGTH


def test_payload_exceeding_max_bytes_raises_error() -> None:
    """Verify payload exceeding byte size limit raises WorkflowVariableValidationError."""
    # 20 items of 1900 chars each = ~38KB > 32KB
    many_items = {f"k_{i}": "v" * 1900 for i in range(20)}
    allowlist = frozenset(many_items.keys())
    with pytest.raises(WorkflowVariableValidationError) as exc_info:
        filter_workflow_variables(many_items, allowed_keys=allowlist)
    assert "exceed max size" in str(exc_info.value)


def test_workflow_variable_policy_filter() -> None:
    """Verify WorkflowVariablePolicy applies mandatory allowlist filtering."""
    policy = WorkflowVariablePolicy(allowed_keys=PHASE_3A_FIXTURE_ALLOWED_VARIABLES)
    raw = {
        "priority": "high",
        "decision": "approved",
        "comment": "Great work",
        "rogue_variable": "injected",
    }
    filtered = policy.filter(raw)
    assert "rogue_variable" not in filtered
    assert filtered == {
        "priority": "high",
        "decision": "approved",
        "comment": "Great work",
    }


def test_filter_handles_none_and_empty() -> None:
    """Verify None and empty input returns empty dict."""
    allowlist = frozenset({"priority"})
    assert filter_workflow_variables(None, allowed_keys=allowlist) == {}
    assert filter_workflow_variables({}, allowed_keys=allowlist) == {}
