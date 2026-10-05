"""Standardized API error contracts and safe FastAPI exception handlers.

Part of Phase 1 Foundation Task 1I.1.
Establishes the canonical, version-independent HTTP error representation for the E-7 API:
- Canonical nested error response envelope:
  {"error": {"code": ..., "message": ..., "correlation_id": ..., "details": ...}}.
- Machine-readable bounded namespaced error codes (^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$).
- Safe field-validation detail model with strict privacy minimization (zero raw input echoing).
- Provider-neutral ApiError exception for safe domain-to-API presentation.
- Exception handlers for ApiError, RequestValidationError, StarletteHTTPException (404/405/etc.),
  and generic unhandled Exception (safe 500).
- Automatic correlation ID resolution reusing existing Task-1B infrastructure.
- Explicit, idempotent exception handler registration via register_api_exception_handlers(app).
- Zero database, messaging, or business logic dependencies.
"""

import http
import re
from collections.abc import Sequence
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.core.correlation import get_correlation_id, validate_correlation_id
from app.core.middleware import resolve_route_template

logger = structlog.get_logger(__name__)

# Bounded lowercase namespaced error code pattern: e.g. "request.validation_failed"
ERROR_CODE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_ERROR_CODE_LENGTH: int = 128
MAX_ERROR_MESSAGE_LENGTH: int = 1024


def validate_error_code(v: Any) -> str:
    """Validate a bounded, lowercase namespaced machine-readable error code.

    Invariants:
    - Must be a string.
    - Non-empty and contains no whitespace or control characters.
    - Strictly lowercase (no uppercase characters, no silent lowercasing).
    - Length bounded to <= 128 characters.
    - At least two dot-separated segments (e.g. 'request.not_found').
    - No leading, trailing, or consecutive dots.
    - Each segment must start with a lowercase letter followed by lowercase alphanumeric/underscore.
    """
    if not isinstance(v, str):
        raise TypeError(f"Error code must be a string, got '{type(v).__name__}'")
    if not v:
        raise ValueError("Error code must not be empty")
    if v.strip() != v or " " in v or "\t" in v or "\n" in v or "\r" in v:
        raise ValueError("Error code must not contain whitespace")
    if len(v) > MAX_ERROR_CODE_LENGTH:
        raise ValueError(
            f"Error code exceeds maximum length of {MAX_ERROR_CODE_LENGTH} characters "
            f"(got {len(v)})"
        )
    if any(c.isupper() for c in v):
        raise ValueError(f"Error code '{v}' must be strictly lowercase")
    if v.startswith("."):
        raise ValueError(f"Error code '{v}' must not have a leading dot")
    if v.endswith("."):
        raise ValueError(f"Error code '{v}' must not have a trailing dot")
    if ".." in v:
        raise ValueError(f"Error code '{v}' must not have consecutive dots")
    if "." not in v:
        raise ValueError(
            f"Error code '{v}' must contain at least two dot-separated segments "
            "(e.g. 'request.validation_failed')"
        )
    if not ERROR_CODE_REGEX.fullmatch(v):
        raise ValueError(
            f"Error code '{v}' is invalid. Must be a lowercase namespaced identifier "
            "with at least two segments (e.g. 'request.validation_failed')"
        )
    return v


class FoundationErrorCode:
    """Canonical foundation-owned error code constants."""

    REQUEST_VALIDATION_FAILED: str = "request.validation_failed"
    REQUEST_NOT_FOUND: str = "request.not_found"
    REQUEST_METHOD_NOT_ALLOWED: str = "request.method_not_allowed"
    REQUEST_UNSUPPORTED_MEDIA_TYPE: str = "request.unsupported_media_type"
    INTERNAL_SERVER_ERROR: str = "internal.server_error"


class ErrorCode(BaseModel):
    """Immutable value object representing a validated machine-readable error code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str

    def __init__(self, value: str | None = None, **data: Any) -> None:
        if value is not None and "value" not in data:
            data["value"] = value
        super().__init__(**data)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any) -> str:
        return validate_error_code(v)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"ErrorCode('{self.value}')"


class ApiErrorDetail(BaseModel):
    """Immutable structural detail for field-level or parameter-level API errors.

    Privacy Invariant:
    - Never stores or echoes raw input payloads, passwords, tokens, or arbitrary context dicts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    location: tuple[str | int, ...]
    code: str
    message: str

    @field_validator("location", mode="before")
    @classmethod
    def _validate_location(cls, v: Any) -> tuple[str | int, ...]:
        if isinstance(v, (list, tuple)):
            clean_loc: list[str | int] = []
            for item in v:
                if isinstance(item, (str, int)):
                    clean_loc.append(item)
                else:
                    clean_loc.append(str(item))
            return tuple(clean_loc)
        raise TypeError(f"location must be a list or tuple, got '{type(v).__name__}'")

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Detail code must be a non-empty string")
        return v.strip()

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Detail message must be a non-empty string")
        return v.strip()


