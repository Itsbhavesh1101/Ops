"""Canonical provider-neutral asynchronous job contracts.

Part of Phase 1 Foundation Task 1I.4A.
Provides:
- JobId: immutable validated UUIDv4 identifier with server-side generation.
- generate_job_id: factory producing fresh random UUIDv4 JobIds.
- JobType: open namespaced logical operation category (e.g. 'planning.generate').
- JobIdentity: immutable composite identity (job_id + tenant_id + job_type).
- JobFailureCode: open namespaced failure code (e.g. 'job.execution_failed').
- JobFailure: immutable safe failure representation (code + sanitized message).

Invariants & Scope Boundaries:
- Provider-neutral: zero HTTP, zero database, zero broker dependencies.
- Open extensibility: JobType and JobFailureCode do not use closed enums.
- Privacy & Safety: JobFailure message must be explicitly sanitized; never created
  automatically from raw exception str/repr or tracebacks.
- Structural Identity: JobIdentity excludes principal ID, timestamps, status,
  failure metadata, request payloads, and response payloads.
- Absence of Speculative Fields: zero result payloads, result references, progress
  percentages, retry metadata, cancellation tokens, or priority fields.
"""

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

# Bounded lowercase namespaced identifier regex: e.g. 'planning.generate'
JOB_TYPE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_JOB_TYPE_LENGTH: int = 128

JOB_FAILURE_CODE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_JOB_FAILURE_CODE_LENGTH: int = 128

MAX_FAILURE_MESSAGE_LENGTH: int = 1024


def validate_job_id(v: Any) -> uuid.UUID:
    """Validate that input represents a valid, non-nil UUID version 4.

    Invariants:
    - Accepts uuid.UUID or canonical hyphenated UUID string.
    - Rejects nil UUID (00000000-0000-0000-0000-000000000000).
    - Requires UUID version 4 strictly (rejects v1, v3, v5).
    - Rejects strings with whitespace or malformed formatting.
    - Rejects non-UUID and non-string types.
    """
    if isinstance(v, JobId):
        return v.value
    if isinstance(v, uuid.UUID):
        u = v
    elif isinstance(v, str):
        if not v or v.strip() != v or " " in v or "\t" in v or "\n" in v or "\r" in v:
            raise ValueError("JobId must not be empty or contain whitespace")
        try:
            u = uuid.UUID(v)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"JobId is not a valid UUID: '{v}'") from exc
    else:
        raise TypeError(f"JobId must be a UUID or string, got '{type(v).__name__}'")

    if u.int == 0:
        raise ValueError("JobId must not be a nil UUID")
    if u.version != 4:
        raise ValueError(f"JobId must be a UUID version 4, got version {u.version}")
    return u


class JobId(BaseModel):
    """Immutable value object representing a validated UUIDv4 job identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: uuid.UUID

    def __init__(self, value: uuid.UUID | str | Any | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, v: Any) -> uuid.UUID:
        return validate_job_id(v)

    @field_serializer("value")
    def _serialize_value(self, v: uuid.UUID) -> str:
        return str(v).lower()

    def __str__(self) -> str:
        return str(self.value).lower()

    def __repr__(self) -> str:
        return f"JobId('{self.value}')"


def generate_job_id() -> JobId:
    """Generate a fresh, cryptographically random UUIDv4 JobId.

    Server-controlled generation using Python standard library uuid.uuid4().
    """
    return JobId(uuid.uuid4())


def validate_job_type(v: Any) -> str:
    """Validate open namespaced logical job type identifier.

    Invariants:
    - Must be a string.
    - Non-empty and contains no whitespace or control characters.
    - Strictly lowercase (no uppercase characters, no silent lowercasing).
    - Length bounded to <= 128 characters.
    - At least two dot-separated segments (e.g. 'planning.generate').
    - No leading, trailing, or consecutive dots.
    - Each segment must start with a lowercase letter followed by lowercase alphanumeric/underscore.
    """
    if not isinstance(v, str):
        raise TypeError(f"Job type must be a string, got '{type(v).__name__}'")
    if not v:
        raise ValueError("Job type must not be empty")
    if v.strip() != v or " " in v or "\t" in v or "\n" in v or "\r" in v:
        raise ValueError("Job type must not contain whitespace")
    for ch in v:
        code = ord(ch)
        if code < 32 or code == 127:
            raise ValueError(f"Job type contains forbidden control character (ASCII {code})")
    if len(v) > MAX_JOB_TYPE_LENGTH:
        raise ValueError(
            f"Job type exceeds maximum length of {MAX_JOB_TYPE_LENGTH} characters (got {len(v)})"
        )
    if any(c.isupper() for c in v):
        raise ValueError(f"Job type '{v}' must be strictly lowercase")
    if v.startswith("."):
        raise ValueError(f"Job type '{v}' must not have a leading dot")
    if v.endswith("."):
        raise ValueError(f"Job type '{v}' must not have a trailing dot")
    if ".." in v:
        raise ValueError(f"Job type '{v}' must not have consecutive dots")
    if "." not in v:
        raise ValueError(
            f"Job type '{v}' must contain at least two dot-separated segments "
            "(e.g. 'planning.generate')"
        )
    if not JOB_TYPE_REGEX.fullmatch(v):
        raise ValueError(
            f"Job type '{v}' is invalid. Must be a lowercase namespaced identifier "
            "with at least two segments (e.g. 'planning.generate')"
        )
    return v


class JobType(BaseModel):
    """Immutable value object representing an open namespaced job type identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_job_type(v)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"JobType('{self.value}')"


