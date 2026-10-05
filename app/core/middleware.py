"""HTTP middleware for request correlation and structured lifecycle logging.

Enforces correlation tracking across all HTTP interactions, measures request elapsed duration
using a monotonic clock, emits structured completion log events, and ensures thorough context
cleanup across synchronous and asynchronous execution paths.
"""

import time

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.correlation import correlation_id_ctx, validate_correlation_id

logger = structlog.get_logger(__name__)


def resolve_route_template(request: Request) -> str:
    """Resolve the matched route template or return a privacy-safe fallback.

    Extracts Starlette's matched route template (e.g. '/api/v1/health', '/api/v1/items/{item_id}').
    If no route matched (404 / unmatched path), returns '<unmatched>' to prevent leaking
    sensitive path components (IDs, emails, filenames, tokens) or PII into logs.
    """
    route = request.scope.get("route")
    if route is not None:
        path_format = getattr(route, "path_format", None)
        if isinstance(path_format, str) and path_format:
            return path_format
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            return path
    return "<unmatched>"


class CorrelationAndLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware resolving correlation headers, binding structlog context, and logging requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        header_name = settings.correlation_header_name
        raw_header = request.headers.get(header_name)

        # Sanitize or generate correlation ID
        effective_correlation_id = validate_correlation_id(raw_header)

        # Bind to contextvar and request state
        token = correlation_id_ctx.set(effective_correlation_id)
        request.state.correlation_id = effective_correlation_id

        # Bind to structlog contextvars for automatic inclusion in downstream logs
        structlog.contextvars.bind_contextvars(correlation_id=effective_correlation_id)

        # Attach correlation ID to active server span if tracing is active
        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.set_attribute("e7.correlation_id", effective_correlation_id)

        start_time = time.monotonic()
        try:
            response = await call_next(request)
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

            route_template = resolve_route_template(request)
            if current_span.is_recording():
                current_span.set_attribute("http.route", route_template)
                current_span.set_attribute("http.target", route_template)
                clean_path = (
                    route_template if route_template.startswith("/") else f"/{route_template}"
                )
                current_span.set_attribute(
                    "http.url",
                    f"{request.url.scheme}://{request.url.netloc}{clean_path}",
                )

            # Inject correlation identifier into outgoing response headers
            response.headers[header_name] = effective_correlation_id

            # Emit structured request completion event (privacy-safe: no bodies, queries, raw paths)
            logger.info(
                "http_request_completed",
                method=request.method,
                route=route_template,
                status_code=response.status_code,
                duration_ms=duration_ms,
                correlation_id=effective_correlation_id,
            )
            return response

        except Exception as exc:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            route_template = resolve_route_template(request)
            if current_span.is_recording():
                current_span.set_attribute("http.route", route_template)
                current_span.set_attribute("http.target", route_template)
                clean_path = (
                    route_template if route_template.startswith("/") else f"/{route_template}"
                )
                current_span.set_attribute(
                    "http.url",
                    f"{request.url.scheme}://{request.url.netloc}{clean_path}",
                )
            logger.error(
                "http_request_failed",
                method=request.method,
                route=route_template,
                duration_ms=duration_ms,
                correlation_id=effective_correlation_id,
                error_type=type(exc).__name__,
            )
            raise

        finally:
            # Reliably reset context variable and clear structlog context
            correlation_id_ctx.reset(token)
            structlog.contextvars.unbind_contextvars("correlation_id")
