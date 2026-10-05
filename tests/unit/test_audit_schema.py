"""Unit tests for audit event schema and defensive details validation.

Part of Phase 1 Foundation Task 1F.
Validates:
- Defensive audit details: safe keys, recursive denylist, case/separator variations,
  type constraints, depth limits, size limits, and non-string key rejection.
- AuditEventCreate schema: namespaced event_type regex, identity consistency rules,
  length bounds, outcome enum, immutability, and rejection of extra/caller-supplied timestamps.
"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.audit.validation import (
    MAX_AUDIT_DETAILS_DEPTH,
    MAX_AUDIT_STRING_LENGTH,
    normalize_audit_key,
    validate_audit_details,
)
from app.core.auth.models import AuthenticationMethod, PrincipalType

# ==============================================================================
# Audit Details Validation Tests
# ==============================================================================


def test_normalize_audit_key_removes_separators_and_lowercases() -> None:
    """Verify normalize_audit_key strips underscores, hyphens, spaces and lowercases."""
    assert normalize_audit_key("API_KEY") == "apikey"
    assert normalize_audit_key("api-key") == "apikey"
    assert normalize_audit_key("Api Key") == "apikey"
    assert normalize_audit_key("CLIENT_SECRET") == "clientsecret"
    assert normalize_audit_key("Access-Token") == "accesstoken"
    assert normalize_audit_key("Password") == "password"


def test_validate_audit_details_accepts_safe_dict_and_empty() -> None:
    """Verify safe dictionary structures and empty details are accepted."""
    assert validate_audit_details(None) == {}
    assert validate_audit_details({}) == {}

    safe_payload = {
        "action": "user_login",
        "attempt_count": 3,
        "is_mfa": True,
        "ratio": 1.25,
        "tags": ["web", "sso"],
        "metadata": {"ip_region": "us-east", "risk_score": 0.12},
    }
    validated = validate_audit_details(safe_payload)
    assert validated == safe_payload


def test_validate_audit_details_rejects_non_dict_root() -> None:
    """Verify non-dictionary root values are rejected."""
    with pytest.raises(ValueError, match="root must be a dictionary"):
        validate_audit_details(["not", "a", "dict"])

    with pytest.raises(ValueError, match="root must be a dictionary"):
        validate_audit_details("string_root")


@pytest.mark.parametrize(
    "denied_key",
    [
        "password",
        "PASSWORD",
        "pass_word",
        "pass-word",
        "Pass Word",
        "passwd",
        "secret",
        "client_secret",
        "client-secret",
        "CLIENT_SECRET",
        "api_key",
        "apiKey",
        "API-KEY",
        "access_token",
        "accessToken",
        "ACCESS-TOKEN",
        "refresh_token",
        "id_token",
        "authorization",
        "AUTHORIZATION",
        "cookie",
        "Cookie",
    ],
)
def test_validate_audit_details_rejects_denied_keys_at_root(denied_key: str) -> None:
    """Verify suspicious credential keys at root are rejected case/separator-insensitively."""
    payload = {denied_key: "synthetic_marker_value"}
    with pytest.raises(ValueError, match="Suspicious sensitive key"):
        validate_audit_details(payload)


def test_validate_audit_details_rejects_nested_denied_keys() -> None:
    """Verify deeply nested denied keys in dicts and lists are detected and rejected."""
    nested_in_dict = {
        "context": {
            "auth_attempt": {
                "api_key": "synthetic_marker",
            }
        }
    }
    with pytest.raises(ValueError, match="Suspicious sensitive key 'api_key'"):
        validate_audit_details(nested_in_dict)

    nested_in_list = {
        "headers": [
            {"name": "Content-Type", "val": "application/json"},
            {"Authorization": "Bearer synthetic_marker"},
        ]
    }
    with pytest.raises(ValueError, match="Suspicious sensitive key 'Authorization'"):
        validate_audit_details(nested_in_list)


def test_validate_audit_details_rejects_non_string_dict_keys() -> None:
    """Verify dictionary keys that are not strings are rejected."""
    payload: dict[Any, Any] = {123: "numeric_key_prohibited"}
    with pytest.raises(ValueError, match="keys must be strings"):
        validate_audit_details(payload)


def test_validate_audit_details_rejects_unsupported_python_types() -> None:
    """Verify exceptions, ORM objects, sets, and custom objects are rejected."""

    class CustomObj:
        pass

    with pytest.raises(ValueError, match="Unsupported value type 'CustomObj'"):
        validate_audit_details({"obj": CustomObj()})

    with pytest.raises(ValueError, match="Unsupported value type 'RuntimeError'"):
        validate_audit_details({"error": RuntimeError("something failed")})

    with pytest.raises(ValueError, match="Unsupported value type 'set'"):
        validate_audit_details({"tags": {"set_not_allowed"}})


def test_validate_audit_details_rejects_excessive_depth() -> None:
    """Verify nesting exceeding MAX_AUDIT_DETAILS_DEPTH is rejected."""
    deep: dict[str, Any] = {"level": 1}
    curr = deep
    for i in range(2, MAX_AUDIT_DETAILS_DEPTH + 3):
        curr["child"] = {"level": i}
        curr = curr["child"]

    with pytest.raises(ValueError, match="nesting depth exceeds maximum limit"):
        validate_audit_details(deep)


def test_validate_audit_details_rejects_oversized_string() -> None:
    """Verify string values exceeding MAX_AUDIT_STRING_LENGTH are rejected."""
    payload = {"large_text": "x" * (MAX_AUDIT_STRING_LENGTH + 1)}
    with pytest.raises(ValueError, match="string value exceeds maximum length"):
        validate_audit_details(payload)


def test_validate_audit_details_rejects_oversized_serialized_payload() -> None:
    """Verify serialized JSON payloads exceeding MAX_AUDIT_DETAILS_BYTES are rejected."""
    # Generate large dictionary of acceptable strings that together exceed 64 KiB
    large_payload = {f"k_{i}": "a" * 1000 for i in range(70)}
    with pytest.raises(ValueError, match="Serialized audit details size"):
        validate_audit_details(large_payload)


def test_validate_audit_details_rejects_non_finite_floats() -> None:
    """Verify NaN, +Inf, -Inf are rejected recursively in root, dicts, and lists."""
    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_audit_details({"value": float("nan")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_audit_details({"value": float("inf")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_audit_details({"value": float("-inf")})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_audit_details({"nested": {"value": float("nan")}})

    with pytest.raises(ValueError, match="Non-finite float value"):
        validate_audit_details({"items": [1, 2, float("inf")]})


def test_validate_audit_details_accepts_finite_floats_and_booleans() -> None:
    """Verify finite float and boolean values remain accepted."""
    assert validate_audit_details({"value": 1.5}) == {"value": 1.5}
    assert validate_audit_details({"enabled": True}) == {"enabled": True}
    assert validate_audit_details({"active": False}) == {"active": False}
    assert validate_audit_details({"nested": {"ratio": 0.0, "flag": True}}) == {
        "nested": {"ratio": 0.0, "flag": True}
    }


# ==============================================================================
# AuditEventCreate Schema Tests
# ==============================================================================


def test_audit_event_create_valid_minimal_system_event() -> None:
    """Verify valid minimal system event without actor or tenant information."""
    event = AuditEventCreate(
        event_type="system.startup.completed",
    )
    assert event.schema_version == 1
    assert event.tenant_id is None
    assert event.actor_principal_id is None
    assert event.actor_principal_type is None
    assert event.authentication_method is None
    assert event.event_type == "system.startup.completed"
    assert event.resource_type is None
    assert event.resource_id is None
    assert event.outcome is None
    assert event.correlation_id is None
    assert event.details == {}


def test_audit_event_create_valid_authenticated_event() -> None:
    """Verify valid authenticated user event with all canonical fields."""
    event = AuditEventCreate(
        schema_version=1,
        tenant_id="tenant-123",
        actor_principal_id="principal-user-456",
        actor_principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        event_type="auth.token.verified",
        resource_type="auth.session",
        resource_id="session-789",
        outcome=AuditEventOutcome.SUCCESS,
        correlation_id="corr-abc-999",
        details={"ip": "127.0.0.1"},
    )
    assert event.tenant_id == "tenant-123"
    assert event.actor_principal_id == "principal-user-456"
    assert event.actor_principal_type == "HUMAN"
    assert event.authentication_method == "OIDC"
    assert event.outcome == "SUCCESS"
    assert event.details == {"ip": "127.0.0.1"}


@pytest.mark.parametrize(
    "invalid_event_type",
    [
        "",
        "   ",
        "singleword",
        "Auth.Access.Denied",
        "auth.access..denied",
        "auth.access.denied.",
        ".auth.access.denied",
        "auth access denied",
        "auth-access-denied",
        "123.access.denied",
        "a" * 129,
    ],
)
def test_audit_event_create_rejects_malformed_event_type(invalid_event_type: str) -> None:
    """Verify event_type regex rejects invalid, uppercase, single-segment, or blank formats."""
    with pytest.raises(ValidationError):
        AuditEventCreate(event_type=invalid_event_type)


@pytest.mark.parametrize(
    "field_name,invalid_val",
    [
        ("tenant_id", "   "),
        ("tenant_id", "t" * 256),
        ("actor_principal_id", "   "),
        ("actor_principal_id", "p" * 256),
        ("resource_type", "   "),
        ("resource_type", "r" * 129),
        ("resource_type", "https://example.com/api"),
        ("resource_type", "SELECT * FROM users"),
        ("resource_id", "   "),
        ("resource_id", "id" * 130),
        ("correlation_id", "   "),
        ("correlation_id", "c" * 129),
    ],
)
def test_audit_event_create_rejects_blank_or_oversized_string_fields(
    field_name: str, invalid_val: str
) -> None:
    """Verify whitespace-only or oversized bounded string fields are rejected."""
    kwargs: dict[str, Any] = {"event_type": "auth.access.denied", field_name: invalid_val}
    if field_name == "actor_principal_id":
        kwargs["tenant_id"] = "tenant-123"
        kwargs["actor_principal_type"] = "HUMAN"
        kwargs["authentication_method"] = "OIDC"
    with pytest.raises(ValidationError):
        AuditEventCreate(**kwargs)


def test_audit_event_create_actor_consistency_cases() -> None:
    """Verify comprehensive actor consistency invariants (Cases A through H)."""
    # Case A: actor_principal_id present, tenant_id missing => rejected
    with pytest.raises(ValidationError, match="tenant_id is required"):
        AuditEventCreate(
            event_type="auth.access.denied",
            actor_principal_id="principal-123",
            actor_principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=None,
        )

    # Case B: actor_principal_id present, actor_principal_type missing => rejected
    with pytest.raises(ValidationError, match="actor_principal_type is required"):
        AuditEventCreate(
            event_type="auth.access.denied",
            tenant_id="tenant-123",
            actor_principal_id="principal-123",
            actor_principal_type=None,
            authentication_method=AuthenticationMethod.OIDC,
        )

    # Case C: actor_principal_id present, authentication_method missing => rejected
    with pytest.raises(ValidationError, match="authentication_method is required"):
        AuditEventCreate(
            event_type="auth.access.denied",
            tenant_id="tenant-123",
            actor_principal_id="principal-123",
            actor_principal_type=PrincipalType.HUMAN,
            authentication_method=None,
        )

    # Case D: full authenticated actor tuple => accepted
    event_d = AuditEventCreate(
        event_type="auth.access.granted",
        tenant_id="tenant-123",
        actor_principal_id="principal-123",
        actor_principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
    )
    assert event_d.actor_principal_id == "principal-123"
    assert event_d.tenant_id == "tenant-123"
    assert event_d.actor_principal_type == "HUMAN"
    assert event_d.authentication_method == "OIDC"

    # Case E: actorless + tenant_id None => accepted as global system event
    event_e = AuditEventCreate(
        event_type="system.node.started",
        tenant_id=None,
        actor_principal_id=None,
        actor_principal_type=None,
        authentication_method=None,
    )
    assert event_e.actor_principal_id is None
    assert event_e.tenant_id is None

    # Case F: actorless + tenant_id present => accepted as tenant-scoped system event
    event_f = AuditEventCreate(
        event_type="system.quota.exceeded",
        tenant_id="tenant-123",
        actor_principal_id=None,
        actor_principal_type=None,
        authentication_method=None,
    )
    assert event_f.actor_principal_id is None
    assert event_f.tenant_id == "tenant-123"

    # Case G: actorless + actor_principal_type present => rejected
    with pytest.raises(ValidationError, match="actor_principal_type must be None"):
        AuditEventCreate(
            event_type="system.maintenance.start",
            actor_principal_id=None,
            actor_principal_type=PrincipalType.HUMAN,
        )

    # Case H: actorless + authentication_method present => rejected
    with pytest.raises(ValidationError, match="authentication_method must be None"):
        AuditEventCreate(
            event_type="system.maintenance.start",
            actor_principal_id=None,
            authentication_method=AuthenticationMethod.OIDC,
        )


def test_audit_event_create_rejects_invalid_actor_type_and_auth_method() -> None:
    """Verify unapproved actor principal types and auth methods are rejected."""
    with pytest.raises(ValidationError, match="actor_principal_type must be one of"):
        AuditEventCreate(
            event_type="auth.access.denied",
            tenant_id="tenant-123",
            actor_principal_id="user-123",
            actor_principal_type="ROBOT_ALIEN",
            authentication_method=AuthenticationMethod.OIDC,
        )

    with pytest.raises(ValidationError, match="authentication_method must be one of"):
        AuditEventCreate(
            event_type="auth.access.denied",
            tenant_id="tenant-123",
            actor_principal_id="user-123",
            actor_principal_type=PrincipalType.HUMAN,
            authentication_method="BASIC_AUTH_FORBIDDEN",
        )


def test_audit_event_create_schema_is_immutable_and_forbids_extra_fields() -> None:
    """Verify AuditEventCreate is frozen and forbids extra/caller-supplied fields."""
    event = AuditEventCreate(event_type="auth.access.denied")

    # Mutation forbidden
    with pytest.raises(ValidationError):
        event.event_type = "auth.access.granted"  # type: ignore[misc]

    # Section 6.A: AuditEventCreate has no audit_event_id field
    assert "audit_event_id" not in AuditEventCreate.model_fields

    # Section 6.B: Supplying audit_event_id as input is rejected because extra fields are forbidden
    with pytest.raises(ValidationError):
        AuditEventCreate(
            event_type="auth.access.denied",
            audit_event_id=uuid4(),  # type: ignore[call-arg]
        )

    # Extra fields forbidden (specifically recorded_at which must be server-authored)
    with pytest.raises(ValidationError):
        AuditEventCreate(
            event_type="auth.access.denied",
            recorded_at="2026-09-26T00:00:00Z",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        AuditEventCreate(
            event_type="auth.access.denied",
            unknown_extra_field="rejected",  # type: ignore[call-arg]
        )
