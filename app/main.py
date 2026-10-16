from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_api_exception_handlers
from app.api.idempotency import register_idempotency_exception_handler
from app.api.v1.calendar import register_calendar_routes
from app.api.v1.composition import register_domain_routes
from app.api.v1.router import (
    OPENAPI_DESCRIPTION,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
)
from app.api.v1.router import (
    router as api_v1_router,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import (
    CorrelationAndLoggingMiddleware,
    RequestBodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.telemetry import (
    TelemetryRuntime,
    TelemetrySettings,
    initialize_telemetry,
    instrument_fastapi_app,
    resolve_telemetry_settings,
    shutdown_telemetry,
    uninstrument_fastapi_app,
)

logger = structlog.get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan managing deterministic startup and shutdown handlers."""
    settings = get_settings()
    setup_logging(settings)

    # 1. Resolve telemetry settings for the API process (canonical identity: e7-api)
    tel_settings: TelemetrySettings = getattr(
        app.state, "telemetry_settings", None
    ) or resolve_telemetry_settings(default_service_name="e7-api")

    # 2. Initialize OpenTelemetry runtime at the process lifecycle boundary
    telemetry_initializer = getattr(app.state, "telemetry_initializer", initialize_telemetry)
    telemetry_shutdown_fn = getattr(app.state, "telemetry_shutdown", shutdown_telemetry)

    telemetry_runtime: TelemetryRuntime | None = None
    try:
        telemetry_runtime = telemetry_initializer(
            tel_settings,
            environment=settings.environment,
        )
        app.state.telemetry_runtime = telemetry_runtime
    except BaseException as exc:
        logger.error(
            "api_telemetry_startup_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    # 3. If telemetry runtime is enabled, instrument FastAPI app
    instrumentor: Any = None
    if telemetry_runtime and telemetry_runtime.enabled:
        instrumentor = getattr(app.state, "fastapi_instrumentor", None)
        if instrumentor is None:
            instrumentor = instrument_fastapi_app(
                app, tracer_provider=telemetry_runtime.tracer_provider
            )
            app.state.fastapi_instrumentor = instrumentor

    # 4. Application serves requests
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # 5. Clean up FastAPI instrumentation
        if instrumentor is not None:
            uninstrument_fastapi_app(app, instrumentor)
            if hasattr(app.state, "fastapi_instrumentor"):
                delattr(app.state, "fastapi_instrumentor")

        # 6. Telemetry flush and shutdown
        try:
            telemetry_shutdown_fn()
        except BaseException as cleanup_exc:
            logger.error(
                "api_telemetry_shutdown_failed",
                error_type=type(cleanup_exc).__name__,
                error=str(cleanup_exc),
            )
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(
                        f"Secondary failure during telemetry shutdown: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            else:
                raise


settings = get_settings()

app = FastAPI(
    title=OPENAPI_TITLE,
    description=OPENAPI_DESCRIPTION,
    version=OPENAPI_VERSION,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)

# Register standardized API error handlers (Task 1I.1)
register_api_exception_handlers(app)

# Register idempotency conflict handler (Task 1I.3)
register_idempotency_exception_handler(app)

# Defensive security response headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Request body size limit middleware
app.add_middleware(RequestBodySizeLimitMiddleware)

# Correlation and structured request logging middleware
app.add_middleware(CorrelationAndLoggingMiddleware)

# Configuration-driven CORS policy (strict isolation: wildcard origins cannot use credentials)
if settings.cors_allowed_origins:
    allow_credentials = "*" not in settings.cors_allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

if settings.mount_domain_routers:
    register_domain_routes(api_v1_router)

register_calendar_routes(api_v1_router)
app.include_router(api_v1_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "engine": settings.app_name,
        "status": "online",
        "docs": "/docs",
    }