class JobIdentity(BaseModel):
    """Canonical immutable tenant-scoped asynchronous job identity.

    Fields exactly:
    - job_id: Canonical JobId (UUIDv4).
    - tenant_id: Authenticated tenant context owning the job.
    - job_type: Namespaced logical operation category.

    Security & Scope Invariants:
    - Excludes principal ID: Jobs represent tenant-level operational state.
    - Excludes transport artifacts: Contains zero HTTP URLs, status codes, or headers.
    - Excludes execution state: Contains zero status, failure, or timestamp data.
    - Tenant identifier must come strictly from trusted context; never inferred from headers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: JobId
    tenant_id: str
    job_type: JobType

    def __init__(
        self,
        job_id: JobId | uuid.UUID | str | None = None,
        tenant_id: str | None = None,
        job_type: JobType | str | None = None,
        **data: Any,
    ) -> None:
        if job_id is not None and "job_id" not in data:
            data["job_id"] = job_id
        if tenant_id is not None and "tenant_id" not in data:
            data["tenant_id"] = tenant_id
        if job_type is not None and "job_type" not in data:
            data["job_type"] = job_type
        super().__init__(**data)

    @field_validator("job_id", mode="before")
    @classmethod
    def _validate_job_id(cls, v: Any) -> JobId:
        if isinstance(v, JobId):
            return v
        if isinstance(v, (uuid.UUID, str)):
            return JobId(v)
        raise TypeError(f"job_id must be a JobId, UUID, or string, got '{type(v).__name__}'")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _validate_tenant_id(cls, v: Any) -> str:
        if hasattr(v, "tenant_id") and isinstance(v.tenant_id, str):
            v = v.tenant_id
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

    @field_validator("job_type", mode="before")
    @classmethod
    def _validate_job_type(cls, v: Any) -> JobType:
        if isinstance(v, JobType):
            return v
        if isinstance(v, str):
            return JobType(v)
        raise TypeError(f"job_type must be a JobType or string, got '{type(v).__name__}'")

    @field_serializer("job_id")
    def _serialize_job_id(self, v: JobId) -> str:
        return str(v)

    @field_serializer("job_type")
    def _serialize_job_type(self, v: JobType) -> str:
        return str(v)


def validate_job_failure_code(v: Any) -> str:
    """Validate open namespaced job failure code identifier.

    Invariants:
    - Must be a string.
    - Non-empty and contains no whitespace or control characters.
    - Strictly lowercase (no uppercase characters, no silent lowercasing).
    - Length bounded to <= 128 characters.
    - At least two dot-separated segments (e.g. 'job.execution_failed').
    - No leading, trailing, or consecutive dots.
    - Each segment must start with a lowercase letter followed by lowercase alphanumeric/underscore.
    """
    if not isinstance(v, str):
        raise TypeError(f"Job failure code must be a string, got '{type(v).__name__}'")
    if not v:
        raise ValueError("Job failure code must not be empty")
    if v.strip() != v or " " in v or "\t" in v or "\n" in v or "\r" in v:
        raise ValueError("Job failure code must not contain whitespace")
    for ch in v:
        code = ord(ch)
        if code < 32 or code == 127:
            raise ValueError(
                f"Job failure code contains forbidden control character (ASCII {code})"
            )
    if len(v) > MAX_JOB_FAILURE_CODE_LENGTH:
        raise ValueError(
            f"Job failure code exceeds maximum length of {MAX_JOB_FAILURE_CODE_LENGTH} characters "
            f"(got {len(v)})"
        )
    if any(c.isupper() for c in v):
        raise ValueError(f"Job failure code '{v}' must be strictly lowercase")
    if v.startswith("."):
        raise ValueError(f"Job failure code '{v}' must not have a leading dot")
    if v.endswith("."):
        raise ValueError(f"Job failure code '{v}' must not have a trailing dot")
    if ".." in v:
        raise ValueError(f"Job failure code '{v}' must not have consecutive dots")
    if "." not in v:
        raise ValueError(
            f"Job failure code '{v}' must contain at least two dot-separated segments "
            "(e.g. 'job.execution_failed')"
        )
    if not JOB_FAILURE_CODE_REGEX.fullmatch(v):
        raise ValueError(
            f"Job failure code '{v}' is invalid. Must be a lowercase namespaced identifier "
            "with at least two segments (e.g. 'job.execution_failed')"
        )
    return v


class JobFailureCode(BaseModel):
    """Immutable value object representing an open namespaced failure code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_job_failure_code(v)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"JobFailureCode('{self.value}')"


