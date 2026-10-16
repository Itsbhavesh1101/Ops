"""app.api.v1 package."""

from app.api.v1.analytics import create_analytics_router
from app.api.v1.audit import create_audit_router
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
    api_router,
    health_check,
    readiness_check,
    router,
)

__all__ = [
    "API_V1_NAME",
    "API_V1_PREFIX",
    "JobFailureResponse",
    "JobReadResponse",
    "JobSnapshotReader",
    "OPENAPI_DESCRIPTION",
    "OPENAPI_TITLE",
    "OPENAPI_VERSION",
    "api_router",
    "create_analytics_router",
    "create_audit_router",
    "create_job_read_router",
    "health_check",
    "job_snapshot_to_response",
    "readiness_check",
    "router",
]