class ApiErrorBody(BaseModel):
    """Canonical error payload body.

    Invariants:
    - code: Validated machine-readable lowercase namespaced error code.
    - message: Safe public human-readable text.
    - correlation_id: Request correlation identifier.
    - details: Optional tuple of structural validation/field details (omitted when None).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    correlation_id: str
    details: tuple[ApiErrorDetail, ...] | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _validate_code(cls, v: Any) -> str:
        if isinstance(v, ErrorCode):
            return v.value
        return validate_error_code(v)

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("message must be a non-empty string")
        if len(v) > MAX_ERROR_MESSAGE_LENGTH:
            raise ValueError(
                f"message exceeds maximum length of {MAX_ERROR_MESSAGE_LENGTH} characters "
                f"(got {len(v)})"
            )
        return v.strip()

    @field_validator("correlation_id")
    @classmethod
    def _validate_correlation_id(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("correlation_id must be a non-empty string")
        return v.strip()

    @field_validator("details", mode="before")
    @classmethod
    def _validate_details(cls, v: Any) -> tuple[ApiErrorDetail, ...] | None:
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            return tuple(
                item if isinstance(item, ApiErrorDetail) else ApiErrorDetail(**item) for item in v
            )
        raise TypeError(f"details must be a sequence or None, got '{type(v).__name__}'")


class ApiErrorResponse(BaseModel):
    """Canonical top-level API error response envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: ApiErrorBody


