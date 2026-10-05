"""Canonical transport-neutral contracts for idempotency identity and request fingerprinting.

Part of Phase 1 Foundation Task 1H.1.
Provides:
- OperationName: provider-neutral logical operation identifier.
- IdempotencyKeyDigest: validated 64-character lowercase SHA-256 digest of client key.
- RequestFingerprint: validated 64-character lowercase SHA-256 digest of canonical request bytes.
- IdempotencyIdentity: immutable composite identity (tenant_id + operation_name + key_digest).
- create_idempotency_identity: factory creating IdempotencyIdentity from raw client key.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

# Namespaced operation name: lowercase alphanumeric dot-separated with >= 2 segments
OPERATION_NAME_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_OPERATION_NAME_LENGTH: int = 128

# Fixed-width 64-character lowercase hexadecimal regex for SHA-256 digests
HEX_64_REGEX: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")


def validate_operation_name(v: Any) -> str:
    """Validate provider-neutral namespaced logical operation name.

    Invariants:
    - Must be a string.
    - Non-empty and contains no whitespace.
    - Strictly lowercase (no uppercase characters, no silent lowercasing).
    - Length bounded to <= 128 characters.
    - At least two dot-separated segments (e.g. 'tasks.create').
    - No leading, trailing, or consecutive dots.
    - Each segment must start with a lowercase letter followed by lowercase alphanumeric/underscore.
    """
    if not isinstance(v, str):
        raise TypeError(f"Operation name must be a string, got '{type(v).__name__}'")
    if not v:
        raise ValueError("Operation name must not be empty")
    if v.strip() != v or " " in v or "\t" in v or "\n" in v or "\r" in v:
        raise ValueError("Operation name must not contain whitespace")
    if len(v) > MAX_OPERATION_NAME_LENGTH:
        raise ValueError(
            f"Operation name exceeds maximum length of {MAX_OPERATION_NAME_LENGTH} characters "
            f"(got {len(v)})"
        )
    if any(c.isupper() for c in v):
        raise ValueError(f"Operation name '{v}' must be strictly lowercase")
    if v.startswith("."):
        raise ValueError(f"Operation name '{v}' must not have a leading dot")
    if v.endswith("."):
        raise ValueError(f"Operation name '{v}' must not have a trailing dot")
    if ".." in v:
        raise ValueError(f"Operation name '{v}' must not have consecutive dots")
    if "." not in v:
        raise ValueError(
            f"Operation name '{v}' must contain at least two dot-separated segments "
            "(e.g. 'tasks.create')"
        )
    if not OPERATION_NAME_REGEX.fullmatch(v):
        raise ValueError(
            f"Operation name '{v}' is invalid. Must be a lowercase namespaced identifier "
            "with at least two segments (e.g. 'tasks.create')"
        )
    return v


def validate_hex_digest(v: Any, label: str = "Digest") -> str:
    """Validate that a value is exactly 64 lowercase hexadecimal characters."""
    if not isinstance(v, str):
        raise TypeError(f"{label} must be a string, got '{type(v).__name__}'")
    if len(v) != 64:
        raise ValueError(f"{label} must be exactly 64 characters (got {len(v)})")
    if not HEX_64_REGEX.fullmatch(v):
        if any(c.isupper() for c in v):
            raise ValueError(f"{label} must be lowercase hexadecimal only")
        raise ValueError(f"{label} contains non-hexadecimal characters")
    return v


class OperationName(BaseModel):
    """Immutable value object representing a validated logical operation name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_operation_name(v)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"OperationName('{self.value}')"


class IdempotencyKeyDigest(BaseModel):
    """Immutable value object representing a validated SHA-256 digest of a client idempotency key.

    Invariants:
    - Exactly 64 lowercase hexadecimal characters.
    - Never stores or exposes the original raw client key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_hex_digest(v, "Idempotency key digest")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"IdempotencyKeyDigest('{self.value}')"


class RequestFingerprint(BaseModel):
    """Immutable value object representing a validated SHA-256 fingerprint of request payload bytes.

    Invariants:
    - Exactly 64 lowercase hexadecimal characters.
    - Used for detecting idempotency payload conflicts (same key + different fingerprint).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_hex_digest(v, "Request fingerprint")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"RequestFingerprint('{self.value}')"


class IdempotencyIdentity(BaseModel):
    """Canonical immutable tenant-scoped idempotency identity.

    Scoped strictly by:
    - tenant_id: The authenticated tenant context owning the mutation.
    - operation_name: The namespaced business operation (e.g. 'tasks.create').
    - key_digest: The SHA-256 hex digest of the client-supplied idempotency key.

    Security & Scope Invariants:
    - Excludes principal ID: Idempotency protects tenant-level business mutation. Replays by
      any authorized principal within the same tenant for the same key and operation
      resolve identically.
    - Excludes raw key: Only fixed-width SHA-256 digest is retained, preventing accidental
      token leakage.
    - Excludes transport artifacts: Contains zero HTTP paths, HTTP methods, status
      codes, or headers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    operation_name: str
    key_digest: IdempotencyKeyDigest

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise TypeError(f"tenant_id must be a string, got '{type(v).__name__}'")
        stripped = v.strip()
        if not stripped:
            raise ValueError("tenant_id must not be empty or whitespace-only")
        if len(stripped) > 128:
            raise ValueError(
                f"tenant_id exceeds maximum length of 128 characters (got {len(stripped)})"
            )
        return stripped

    @field_validator("operation_name", mode="before")
    @classmethod
    def _validate_operation_name(cls, v: Any) -> str:
        if isinstance(v, OperationName):
            return v.value
        return validate_operation_name(v)

    @field_validator("key_digest", mode="before")
    @classmethod
    def _validate_key_digest(cls, v: Any) -> IdempotencyKeyDigest:
        if isinstance(v, IdempotencyKeyDigest):
            return v
        if isinstance(v, str):
            return IdempotencyKeyDigest(v)
        raise TypeError(
            f"key_digest must be an IdempotencyKeyDigest or 64-character hex string, "
            f"got '{type(v).__name__}'"
        )

    @field_serializer("key_digest")
    def _serialize_key_digest(self, v: IdempotencyKeyDigest) -> str:
        return str(v)

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        operation_name: str | OperationName,
        raw_key: str,
    ) -> "IdempotencyIdentity":
        """Factory method constructing IdempotencyIdentity from a raw client key.

        The raw key is validated, digested via SHA-256, and discarded immediately.
        """
        # Imported lazily to avoid circular module dependency
        from app.core.idempotency.hashing import digest_idempotency_key

        digest = digest_idempotency_key(raw_key)
        op_name_str = (
            operation_name.value if isinstance(operation_name, OperationName) else operation_name
        )
        return cls(
            tenant_id=tenant_id,
            operation_name=op_name_str,
            key_digest=digest,
        )


def create_idempotency_identity(
    *,
    tenant_id: str,
    operation_name: str | OperationName,
    raw_key: str,
) -> IdempotencyIdentity:
    """Convenience factory function creating an IdempotencyIdentity from a raw client key."""
    return IdempotencyIdentity.create(
        tenant_id=tenant_id,
        operation_name=operation_name,
        raw_key=raw_key,
    )
