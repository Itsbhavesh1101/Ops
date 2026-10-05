"""OpenTelemetry observability core package for the IR Infotech E-7 Operations Engine."""

from app.core.telemetry.config import TelemetrySettings, resolve_telemetry_settings
from app.core.telemetry.fastapi import instrument_fastapi_app, uninstrument_fastapi_app
from app.core.telemetry.logging import add_otel_trace_context
from app.core.telemetry.outbox import OutboxTelemetry, get_outbox_telemetry
from app.core.telemetry.resource import build_resource
from app.core.telemetry.runtime import (
    TelemetryInitializationError,
    TelemetryRuntime,
    build_telemetry_runtime,
    get_telemetry_runtime,
    initialize_telemetry,
    shutdown_telemetry,
)

__all__ = [
    "TelemetrySettings",
    "resolve_telemetry_settings",
    "TelemetryRuntime",
    "TelemetryInitializationError",
    "build_telemetry_runtime",
    "initialize_telemetry",
    "get_telemetry_runtime",
    "shutdown_telemetry",
    "build_resource",
    "add_otel_trace_context",
    "instrument_fastapi_app",
    "uninstrument_fastapi_app",
    "OutboxTelemetry",
    "get_outbox_telemetry",
]