class ApiError(Exception):
    """Domain-neutral exception representing an explicitly exposed API error.

    Invariants:
    - status_code: HTTP error status code in the 400..599 range.
    - code: Validated machine-readable error code.
    - message: Safe public human-readable text.
    - details: Optional sequence of structural ApiErrorDetail objects.
    - Pure presentation metadata: owns zero transaction, persistence, or retry logic.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str | ErrorCode,
        message: str,
        details: Sequence[ApiErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            raise TypeError(f"status_code must be an integer, got '{type(status_code).__name__}'")
        if not (400 <= status_code <= 599):
            raise ValueError(
                f"status_code must be an HTTP error status between 400 and 599 (got {status_code})"
            )

        self.status_code = status_code
        self.code = code.value if isinstance(code, ErrorCode) else validate_error_code(code)

        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        if len(message) > MAX_ERROR_MESSAGE_LENGTH:
            raise ValueError(
                f"message exceeds maximum length of {MAX_ERROR_MESSAGE_LENGTH} characters "
                f"(got {len(message)})"
            )
        self.message = message.strip()

        if details is not None:
            self.details: tuple[ApiErrorDetail, ...] | None = tuple(details)
        else:
            self.details = None

        self.headers = dict(headers) if headers is not None else None

        super().__init__(f"[{self.status_code}] {self.code}: {self.message}")


def resolve_correlation_id(request: Request | None = None) -> str:
    """Resolve active correlation identifier or generate safe fallback.

    Reuses existing Task-1B correlation context and validation infrastructure.
    """
    cid = get_correlation_id()
    if cid:
        return cid
    if request is not None:
        state_cid = getattr(request.state, "correlation_id", None)
        if isinstance(state_cid, str) and state_cid:
            return state_cid
    return validate_correlation_id(None)


def sanitize_validation_errors(exc: RequestValidationError) -> tuple[ApiErrorDetail, ...]:
    """Sanitize Pydantic RequestValidationError items into privacy-safe ApiErrorDetail tuples.

    Privacy invariants:
    - Never includes 'input' (raw user input, passwords, tokens, payloads).
    - Never exposes unvalidated 'ctx' dictionaries.
    - Retains only safe scalar location components, validation error code, and error message.
    """
    sanitized: list[ApiErrorDetail] = []
    for err in exc.errors():
        raw_loc = err.get("loc", ())
        clean_loc: list[str | int] = []
        for part in raw_loc:
            if isinstance(part, (str, int)):
                clean_loc.append(part)
            else:
                clean_loc.append(str(part))

        err_type = str(err.get("type", "invalid"))
        err_msg = str(err.get("msg", "Validation failed."))

        sanitized.append(
            ApiErrorDetail(
                location=tuple(clean_loc),
                code=err_type,
                message=err_msg,
            )
        )
    return tuple(sanitized)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """Handle explicitly raised ApiError presentation exceptions."""
    correlation_id = resolve_correlation_id(request)
    body = ApiErrorBody(
        code=exc.code,
        message=exc.message,
        correlation_id=correlation_id,
        details=exc.details,
    )
    response = ApiErrorResponse(error=body)
    headers = dict(exc.headers) if exc.headers else {}
    headers.setdefault("X-Correlation-ID", correlation_id)
    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle FastAPI RequestValidationError with sanitized details and HTTP 422."""
    correlation_id = resolve_correlation_id(request)
    details = sanitize_validation_errors(exc)
    body = ApiErrorBody(
        code=FoundationErrorCode.REQUEST_VALIDATION_FAILED,
        message="Request validation failed.",
        correlation_id=correlation_id,
        details=details,
    )
    response = ApiErrorResponse(error=body)
    return JSONResponse(
        status_code=422,
        content=response.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": correlation_id},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle Starlette and FastAPI HTTPExceptions with canonical error representations."""
    correlation_id = resolve_correlation_id(request)
    status_code = exc.status_code

    if status_code == 404:
        code = FoundationErrorCode.REQUEST_NOT_FOUND
        message = "Resource not found."
    elif status_code == 405:
        code = FoundationErrorCode.REQUEST_METHOD_NOT_ALLOWED
        message = "Method not allowed."
    elif status_code == 415:
        code = FoundationErrorCode.REQUEST_UNSUPPORTED_MEDIA_TYPE
        message = "Unsupported media type."
    elif status_code >= 500:
        logger.error(
            "http_server_error",
            status_code=status_code,
            route=resolve_route_template(request),
            method=request.method,
            correlation_id=correlation_id,
            exc_info=exc,
        )
        code = FoundationErrorCode.INTERNAL_SERVER_ERROR
        message = "An internal server error occurred."
    else:
        # Standard HTTP phrase mapping or fallback
        if status_code in http.HTTPStatus._value2member_map_:
            phrase = http.HTTPStatus(status_code).phrase
        else:
            phrase = "HTTP Error"
        code = f"http.error_{status_code}"
        message = f"{phrase}."

    body = ApiErrorBody(
        code=code,
        message=message,
        correlation_id=correlation_id,
        details=None,
    )
    response = ApiErrorResponse(error=body)
    headers = dict(exc.headers) if exc.headers else {}
    headers.setdefault("X-Correlation-ID", correlation_id)
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions with structured internal logging and safe generic HTTP 500."""
    correlation_id = resolve_correlation_id(request)

    # Structured internal logging: records exception with traceback
    logger.error(
        "unhandled_server_error",
        route=resolve_route_template(request),
        method=request.method,
        correlation_id=correlation_id,
        exc_info=exc,
    )

    body = ApiErrorBody(
        code=FoundationErrorCode.INTERNAL_SERVER_ERROR,
        message="An internal server error occurred.",
        correlation_id=correlation_id,
        details=None,
    )
    response = ApiErrorResponse(error=body)
    return JSONResponse(
        status_code=500,
        content=response.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": correlation_id},
    )


def register_api_exception_handlers(app: FastAPI) -> None:
    """Explicitly register canonical API exception handlers on a FastAPI application.

    Idempotent: safe to invoke multiple times without registering duplicate handlers.
    Does not execute at import time.
    """
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(FastAPIHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "ERROR_CODE_REGEX",
    "MAX_ERROR_CODE_LENGTH",
    "MAX_ERROR_MESSAGE_LENGTH",
    "ApiError",
    "ApiErrorBody",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ErrorCode",
    "FoundationErrorCode",
    "api_error_handler",
    "http_exception_handler",
    "register_api_exception_handlers",
    "resolve_correlation_id",
    "sanitize_validation_errors",
    "unhandled_exception_handler",
    "validate_error_code",
    "validation_error_handler",
]
