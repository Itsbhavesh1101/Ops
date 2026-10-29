"""app.api.v1 package."""

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
    "OPENAPI_DESCRIPTION",
    "OPENAPI_TITLE",
    "OPENAPI_VERSION",
    "api_router",
    "health_check",
    "readiness_check",
    "router",
]
