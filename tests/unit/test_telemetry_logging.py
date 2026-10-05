"""Unit tests for OpenTelemetry trace-context logging enrichment and privacy preservation."""

import io
import json
import logging
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from app.core.config import Settings
from app.core.logging import setup_logging
from app.core.telemetry.logging import add_otel_trace_context


def test_add_otel_trace_context_without_active_span() -> None:
    """Verify event_dict is left completely unmodified when no active span exists."""
    event_dict: dict[str, Any] = {
        "event": "user.login",
        "correlation_id": "corr-12345-abcde",
        "user": "developer",
    }
    result = add_otel_trace_context(None, "info", event_dict.copy())
    assert result == event_dict
    assert "trace_id" not in result
    assert "span_id" not in result
    assert result["correlation_id"] == "corr-12345-abcde"


def test_add_otel_trace_context_with_active_span() -> None:
    """Verify trace_id (32-char hex) and span_id (16-char hex) are added when span is active."""
    tracer_provider = TracerProvider()
    tracer = tracer_provider.get_tracer("test-tracer")

    with tracer.start_as_current_span("test-operation") as span:
        ctx = span.get_span_context()
        assert ctx.is_valid

        event_dict: dict[str, Any] = {
            "event": "database.query",
            "correlation_id": "corr-uuid-98765",
            "table": "audit_events",
        }

        result = add_otel_trace_context(None, "info", event_dict)

        expected_trace_id = f"{ctx.trace_id:032x}"
        expected_span_id = f"{ctx.span_id:016x}"

        assert len(result["trace_id"]) == 32
        assert len(result["span_id"]) == 16
        assert result["trace_id"] == expected_trace_id
        assert result["span_id"] == expected_span_id
        assert result["trace_id"].islower()
        assert result["span_id"].islower()

        # Correlation ID preserved completely independently
        assert result["correlation_id"] == "corr-uuid-98765"
        # No extra span metadata added
        assert "trace_flags" not in result
        assert "span_name" not in result
        assert "attributes" not in result


def test_structlog_pipeline_trace_enrichment_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify end-to-end structlog JSON rendering contains trace_id/span_id and redacts secrets."""
    stream = io.StringIO()

    test_settings = Settings(
        log_format="json",
        log_level="INFO",
    )
    setup_logging(test_settings)

    # Replace stream handler's stream with our in-memory buffer
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            monkeypatch.setattr(h, "stream", stream)

    import structlog

    structlog.contextvars.bind_contextvars(correlation_id="req-corr-777")
    tracer_provider = TracerProvider()
    tracer = tracer_provider.get_tracer("integration-test-tracer")

    with tracer.start_as_current_span("monitored-call") as span:
        ctx = span.get_span_context()
        logger = structlog.get_logger("test.logger")
        logger.info(
            "Service operation processed",
            api_key="super-secret-api-key",
            authorization="Bearer secret-token",
            safe_field="public-data",
        )

    output = stream.getvalue().strip()
    assert output, "Log output should not be empty"

    # Must be valid JSON (single line)
    parsed = json.loads(output)
    assert parsed["event"] == "Service operation processed"
    assert parsed["correlation_id"] == "req-corr-777"
    assert parsed["trace_id"] == f"{ctx.trace_id:032x}"
    assert parsed["span_id"] == f"{ctx.span_id:016x}"

    # Verify sensitive data redaction was preserved after trace enrichment
    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["authorization"] == "[REDACTED]"
    assert parsed["safe_field"] == "public-data"
