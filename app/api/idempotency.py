"""HTTP Idempotency-Key boundary and conflict presentation.

Part of Phase 1 Foundation Task 1I.3.
Provides:
- IDEMPOTENCY_KEY_HEADER canonical header name constant ("Idempotency-Key").
- resolve_idempotency_key_digest: extracts, validates, and immediately SHA-256 digests
  the incoming client key, rejecting missing and duplicate header occurrences.
- build_http_idempotency_identity: constructs canonical IdempotencyIdentity from trusted
  tenant context, logical operation name, and key digest.
- HttpIdempotencyContext: immutable container holding canonical identity and optional
  caller-provided RequestFingerprint.
- idempotency_conflict_handler: maps IdempotencyConflictError to canonical HTTP 409
  ApiError presentation reusing the frozen Task-1I.1 response envelope.
- register_idempotency_exception_handler: explicit FastAPI exception handler registration.

Invariants & Scope Boundaries:
- Privacy: The raw client key is strictly transient; never retained in request.state,
  models, dataclasses, exceptions, logs, or error responses.
- Security: Tenant identity must come exclusively from trusted caller context; never
  derived from request headers (such as X-Tenant-ID), query params, path, or body.
- Payload Independence: Does not read request.body(), hash arbitrary payloads, or perform
  JSON canonicalization.
- Response Policy: Task 1H froze IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN.
  Neither EXECUTED nor ALREADY_PROCESSED is mapped to a generic HTTP status code.
- Zero database or messaging dependencies.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from app.api.errors import ApiError, api_error_handler
from app.core.auth.models import TenantContext
from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    IdempotencyKeyDigest,
    OperationName,
    RequestFingerprint,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.core.idempotency.hashing import (
    digest_idempotency_key,
    validate_raw_idempotency_key,
)

IDEMPOTENCY_KEY_HEADER: str = "Idempotency-Key"


class IdempotencyErrorCode:
    """Canonical machine-readable error codes for HTTP idempotency."""

    KEY_REQUIRED: str = "idempotency.key_required"
    KEY_INVALID: str = "idempotency.key_invalid"
    KEY_AMBIGUOUS: str = "idempotency.key_ambiguous"
    CONFLICT: str = "idempotency.conflict"


def resolve_idempotency_key_digest(request: Request) -> IdempotencyKeyDigest:
    """Extract, validate, and SHA-256 digest the Idempotency-Key header from an HTTP request.

    Invariants:
    - Extracts header occurrences using request.headers.getlist("Idempotency-Key").
    - Zero occurrences: raises ApiError(status_code=400, code="idempotency.key_required",
      message="Idempotency-Key header is required.").
    - More than one occurrence: raises ApiError(status_code=400, code="idempotency.key_ambiguous",
      message="Multiple Idempotency-Key headers are not allowed.").
    - Exactly one occurrence: validates raw key via validate_raw_idempotency_key.
      On validation failure: raises ApiError(status_code=400, code="idempotency.key_invalid",
      message="Idempotency-Key header is invalid.").
    - Valid raw key: digests immediately via digest_idempotency_key and discards raw key.
    - Raw key is never stored in request.state, logs, or error responses.
    - Zero database access, zero body reading, zero tenant extraction.
    """
    raw_keys = request.headers.getlist(IDEMPOTENCY_KEY_HEADER)
    if not raw_keys:
        raise ApiError(
            status_code=400,
            code=IdempotencyErrorCode.KEY_REQUIRED,
            message="Idempotency-Key header is required.",
        )
    if len(raw_keys) > 1:
        raise ApiError(
            status_code=400,
            code=IdempotencyErrorCode.KEY_AMBIGUOUS,
            message="Multiple Idempotency-Key headers are not allowed.",
        )

    raw_key = raw_keys[0]
    try:
        validate_raw_idempotency_key(raw_key)
    except (TypeError, ValueError):
        raise ApiError(
            status_code=400,
            code=IdempotencyErrorCode.KEY_INVALID,
            message="Idempotency-Key header is invalid.",
        ) from None

    return digest_idempotency_key(raw_key)


def build_http_idempotency_identity(
    *,
    tenant_id: str | TenantContext,
    operation_name: str | OperationName,
    key_digest: IdempotencyKeyDigest,
) -> IdempotencyIdentity:
    """Construct canonical IdempotencyIdentity from trusted tenant, operation, and digest.

    Invariants:
    - tenant_id: strictly provided by caller from authenticated/trusted context.
      Never derived from request headers (e.g. X-Tenant-ID), query params, path, or body.
    - operation_name: validated namespaced OperationName or valid str.
    - key_digest: validated IdempotencyKeyDigest.
    - Zero raw key, zero principal ID, zero HTTP method or path.
    """
    clean_tenant_id: str
    if isinstance(tenant_id, TenantContext):
        clean_tenant_id = tenant_id.tenant_id
    elif isinstance(tenant_id, str):
        clean_tenant_id = tenant_id
    else:
        raise TypeError(
            f"tenant_id must be a string or TenantContext, got '{type(tenant_id).__name__}'"
        )

    clean_op_name = (
        operation_name.value if isinstance(operation_name, OperationName) else operation_name
    )

    if not isinstance(key_digest, IdempotencyKeyDigest):
        raise TypeError(
            f"key_digest must be an IdempotencyKeyDigest instance, "
            f"got '{type(key_digest).__name__}'"
        )

    return IdempotencyIdentity(
        tenant_id=clean_tenant_id,
        operation_name=clean_op_name,
        key_digest=key_digest,
    )


class HttpIdempotencyContext(BaseModel):
    """Immutable HTTP idempotency context holding canonical identity and optional fingerprint.

    Invariants:
    - Contains zero raw client keys, HTTP request objects, request bodies, credentials,
      or database references.
    - request_fingerprint, if provided, must be an already-validated RequestFingerprint
      supplied by the operation adapter. It is never calculated by this adapter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint | None = None


async def idempotency_conflict_handler(
    request: Request, exc: IdempotencyConflictError
) -> JSONResponse:
    """Handle IdempotencyConflictError by mapping to canonical HTTP 409 ApiError response.

    Reuses the frozen Task-1I.1 api_error_handler seam:
    - Canonical nested error envelope: {"error": {"code": "idempotency.conflict", ...}}.
    - Fixed safe public message: "Idempotency key was already used for a different request."
    - Automatic correlation ID resolution via Task-1B/1I.1 machinery.
    - Zero raw key, body bytes, exception representation, or database leakage.
    """
    api_error = ApiError(
        status_code=409,
        code=IdempotencyErrorCode.CONFLICT,
        message="Idempotency key was already used for a different request.",
    )
    return await api_error_handler(request, api_error)


def register_idempotency_exception_handler(app: FastAPI) -> None:
    """Register canonical IdempotencyConflictError handler on a FastAPI application.

    Idempotent: safe to invoke multiple times without registering duplicate handlers.
    Does not execute at import time.
    """
    app.add_exception_handler(IdempotencyConflictError, idempotency_conflict_handler)  # type: ignore[arg-type]


__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "HttpIdempotencyContext",
    "IdempotencyErrorCode",
    "build_http_idempotency_identity",
    "idempotency_conflict_handler",
    "register_idempotency_exception_handler",
    "resolve_idempotency_key_digest",
]
