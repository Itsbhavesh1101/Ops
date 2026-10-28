"""Canonical NotificationCommand models based on Phase-0 E-8 contract.

Adheres strictly to docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md and
docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.json.
Enforces transport-neutral, privacy-preserving notification commands.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Keywords that indicate prohibited sensitive data, secrets, or raw upstream database keys
PROHIBITED_CONTEXT_KEYWORDS: frozenset[str] = frozenset({
    # Secrets / credentials
    "password",
    "secret",
    "api_key",
    "private_key",
    "auth_key",
    "bearer_token",
    "credential",
    # Financial / payroll
    "salary",
    "compensation",
    "bank_account",
    "routing_number",
    "social_security",
    "payroll",
    # Student PII / sensitive records
    "student_ssn",
    "student_name",
    "student_dob",
    "student_email",
    "student_phone",
    "health_record",
    "disciplinary_record",
    "medical_record",
    # Raw institutional MySQL primary keys
    "mysql_id",
    "iredu_id",
    "upstream_pk",
    "raw_mysql_id",
})

# Narrowly defined allowed context fields for known canonical notification templates
ALLOWED_TEMPLATE_CONTEXT_FIELDS: dict[str, frozenset[str]] = {
    "EXAM_SCHEDULE_PUBLISHED": frozenset({
        "template_id",
        "schedule_id",
        "version",
        "effective_date",
    }),
    "EXAM_SCHEDULE_CHANGED": frozenset({
        "template_id",
        "schedule_id",
        "old_version",
        "new_version",
        "repair_reason",
        "effective_date",
    }),
    "TASK_BLOCKED_ALERT": frozenset({
        "template_id",
        "task_id",
        "plan_id",
        "temporary_id",
        "blocked_reason",
    }),
    "WORKFLOW_TASK_ASSIGNED": frozenset({
        "template_id",
        "workflow_task_id",
        "workflow_instance_id",
        "task_name",
        "task_definition_key",
        "due_at",
    }),
    "WORKFLOW_SLA_WARNING": frozenset({
        "template_id",
        "workflow_task_id",
        "policy_key",
        "warning_at",
        "breach_at",
        "action",
    }),
}


class TriggerKind(StrEnum):
    """Classification of trigger event / condition."""

    SOURCE_VERIFIED_EVENT = "SOURCE_VERIFIED_EVENT"
    SOURCE_BACKED_BUSINESS_CONDITION = "SOURCE_BACKED_BUSINESS_CONDITION"


class NotificationTrigger(BaseModel):
    """Provenance metadata detailing operational cause of notification command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_kind: TriggerKind
    source_event_name: str | None = None
    business_condition: str | None = None
    source_entity_reference: str | None = None

    @model_validator(mode="after")
    def validate_trigger_provenance(self) -> "NotificationTrigger":
        """Enforce provenance requirements based on trigger classification."""
        if self.trigger_kind == TriggerKind.SOURCE_VERIFIED_EVENT:
            if not self.source_event_name or not self.source_event_name.strip():
                raise ValueError("SOURCE_VERIFIED_EVENT requires non-empty 'source_event_name'")
        elif self.trigger_kind == TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION:
            if not self.business_condition or not self.business_condition.strip():
                raise ValueError(
                    "SOURCE_BACKED_BUSINESS_CONDITION requires non-empty 'business_condition'"
                )
        return self