def validate_failure_message(v: Any) -> str:
    """Validate that a failure message is non-empty and within bounded length.

    Invariants:
    - Must be a string.
    - Non-empty and not purely whitespace.
    - Length bounded to <= 1024 characters.
    - Must be explicitly sanitized by caller; never auto-populated from raw exception str/repr.
    """
    if not isinstance(v, str):
        raise TypeError(f"Failure message must be a string, got '{type(v).__name__}'")
    stripped = v.strip()
    if not stripped:
        raise ValueError("Failure message must not be empty or whitespace-only")
    if len(stripped) > MAX_FAILURE_MESSAGE_LENGTH:
        raise ValueError(
            f"Failure message exceeds maximum length of {MAX_FAILURE_MESSAGE_LENGTH} characters "
            f"(got {len(stripped)})"
        )
    return stripped


class JobFailure(BaseModel):
    """Immutable value object representing sanitized job failure metadata.

    Fields exactly:
    - code: Validated JobFailureCode.
    - message: Sanitized, human-readable failure explanation (<= 1024 chars).

    Safety Invariants:
    - Must NOT be constructed from raw exception str/repr or stack traces.
    - Contains zero database error strings, SQL queries, credentials, or internal payload dumps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: JobFailureCode
    message: str

    def __init__(
        self,
        code: JobFailureCode | str | None = None,
        message: str | None = None,
        **data: Any,
    ) -> None:
        if code is not None and "code" not in data:
            data["code"] = code
        if message is not None and "message" not in data:
            data["message"] = message
        super().__init__(**data)

    @field_validator("code", mode="before")
    @classmethod
    def _validate_code(cls, v: Any) -> JobFailureCode:
        if isinstance(v, JobFailureCode):
            return v
        if isinstance(v, str):
            return JobFailureCode(v)
        raise TypeError(f"code must be a JobFailureCode or string, got '{type(v).__name__}'")

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: Any) -> str:
        return validate_failure_message(v)

    @field_serializer("code")
    def _serialize_code(self, v: JobFailureCode) -> str:
        return str(v)


__all__ = [
    "MAX_FAILURE_MESSAGE_LENGTH",
    "MAX_JOB_FAILURE_CODE_LENGTH",
    "MAX_JOB_TYPE_LENGTH",
    "JobFailure",
    "JobFailureCode",
    "JobId",
    "JobIdentity",
    "JobType",
    "generate_job_id",
    "validate_failure_message",
    "validate_job_failure_code",
    "validate_job_id",
    "validate_job_type",
]
