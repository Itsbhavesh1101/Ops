import asyncio
import os
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

    # Validate production required deployment attributes at startup (Task 2)
    if settings.environment in ("production", "staging"):
        if not settings.database_url or not settings.database_url.get_secret_value().strip():
            logger.error(
                "startup_validation_failed_missing_database_url",
                environment=settings.environment,
            )
            raise RuntimeError(
                f"database_url must be configured for {settings.environment} deployment"
            )
        if not settings.mount_domain_routers:
            logger.error(
                "startup_validation_failed_unmounted_domain_routers",
                environment=settings.environment,
            )
            raise RuntimeError(
                f"mount_domain_routers must be enabled for {settings.environment} "
                "Operations Engine deployment"
            )

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

    # 3b. Optional embedded scheduler worker for local/test execution
    worker_task: asyncio.Task[None] | None = None
    stop_worker_event: asyncio.Event | None = None
    if (
        os.environ.get("E7_EMBED_SCHEDULER_WORKER", "").lower() in ("true", "1")
        or settings.environment in ("local", "test")
    ):
        try:
            from app.db.engine import DatabaseManager
            from app.scheduling.config import SchedulerSettings
            from app.scheduling.worker import SchedulerWorker

            sched_settings = SchedulerSettings(
                poll_interval_seconds=float(
                    os.environ.get("E7_SCHEDULER_POLL_INTERVAL_SECONDS", "0.2")
                ),
                claim_batch_size=int(os.environ.get("E7_SCHEDULER_CLAIM_BATCH_SIZE", "5")),
                solve_timeout_seconds=float(
                    os.environ.get("E7_SCHEDULER_SOLVE_TIMEOUT_SECONDS", "30.0")
                ),
                lease_duration_seconds=float(
                    os.environ.get("E7_SCHEDULER_LEASE_DURATION_SECONDS", "60.0")
                ),
                shutdown_grace_seconds=float(
                    os.environ.get("E7_SCHEDULER_SHUTDOWN_GRACE_SECONDS", "5.0")
                ),
            )
            db_mgr = DatabaseManager(settings)
            worker = SchedulerWorker(db_manager=db_mgr, settings=sched_settings)
            stop_worker_event = asyncio.Event()
            worker_task = asyncio.create_task(worker.run(stop_event=stop_worker_event))
            logger.info("embedded_scheduler_worker_started")
        except Exception as exc:
            logger.warning("embedded_scheduler_worker_failed_to_start", error=str(exc))

    # 4. Application serves requests
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Stop embedded scheduler worker if running
        if stop_worker_event is not None and worker_task is not None:
            stop_worker_event.set()
            try:
                await asyncio.wait_for(worker_task, timeout=2.0)
            except Exception:
                pass

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
    allowed_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    allowed_headers = [
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Correlation-ID",
        "Idempotency-Key",
        "X-E7-Service-Key",
        "If-Match",
        "If-None-Match",
        "X-Tenant-ID",
        "X-Request-ID",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=allowed_methods,
        allow_headers=allowed_headers,
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
