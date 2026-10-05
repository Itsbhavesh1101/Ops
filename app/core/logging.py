"""Structured logging foundation module for the IR Infotech E-7 Operations Engine.

Configures Python standard logging and structlog to provide structured, contextual,
and privacy-safe event logging supporting both JSON (production) and console (local dev) formats.
Idempotent setup prevents duplicate handler accumulation during testing or re-initialization.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import Settings, get_settings
from app.core.telemetry.logging import add_otel_trace_context

# Privacy-sensitive field blocklist for structured logging
SENSITIVE_KEY_SUBSTRINGS = {
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "private_key",
    "credit_card",
}


def censor_sensitive_data_processor(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Mask or redact sensitive keys from structured log event attributes."""
    for key in list(event_dict.keys()):
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in SENSITIVE_KEY_SUBSTRINGS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def setup_logging(settings: Settings | None = None) -> None:
    """Configure standard logging and structlog idempotently.

    Safe to invoke multiple times without handler accumulation or duplicate log emissions.
    """
    if settings is None:
        settings = get_settings()

    log_level = getattr(logging, settings.log_level, logging.INFO)

    # Common structlog processors pipeline
    # Deterministic order:
    # 1. Merge request-scoped ContextVars (correlation_id)
    # 2. Add logger name, level, positional args, timestamps, exception info
    # 3. Add OpenTelemetry trace/span context (trace_id, span_id) if active
    # 4. Censor sensitive data across all attributes (ensures trace fields cannot bypass)
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        add_otel_trace_context,
        censor_sensitive_data_processor,
    ]

    # Select renderer based on configuration
    if settings.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib root logger idempotently
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing StreamHandlers attached to stdout to prevent handler accumulation
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler):
            root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Silence excessively verbose third-party loggers if present
    for noisy_logger_name in ("uvicorn.access",):
        noisy_logger = logging.getLogger(noisy_logger_name)
        noisy_logger.propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Obtain a structured logger bound to the specified component name."""
    return structlog.get_logger(name)