class NotificationCommand(BaseModel):
    """Canonical provider-neutral NotificationCommand contract for E-8 engagement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = Field(default="1.0.0", min_length=1)
    command_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    correlation_reference: str = Field(..., min_length=1)
    causation_reference: str | None = None
    organization_scope_reference: str = Field(..., min_length=1)  # tenant_id
    trigger: NotificationTrigger
    recipient_references: list[str] = Field(..., min_length=1)
    notification_context: dict[str, Any]
    requested_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    @field_validator("recipient_references")
    @classmethod
    def validate_recipients(cls, v: list[str]) -> list[str]:
        """Ensure recipient references are non-empty and strictly opaque.

        Enforces Section 16 of E8_NOTIFICATION_ENGAGEMENT_CONTRACT:
        - Strictly rejects plaintext email addresses.
        - Strictly rejects plaintext telephone numbers (E.164, local, or formatted).
        - Strictly rejects unauthorized personal identifiers (names, SSNs, DOBs).
        - Strictly rejects raw institutional MySQL identifiers (pure integer PKs).
        - Strictly rejects malformed references (control characters, injection sequences).
        - Preserves canonical opaque references (e.g. 'urn:e7:...', 'user:...', UUIDs).
        """
        import re

        if not v:
            raise ValueError("recipient_references must contain at least one recipient reference")

        prohibited_pii_keywords = (
            "student_ssn",
            "student_name",
            "student_dob",
            "student_phone",
            "student_email",
            "social_security",
            "national_id",
            "passport",
            "first_name",
            "last_name",
            "full_name",
            "ssn:",
            "dob:",
        )
        prohibited_mysql_prefixes = (
            "mysql:",
            "iredu:",
            "students.id:",
            "staff.id:",
            "raw_id:",
            "mysql_id:",
        )

        for ref in v:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("recipient_references elements must be non-empty strings")
            clean_ref = ref.strip()

            # Malformed checks: length, control chars, injection delimiters
            if len(clean_ref) > 256:
                raise ValueError(
                    f"Recipient reference exceeds maximum length (256): '{clean_ref[:32]}...'"
                )
            if any(ord(c) < 32 or ord(c) == 127 for c in clean_ref):
                repr_ref = repr(clean_ref)
                raise ValueError(
                    f"Malformed recipient reference contains control characters: '{repr_ref}'"
                )
            if ";" in clean_ref or "--" in clean_ref or "/*" in clean_ref:
                raise ValueError(
                    f"Malformed recipient reference contains invalid characters: '{clean_ref}'"
                )

            # 1. Reject plaintext email addresses
            if "@" in clean_ref:
                raise ValueError(
                    f"Plaintext email address '{clean_ref}' is prohibited in recipient_references. "
                    "Use canonical opaque references (e.g. 'urn:e7:cohort:faculty' or 'user:<id>')."
                )

            # 2. Reject plaintext phone numbers (E.164, local, or formatted digit sequences)
            digits_only = re.sub(r"[\s\-\(\)\.]", "", clean_ref)
            is_intl = (
                digits_only.startswith("+")
                and digits_only[1:].isdigit()
                and len(digits_only) >= 7
            )
            is_local = digits_only.isdigit() and len(digits_only) >= 7
            if is_intl or is_local:
                raise ValueError(
                    f"Plaintext phone number '{clean_ref}' is prohibited in recipient_references. "
                    "Use canonical opaque references (e.g. 'urn:e7:cohort:faculty')."
                )

            lower_ref = clean_ref.lower()

            # 3. Reject unauthorized personal identifiers
            for pii in prohibited_pii_keywords:
                if pii in lower_ref:
                    msg = (
                        f"Unauthorized personal identifier detected in recipient reference "
                        f"'{clean_ref}'. Violates recipient privacy and data minimization."
                    )
                    raise ValueError(msg)

            # 4. Reject raw institutional MySQL identifiers (pure integer PKs or table.id prefixes)
            if clean_ref.isdigit():
                msg = (
                    f"Raw numeric/MySQL primary key '{clean_ref}' is prohibited in "
                    "recipient_references. Use canonical opaque references."
                )
                raise ValueError(msg)
            for prefix in prohibited_mysql_prefixes:
                if (
                    lower_ref.startswith(prefix)
                    or f".{prefix}" in lower_ref
                    or f"raw_{prefix}" in lower_ref
                ):
                    msg = (
                        f"Raw institutional MySQL identifier '{clean_ref}' is prohibited in "
                        "recipient_references."
                    )
                    raise ValueError(msg)

        return v

    @field_validator("notification_context")
    @classmethod
    def validate_privacy_boundaries(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Enforce data minimization, privacy boundaries, and typed template validation.

        Rejects student PII, employee compensation, authentication secrets, raw MySQL
        primary keys, and unexpected fields from crossing the E-7/E-8 boundary.
        """
        import re

        # 1. Strictly validate template-specific allowed fields if template_id is specified
        template_id = v.get("template_id")
        if isinstance(template_id, str) and template_id in ALLOWED_TEMPLATE_CONTEXT_FIELDS:
            allowed = ALLOWED_TEMPLATE_CONTEXT_FIELDS[template_id]
            for key in v:
                if key not in allowed:
                    raise ValueError(
                        f"notification_context contains unexpected field '{key}' for template "
                        f"'{template_id}'. Allowed fields are: {sorted(allowed)}."
                    )

        # 2. Key and Value Inspection across nested structures
        def _check_key(key: str) -> None:
            k_lower = key.strip().lower()
            for prohibited in PROHIBITED_CONTEXT_KEYWORDS:
                if prohibited in k_lower:
                    raise ValueError(
                        f"notification_context contains prohibited sensitive/PII field: '{key}'. "
                        "Violates E-8 privacy and data minimization boundaries."
                    )

        def _check_value(val: Any) -> None:
            if isinstance(val, str):
                # Detect credentials / tokens
                if any(tok in val for tok in ("Bearer ", "e7sk_", "-----BEGIN PRIVATE KEY")):
                    raise ValueError(
                        "notification_context contains prohibited credential or secret value."
                    )
                # Detect SSN pattern
                if re.search(r"\b\d{3}-\d{2}-\d{4}\b", val):
                    raise ValueError(
                        "notification_context contains prohibited Social Security Number (SSN)."
                    )
                # Detect raw MySQL connection strings
                if "mysql://" in val or "mysql+pymysql://" in val:
                    raise ValueError(
                        "notification_context contains raw MySQL database connection URL."
                    )
                # Detect plaintext email addresses inside context values
                if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b", val):
                    msg = f"notification_context contains prohibited plaintext email: '{val}'."
                    raise ValueError(msg)
                # Detect plaintext phone numbers inside context values
                digits_only = re.sub(r"[\s\-\(\)\.]", "", val)
                val_intl = (
                    digits_only.startswith("+")
                    and digits_only[1:].isdigit()
                    and len(digits_only) >= 8
                )
                val_local = digits_only.isdigit() and len(digits_only) >= 10
                if val_intl or val_local:
                    msg = f"notification_context contains prohibited plaintext phone: '{val}'."
                    raise ValueError(msg)

        def _traverse(data: Any) -> None:
            if isinstance(data, dict):
                if "_sa_instance_state" in data:
                    raise ValueError(
                        "Raw database model state cannot be passed in notification_context."
                    )
                for k, val in data.items():
                    _check_key(str(k))
                    _check_value(val)
                    _traverse(val)
            elif isinstance(data, list):
                for item in data:
                    _check_value(item)
                    _traverse(item)

        _traverse(v)
        return v


__all__ = [
    "ALLOWED_TEMPLATE_CONTEXT_FIELDS",
    "NotificationCommand",
    "NotificationTrigger",
    "PROHIBITED_CONTEXT_KEYWORDS",
    "TriggerKind",
]
