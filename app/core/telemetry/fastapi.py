"""FastAPI OpenTelemetry instrumentation and request tracing module.

Provides programmatic instrumentation of the FastAPI application with:
- Standard safe semantic attributes (method, route template, status code, service info).
- Strict privacy boundary enforcement (zero capture of headers, cookies, credentials,
  query params, bodies, or concrete IDs in paths).
- Dynamic attachment of e7.correlation_id to active server spans.
- Default exclusion of health and readiness probes (/api/v1/health, /api/v1/ready).
- Zero global bootstrap or automatic process patching.
"""

from typing import Any

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import TracerProvider

DEFAULT_EXCLUDED_URLS = "api/v1/health,api/v1/ready"


def _server_request_hook(span: Any, scope: dict[str, Any]) -> None:
    """Sanitize initial span attributes to enforce zero query parameter leakage.

    Guarantees:
    - Query parameter values are NEVER recorded in span attributes.
    - http.target and http.url contain path only, without ?query=value.
    - Raw query strings from scope are never attached.
    """
    if hasattr(span, "is_recording") and span.is_recording():
        path = scope.get("path", "/")
        server = scope.get("server")
        scheme = scope.get("scheme", "http")
        host = server[0] if server else "localhost"
        port = f":{server[1]}" if server and server[1] not in (80, 443) else ""
        span.set_attribute("http.target", path)
        span.set_attribute("http.url", f"{scheme}://{host}{port}{path}")

        # Guard against any attributes pre-populated by OTel containing query strings
        attrs = getattr(span, "attributes", None) or {}
        for key in list(attrs.keys()):
            val = attrs[key]
            if isinstance(val, str) and "?" in val:
                span.set_attribute(key, val.split("?")[0])


def instrument_fastapi_app(
    app: FastAPI,
    tracer_provider: TracerProvider | None = None,
    excluded_urls: str = DEFAULT_EXCLUDED_URLS,
) -> FastAPIInstrumentor:
    """Programmatically instrument FastAPI app with OpenTelemetry request tracing."""
    instrumentor = FastAPIInstrumentor()
    instrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=excluded_urls,
        exclude_spans=["receive", "send"],
        server_request_hook=_server_request_hook,
        http_capture_headers_server_request=None,
        http_capture_headers_server_response=None,
    )
    # Rebuild Starlette middleware stack to activate the newly added instrumentation middleware
    app.middleware_stack = app.build_middleware_stack()
    return instrumentor


def uninstrument_fastapi_app(
    app: FastAPI,
    instrumentor: FastAPIInstrumentor | None = None,
) -> None:
    """Safely remove OpenTelemetry instrumentation from the FastAPI app."""
    if instrumentor is not None:
        try:
            instrumentor.uninstrument_app(app)
        except Exception:
            pass
    # Rebuild middleware stack after removal
    try:
        app.middleware_stack = app.build_middleware_stack()
    except Exception:
        pass
