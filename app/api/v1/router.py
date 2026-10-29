"""Canonical API version 1 router composition module.

Part of Phase 1 Foundation Task 1I.2.
Provides the central aggregation point for all /api/v1 endpoints,
defines immutable API version constants and OpenAPI metadata,
and preserves foundational operational health and readiness probes.
"""

from typing import Any

from fastapi import APIRouter, Query, Response, status

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
async def readiness_check(
    response: Response,
    deep: bool = Query(default=False, description="Execute deep infrastructure probe"),
    strict: bool = Query(default=False, description="Fail readiness on degraded external broker"),
) -> ReadinessResponse:
    """Service traffic readiness probe.

    Distinguishes:
    - Shallow probe (deep=False): Application startup readiness
      (process running, configuration valid).
    - Deep probe (deep=True): Comprehensive stateful infrastructure verification
      (PostgreSQL connectivity pool, RabbitMQ message broker, bounded timeouts).
    """
    settings = get_settings()

    if not deep:
        return ReadinessResponse(
            status="ready",
            service="operations-engine",
            environment=settings.environment,
            probe="shallow",
        )

    dependencies: dict[str, Any] = {}
    is_ready = True
    is_degraded = False

    # 1. PostgreSQL Connectivity Probe
    if settings.database_url:
        try:
            from app.api.v1.composition import get_shared_db_manager

            db_manager = get_shared_db_manager()
            db_ready = await db_manager.check_readiness(timeout_seconds=2.0)
            if db_ready:
                dependencies["database"] = {
                    "status": "healthy",
                    "pool": db_manager.get_pool_status(),
                }
            else:
                dependencies["database"] = {
                    "status": "unhealthy",
                    "error": "database_connection_failed",
                }
                is_ready = False
        except Exception as exc:
            dependencies["database"] = {
                "status": "unhealthy",
                "error": type(exc).__name__,
            }
            is_ready = False
    else:
        dependencies["database"] = {"status": "not_configured"}

    # 2. RabbitMQ Connectivity Probe
    try:
        import os

        from app.events.health import check_broker_ready

        broker_ready = await check_broker_ready(timeout_seconds=2.0)
        has_broker_config = bool(
            os.getenv("E7_RABBITMQ_URL") or os.getenv("RABBITMQ_URL")
        )
        if has_broker_config:
            if broker_ready:
                dependencies["broker"] = {"status": "healthy"}
            else:
                dependencies["broker"] = {
                    "status": "degraded",
                    "error": "broker_connection_failed",
                }
                is_degraded = True
        else:
            dependencies["broker"] = {"status": "not_configured"}
    except Exception as exc:
        dependencies["broker"] = {
            "status": "degraded",
            "error": type(exc).__name__,
        }
        is_degraded = True

    # 3. Determine Overall Status & Response Code
    if not is_ready or (strict and is_degraded):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall_status = "not_ready"
    elif is_degraded:
        # Documented degraded asynchronous mode: API can still accept writes into outbox
        overall_status = "degraded"
    else:
        overall_status = "ready"

    return ReadinessResponse(
        status=overall_status,
        service="operations-engine",
        environment=settings.environment,
        probe="deep",
        dependencies=dependencies,
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
