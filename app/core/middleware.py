"""HTTP middleware for request correlation and structured lifecycle logging.

Enforces correlation tracking across all HTTP interactions, measures request elapsed duration
using a monotonic clock, emits structured completion log events, and ensures thorough context
cleanup across synchronous and asynchronous execution paths.
"""

import time
from typing import Any

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforces maximum incoming request body size to protect against DoS attacks."""

    def __init__(
        self,
        app: Any,
        max_body_bytes: int | None = None,
        dispatch: Any = None,
    ) -> None:
        super().__init__(app, dispatch=dispatch)
        self._max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        max_bytes = (
            self._max_body_bytes
            if self._max_body_bytes is not None
            else settings.max_request_body_bytes
        )

        # 1. Inspect Content-Length header if present
        content_length_str = request.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
                if content_length > max_bytes:
                    cid = getattr(request.state, "correlation_id", "unknown")
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "request.payload_too_large",
                                "message": (
                                    f"Request body exceeds maximum permitted size of {max_bytes} "
                                    f"bytes (received Content-Length: {content_length})."
                                ),
                                "correlation_id": cid,
                            }
                        },
                        headers={"X-Correlation-ID": cid},
                    )
            except ValueError:
                pass

        # 2. For chunked or streaming requests without Content-Length, wrap body consumption
        bytes_received = 0
        original_receive = request._receive

        async def limited_receive() -> Any:
            nonlocal bytes_received
            message = await original_receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > max_bytes:
                    # Raising HTTPException or sending response
                    from app.api.errors import ApiError

                    raise ApiError(
                        status_code=413,
                        code="request.payload_too_large",
                        message=(
                            f"Request body exceeds maximum permitted size of {max_bytes} bytes."
                        ),
                    )
            return message

        request._receive = limited_receive
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects defensive HTTP security response headers."""

    def __init__(
        self,
        app: Any,
        security_headers_enabled: bool | None = None,
        hsts_enabled: bool | None = None,
        dispatch: Any = None,
    ) -> None:
        super().__init__(app, dispatch=dispatch)
        self._security_headers_enabled = security_headers_enabled
        self._hsts_enabled = hsts_enabled

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        enabled = (
            self._security_headers_enabled
            if self._security_headers_enabled is not None
            else settings.security_headers_enabled
        )
        if not enabled:
            return await call_next(request)

        response = await call_next(request)

        # Base defensive security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        # Strict-Transport-Security (HSTS) - only emit over HTTPS or when explicitly enabled
        proto = request.headers.get("x-forwarded-proto")
        is_https = request.url.scheme == "https" or proto == "https"
        hsts_active = (
            self._hsts_enabled
            if self._hsts_enabled is not None
            else settings.hsts_enabled
        )
        if (hsts_active or settings.environment in ("staging", "production")) and is_https:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
            )

        # Content-Security-Policy (CSP)
        path = request.url.path
        if path in ("/docs", "/redoc", "/openapi.json") or path.startswith("/docs/"):
            # Permissive CSP for OpenAPI documentation UI (Swagger UI needs CDN assets)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "frame-ancestors 'none';"
            )
        else:
            # Strict CSP for API endpoints
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none';"
            )

        return response
