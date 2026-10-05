"""Canonical API version 1 router composition module.

Part of Phase 1 Foundation Task 1I.2.
Provides the central aggregation point for all /api/v1 endpoints,
defines immutable API version constants and OpenAPI metadata,
and preserves foundational operational health and readiness probes.
"""

from fastapi import APIRouter

from app.api.v1.schemas import HealthResponse, ReadinessResponse
from app.core.config import get_settings

# Stable canonical API prefix and version identifier
API_V1_PREFIX: str = "/api/v1"
API_V1_NAME: str = "v1"

# Safe static OpenAPI contract metadata
OPENAPI_TITLE: str = "E-7 Operations Engine API"
OPENAPI_VERSION: str = "1.0.0"
OPENAPI_DESCRIPTION: str = "E-7 Operations Engine HTTP API"

# Canonical v1 top-level aggregation router
router = APIRouter(prefix=API_V1_PREFIX)
api_router = router  # Backwards-compatible alias for existing imports


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Process liveness probe."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="operations-engine",
        environment=settings.environment,
    )


@router.get("/ready", response_model=ReadinessResponse, tags=["Health"])
async def readiness_check() -> ReadinessResponse:
    """Service traffic readiness probe.

    Currently returns ready as initial Phase-1 foundation runtime has no external
    stateful infrastructure dependencies yet implemented.
    """
    settings = get_settings()
    return ReadinessResponse(
        status="ready",
        service="operations-engine",
        environment=settings.environment,
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
