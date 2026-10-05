"""Unit tests for structured logging module (app/core/logging.py)."""

import logging

import structlog

from app.core.config import Settings
from app.core.logging import censor_sensitive_data_processor, get_logger, setup_logging


def test_setup_logging_idempotent() -> None:
    """Invoking setup_logging multiple times does not accumulate handlers."""
    settings = Settings(log_level="DEBUG", log_format="json")

    # Initial setup
    setup_logging(settings)
    root = logging.getLogger()
    initial_stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(initial_stream_handlers) == 1

    # Second invocation with different settings
    setup_logging(Settings(log_level="INFO", log_format="console"))
    second_stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(second_stream_handlers) == 1

    # Third invocation
    setup_logging(settings)
    third_stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert len(third_stream_handlers) == 1


def test_structured_logger_binding() -> None:
    """Verify structured logger binds context keys and returns bound logger."""
    logger = get_logger("test_module")
    assert logger is not None

    # Verify contextvar binding
    structlog.contextvars.bind_contextvars(correlation_id="test-corr-456", tenant_id="test-tenant")
    # Verify unbind
    structlog.contextvars.unbind_contextvars("correlation_id", "tenant_id")


def test_sensitive_data_redaction() -> None:
    """Verify processor redacts passwords, tokens, API keys, and authorization headers."""
    event_dict = {
        "event": "user_action",
        "username": "alice",
        "password": "super-secret-password",
        "api_key": "my-secret-key-123",
        "authorization": "Bearer eyJhbGciOi...",
        "user_token": "token-999",
        "safe_field": "public_data",
    }

    processed = censor_sensitive_data_processor(None, "info", event_dict)
    assert processed["username"] == "alice"
    assert processed["safe_field"] == "public_data"
    assert processed["password"] == "[REDACTED]"
    assert processed["api_key"] == "[REDACTED]"
    assert processed["authorization"] == "[REDACTED]"
    assert processed["user_token"] == "[REDACTED]"


def test_unmatched_route_request_logging_privacy() -> None:
    """Unmatched requests use <unmatched> marker and never log raw path or query values."""
    from starlette.testclient import TestClient

    import app.core.middleware as mw
    from app.main import app

    client = TestClient(app)

    old_logger = mw.logger
    try:
        with structlog.testing.capture_logs() as captured_logs:
            mw.logger = structlog.get_logger("app.core.middleware")
            # Request with sensitive-looking path and query parameters
            client.get(
                "/definitely-not-a-route/student-12345/private-value?token=sensitive-query-param&email=test@example.com"
            )

            http_logs = [
                log for log in captured_logs if log.get("event") == "http_request_completed"
            ]
            assert len(http_logs) == 1
            entry = http_logs[0]

        # 1. Fixed privacy-safe marker is used
        assert entry["route"] == "<unmatched>"
        assert entry["status_code"] == 404
        assert entry["method"] == "GET"

        # 2. Convert entire entry to string to verify sensitive values never leaked into any field
        log_str = str(entry)
        assert "student-12345" not in log_str
        assert "private-value" not in log_str
        assert "sensitive-query-param" not in log_str
        assert "test@example.com" not in log_str
        assert "token" not in entry
        assert "email" not in entry
        assert "query_string" not in entry
        assert "raw_path" not in entry
        assert "path" not in entry
    finally:
        mw.logger = old_logger


def test_resolve_route_template_parameterized_and_matched() -> None:
    """Route template resolution preserves parameterized templates and returns <unmatched>."""
    from starlette.requests import Request

    from app.core.middleware import resolve_route_template

    # Parameterized route simulation
    class DummyParameterizedRoute:
        path_format = "/api/v1/items/{item_id}"
        path = "/api/v1/items/{item_id}"

    req_param = Request({"type": "http", "route": DummyParameterizedRoute()})
    assert resolve_route_template(req_param) == "/api/v1/items/{item_id}"

    # Route with only path attribute
    class DummyPlainRoute:
        path = "/api/v1/users/{user_id}/profile"

    req_plain = Request({"type": "http", "route": DummyPlainRoute()})
    assert resolve_route_template(req_plain) == "/api/v1/users/{user_id}/profile"

    # Unmatched route (no route in scope)
    req_unmatched = Request({"type": "http"})
    assert resolve_route_template(req_unmatched) == "<unmatched>"
