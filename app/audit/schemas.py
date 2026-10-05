"""Immutable Pydantic schemas and contracts for audit event creation.

Part of Phase 1 Foundation Task 1F.
Defines caller-supplied append contracts, event-type validation, identity consistency rules,
and integration with defensive audit details validation.
"""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.audit.validation import validate_audit_details
from app.core.auth.models import AuthenticationMethod, PrincipalType

# Namespaced event type regex: at least two dot-separated segments of lowercase chars
EVENT_TYPE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Canonical resource type regex: dot or underscore-separated lowercase identifier
RESOURCE_TYPE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class AuditEventOutcome(StrEnum):
    """Bounded, provider-neutral outcome classification for audit events."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DENIED = "DENIED"


class AuditEventCreate(BaseModel):
    """Immutable input contract for creating a canonical append-only audit event.

    Invariants:
    - Immutable: frozen=True, extra='forbid'.
    - Primary key: persistence-generated UUID (omitted from input contract;
      callers cannot choose ID).
    - recorded_at is omitted: authoritative timestamp is authored by the database.
    - Identity consistency: system events have null actor (global or tenant-scoped);
      authenticated events require complete canonical actor context
      (tenant_id, actor_principal_type, authentication_method).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, gt=0)

    tenant_id: str | None = None

    actor_principal_id: str | None = None
    actor_principal_type: str | None = None
    authentication_method: str | None = None

    event_type: str
    resource_type: str | None = None
    resource_id: str | None = None

    outcome: AuditEventOutcome | str | None = None
    correlation_id: str | None = None

    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned:
            raise ValueError("event_type must not be empty or whitespace-only")
        if len(cleaned) > 128:
            raise ValueError("event_type must not exceed 128 characters")
        if not EVENT_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"event_type '{cleaned}' is invalid; must be lowercase dot-separated segments "
                "with at least two segments (e.g. 'auth.access.denied')"
            )
        return cleaned

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("tenant_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("tenant_id must not exceed 255 characters")
        return cleaned

    @field_validator("actor_principal_id")
    @classmethod
    def validate_actor_principal_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("actor_principal_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("actor_principal_id must not exceed 255 characters")
        return cleaned

    @field_validator("actor_principal_type")
    @classmethod
    def validate_actor_principal_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip() if isinstance(v, str) else str(v)
        allowed = {pt.value for pt in PrincipalType}
        if cleaned not in allowed:
            opts = sorted(allowed)
            raise ValueError(f"actor_principal_type must be one of {opts}, got '{cleaned}'")
        return cleaned

    @field_validator("authentication_method")
    @classmethod
    def validate_authentication_method(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip() if isinstance(v, str) else str(v)
        allowed = {am.value for am in AuthenticationMethod}
        if cleaned not in allowed:
            raise ValueError(
                f"authentication_method must be one of {sorted(allowed)}, got '{cleaned}'"
            )
        return cleaned

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("resource_type must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("resource_type must not exceed 128 characters")
        if not RESOURCE_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"resource_type '{cleaned}' must be a lowercase dot/underscore identifier; "
                "URLs, SQL, and whitespace are prohibited"
            )
        return cleaned

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("resource_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("resource_id must not exceed 255 characters")
        return cleaned

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: AuditEventOutcome | str | None) -> str | None:
        if v is None:
            return None
        raw = v.value if isinstance(v, AuditEventOutcome) else str(v).strip()
        allowed = {o.value for o in AuditEventOutcome}
        if raw not in allowed:
            raise ValueError(f"outcome must be one of {sorted(allowed)}, got '{raw}'")
        return raw

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("correlation_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("correlation_id must not exceed 128 characters")
        return cleaned

    @field_validator("details")
    @classmethod
    def validate_details_content(cls, v: dict[str, Any]) -> dict[str, Any]:
        return validate_audit_details(v)

    @model_validator(mode="after")
    def validate_actor_consistency(self) -> "AuditEventCreate":
        if self.actor_principal_id is None:
            if self.actor_principal_type is not None:
                raise ValueError(
                    "actor_principal_type must be None for system events (no principal ID)"
                )
            if self.authentication_method is not None:
                raise ValueError(
                    "authentication_method must be None for system events (no principal ID)"
                )
        else:
            if self.tenant_id is None:
                raise ValueError("tenant_id is required when actor_principal_id is present")
            if self.actor_principal_type is None:
                raise ValueError(
                    "actor_principal_type is required when actor_principal_id is present"
                )
            if self.authentication_method is None:
                raise ValueError(
                    "authentication_method is required when actor_principal_id is present"
                )
        return self
