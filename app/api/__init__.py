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
from app.api.v1.jobs import (
    JobFailureResponse,
    JobReadResponse,
    JobSnapshotReader,
    create_job_read_router,
    job_snapshot_to_response,
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
    "JobFailureResponse",
    "JobReadResponse",
    "JobSnapshotReader",
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
    "create_job_read_router",
    "idempotency_conflict_handler",
    "job_snapshot_to_response",
    "register_api_exception_handlers",
    "register_idempotency_exception_handler",
    "resolve_idempotency_key_digest",
    "validate_error_code",
]
