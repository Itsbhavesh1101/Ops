"""Provider-neutral security contracts and principal models.

Defines canonical E-7 representations for authentication methods, principal types,
authenticated principals, tenant context, and verified external identities.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


def _clean_str(v: Any, field_name: str) -> str:
    """Validate that a required string is non-empty and not purely whitespace."""
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{field_name} must not be empty or whitespace-only")
    return v.strip()


def _clean_opt_str(v: Any, field_name: str) -> str | None:
    """Validate that an optional string, if provided, is not empty or whitespace-only."""
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{field_name} must not be empty or whitespace-only if provided")
    return v.strip()


def _normalize_string_set(v: Any, field_name: str) -> frozenset[str]:
    """Validate and normalize a set of strings, rejecting empty or whitespace entries."""
    if v is None:
        return frozenset()
    if isinstance(v, str):
        cleaned = v.strip()
        if not cleaned:
            raise ValueError(f"{field_name} entry must not be empty or whitespace-only")
        return frozenset({cleaned})
    if isinstance(v, (set, list, tuple, frozenset)):
        cleaned_items = set()
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{field_name} entries must not be empty or whitespace-only")
            cleaned_items.add(item.strip())
        return frozenset(cleaned_items)
    raise ValueError(f"{field_name} must be an iterable of strings")


def _validate_audiences(v: Any) -> frozenset[str]:
    """Validate and normalize OIDC audience collection, ensuring at least one valid audience."""
    if isinstance(v, str):
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("audiences must not be empty or whitespace-only")
        return frozenset({cleaned})
    if isinstance(v, (set, list, tuple, frozenset)):
        if not v:
            raise ValueError("audiences collection must not be empty")
        cleaned_items = set()
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("audiences entries must not be empty or whitespace-only")
            cleaned_items.add(item.strip())
        if not cleaned_items:
            raise ValueError("audiences collection must not be empty")
        return frozenset(cleaned_items)
    raise ValueError("audiences must be a non-empty string or collection of strings")


class AuthenticationMethod(StrEnum):
    """Bounded set of supported authentication mechanisms."""

    OIDC = "OIDC"
    SERVICE_API_KEY = "SERVICE_API_KEY"


class PrincipalType(StrEnum):
    """Classification of principal: human user vs service/machine account."""

    HUMAN = "HUMAN"
    SERVICE = "SERVICE"


class VerifiedOidcIdentity(BaseModel):
    """Cryptographically verified external OIDC identity token payload.

    Represents identity claims verified by an external identity provider (e.g. Google OIDC)
    BEFORE resolution to an internal E-7 principal. This is NOT yet an authorized E-7 principal
    and carries zero intrinsic tenant authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    issuer: str
    subject: str
    audiences: frozenset[str]
    email: str | None = None
    hosted_domain: str | None = None

    @field_validator("issuer", "subject")
    @classmethod
    def validate_required_strings(cls, v: str, info: ValidationInfo) -> str:
        return _clean_str(v, str(info.field_name))

    @field_validator("audiences", mode="before")
    @classmethod
    def validate_audiences_field(cls, v: Any) -> frozenset[str]:
        return _validate_audiences(v)

    @field_validator("email", "hosted_domain")
    @classmethod
    def validate_optional_strings(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _clean_opt_str(v, str(info.field_name))


class VerifiedServiceCredentialIdentity(BaseModel):
    """Cryptographically verified service credential metadata.

    Represents verified machine/service metadata BEFORE resolution to an internal E-7 principal.
    MUST NEVER contain raw API keys, secrets, or bearer tokens.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    credential_id: str
    service_subject: str
    issuer: str | None = None

    @field_validator("credential_id", "service_subject")
    @classmethod
    def validate_required_strings(cls, v: str, info: ValidationInfo) -> str:
        return _clean_str(v, str(info.field_name))

    @field_validator("issuer")
    @classmethod
    def validate_optional_strings(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _clean_opt_str(v, str(info.field_name))


class TenantContext(BaseModel):
    """Canonical E-7 tenant/organization context.

    Tenant scope must originate strictly from the authenticated E-7 principal or approved
    identity mapping. It MUST NEVER be inferred from unverified client inputs (e.g. headers,
    URL parameters, or request payloads).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    name: str | None = None

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str, info: ValidationInfo) -> str:
        return _clean_str(v, str(info.field_name))

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _clean_opt_str(v, str(info.field_name))


class AuthenticatedPrincipal(BaseModel):
    """Canonical provider-neutral E-7 authenticated principal.

    Represents an authorized internal identity possessing an immutable tenant scope and
    deterministic roles and permissions. Does not contain raw tokens, passwords, or secrets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    principal_type: PrincipalType
    authentication_method: AuthenticationMethod
    tenant_id: str
    roles: frozenset[str] = Field(default_factory=frozenset)
    permissions: frozenset[str] = Field(default_factory=frozenset)
    issuer: str | None = None
    external_subject: str | None = None

    @field_validator("principal_id", "tenant_id")
    @classmethod
    def validate_required_identifiers(cls, v: str, info: ValidationInfo) -> str:
        return _clean_str(v, str(info.field_name))

    @field_validator("roles", mode="before")
    @classmethod
    def validate_roles(cls, v: Any) -> frozenset[str]:
        return _normalize_string_set(v, "roles")

    @field_validator("permissions", mode="before")
    @classmethod
    def validate_permissions(cls, v: Any) -> frozenset[str]:
        return _normalize_string_set(v, "permissions")

    @field_validator("issuer", "external_subject")
    @classmethod
    def validate_optional_identifiers(cls, v: str | None, info: ValidationInfo) -> str | None:
        return _clean_opt_str(v, str(info.field_name))

    def has_role(self, role: str) -> bool:
        """Check if the principal possesses a specific role."""
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        """Check if the principal possesses at least one of the specified roles."""
        return bool(self.roles.intersection(roles))

    def has_all_roles(self, *roles: str) -> bool:
        """Check if the principal possesses all of the specified roles."""
        return set(roles).issubset(self.roles)

    def has_permission(self, permission: str) -> bool:
        """Check if the principal possesses a specific permission."""
        return permission in self.permissions

    def has_any_permission(self, *permissions: str) -> bool:
        """Check if the principal possesses at least one of the specified permissions."""
        return bool(self.permissions.intersection(permissions))

    def has_all_permissions(self, *permissions: str) -> bool:
        """Check if the principal possesses all of the specified permissions."""
        return set(permissions).issubset(self.permissions)

    @property
    def tenant_context(self) -> TenantContext:
        """Get the immutable TenantContext for this principal."""
        return TenantContext(tenant_id=self.tenant_id)
