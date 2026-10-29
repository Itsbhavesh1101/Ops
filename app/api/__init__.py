"""app.api package."""

from app.api.errors import (
    ApiError,
    ApiErrorBody,
    ApiErrorDetail,
    ApiErrorResponse,
    ErrorCode,
    FoundationErrorCode,
    register_api_exception_handlers,
    validate_error_code,
)
from app.api.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    HttpIdempotencyContext,
    IdempotencyErrorCode,
    build_http_idempotency_identity,
    idempotency_conflict_handler,
    register_idempotency_exception_handler,
    resolve_idempotency_key_digest,
)
from app.api.v1.router import (
    API_V1_NAME,
    API_V1_PREFIX,
    OPENAPI_DESCRIPTION,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
)

__all__ = [
    "API_V1_NAME",
    "API_V1_PREFIX",
    "IDEMPOTENCY_KEY_HEADER",
    "OPENAPI_DESCRIPTION",
    "OPENAPI_TITLE",
    "OPENAPI_VERSION",
    "ApiError",
    "ApiErrorBody",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ErrorCode",
    "FoundationErrorCode",
    "HttpIdempotencyContext",
    "IdempotencyErrorCode",
    "build_http_idempotency_identity",
    "idempotency_conflict_handler",
    "register_api_exception_handlers",
    "register_idempotency_exception_handler",
    "resolve_idempotency_key_digest",
    "validate_error_code",
]
