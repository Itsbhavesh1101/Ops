"""HTTP Boundary Hardening Regression Suite for Phase 8A.

Covers:
1. Request-body size hardening: 413 Payload Too Large on bodies > 10MB.
2. Security response headers: X-Content-Type-Options, Referrer-Policy, X-Frame-Options, CSP,
   and HSTS conditional gating (omitted on plain HTTP development).
3. OpenAPI / Documentation exposure: configurable disablement of /docs, /redoc, and /openapi.json.
4. CORS regression: strict origins, disallowed origin rejection, no arbitrary origin reflection.
5. Information leakage: no stack traces, raw secrets, tokens, or DB URLs in errors.
6. SQL-injection regression: parameter binding safety on hostile query/identifier inputs.
"""

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.core.config import Settings
from app.core.middleware import (
    CorrelationAndLoggingMiddleware,
    RequestBodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from tests.security.conftest import (
    TENANT_A,
    SecurityTestContext,
)


class TestHttpHardening:
    """Verifies HTTP boundary controls, headers, docs exposure, CORS, and leakage guards."""

    # -------------------------------------------------------------------------
    # 1. Request Body Size Limit (10MB ceiling)
    # -------------------------------------------------------------------------
    def test_oversized_content_length_rejected_413(self) -> None:
        """Requests declaring Content-Length > 10MB are rejected immediately with 413."""
        app = FastAPI()
        app.add_middleware(RequestBodySizeLimitMiddleware, max_body_bytes=10 * 1024 * 1024)
        register_api_exception_handlers(app)

        @app.post("/test-upload")
        async def upload_endpoint(payload: dict[str, Any]) -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, raise_server_exceptions=False)
        # Content-Length exceeds 10MB (11MB declared)
        headers = {"Content-Length": str(11 * 1024 * 1024)}
        res = client.post("/test-upload", content=b"x" * 100, headers=headers)
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "request.payload_too_large"

    def test_oversized_streaming_body_rejected_413(self) -> None:
        """Streaming bodies exceeding max limit return 413 without full memory buffering."""
        app = FastAPI()
        # Set small limit (1KB) to test streaming cutoff without huge allocation
        app.add_middleware(RequestBodySizeLimitMiddleware, max_body_bytes=1024)
        register_api_exception_handlers(app)

        @app.post("/test-stream")
        async def stream_endpoint(payload: dict[str, Any]) -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, raise_server_exceptions=False)
        # Send 2KB
        oversized_data = b"a" * 2048
        res = client.post("/test-stream", content=oversized_data)
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "request.payload_too_large"

    def test_normal_payload_accepted(self) -> None:
        """Normal payload within size limit is processed normally."""
        app = FastAPI()
        app.add_middleware(RequestBodySizeLimitMiddleware, max_body_bytes=10 * 1024 * 1024)
        register_api_exception_handlers(app)

        @app.post("/test-normal")
        async def normal_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, raise_server_exceptions=False)
        res = client.post("/test-normal", json={"small": "payload"})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    # -------------------------------------------------------------------------
    # 2. Security Response Headers
    # -------------------------------------------------------------------------
    def test_security_headers_emitted_correctly(self) -> None:
        """Security headers are emitted on responses; HSTS is strictly omitted on HTTP."""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=False)

        @app.get("/api/v1/ping")
        async def ping() -> dict[str, str]:
            return {"status": "pong"}

        client = TestClient(app)
        res = client.get("/api/v1/ping")
        assert res.status_code == 200

        # Check defensive headers
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in res.headers.get("Content-Security-Policy", "")

        # Strict-Transport-Security must NOT be present on development / plain HTTP
        assert "Strict-Transport-Security" not in res.headers

    def test_hsts_emitted_when_configured_over_https(self) -> None:
        """HSTS is only emitted when explicitly enabled and running over HTTPS."""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=True)

        @app.get("/api/v1/ping")
        async def ping() -> dict[str, str]:
            return {"status": "pong"}

        client = TestClient(app)
        # Plain HTTP -> Still omitted even if hsts_enabled=True
        res_http = client.get("http://testserver/api/v1/ping")
        assert "Strict-Transport-Security" not in res_http.headers

        # HTTPS -> Emitted
        res_https = client.get("https://testserver/api/v1/ping")
        assert "Strict-Transport-Security" in res_https.headers
        assert "max-age=" in res_https.headers["Strict-Transport-Security"]

    # -------------------------------------------------------------------------
    # 3. OpenAPI / Docs Exposure Control
    # -------------------------------------------------------------------------
    def test_docs_enabled_mode(self) -> None:
        """When docs_enabled=True, API documentation endpoints are available."""
        settings = Settings(docs_enabled=True)
        app = FastAPI(
            docs_url="/docs" if settings.docs_enabled else None,
            redoc_url="/redoc" if settings.docs_enabled else None,
            openapi_url="/openapi.json" if settings.docs_enabled else None,
        )

        client = TestClient(app)
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_docs_disabled_in_production(self) -> None:
        """When docs_enabled=False, /docs, /redoc, and /openapi.json return 404."""
        settings = Settings(docs_enabled=False)
        app = FastAPI(
            docs_url="/docs" if settings.docs_enabled else None,
            redoc_url="/redoc" if settings.docs_enabled else None,
            openapi_url="/openapi.json" if settings.docs_enabled else None,
        )

        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    # -------------------------------------------------------------------------
    # 4. CORS Regression
    # -------------------------------------------------------------------------
    def test_cors_disallowed_origin_not_reflected(self) -> None:
        """Arbitrary untrusted origin is never reflected in Access-Control-Allow-Origin."""
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://ops.school.edu"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/api/v1/test-cors")
        async def cors_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app)

        # 1. Approved origin
        res_good = client.get("/api/v1/test-cors", headers={"Origin": "https://ops.school.edu"})
        assert res_good.headers.get("access-control-allow-origin") == "https://ops.school.edu"

        # 2. Hostile disallowed origin
        res_bad = client.get("/api/v1/test-cors", headers={"Origin": "https://evil-attacker.com"})
        assert "access-control-allow-origin" not in res_bad.headers

    # -------------------------------------------------------------------------
    # 5. Information Leakage & Secret Redaction
    # -------------------------------------------------------------------------
    def test_internal_server_errors_do_not_leak_stack_traces(self) -> None:
        """Unhandled exceptions return sanitized 500 without leaking stack traces."""
        app = FastAPI()
        app.add_middleware(CorrelationAndLoggingMiddleware)
        register_api_exception_handlers(app)

        @app.get("/api/v1/boom")
        async def boom() -> None:
            conn = "postgresql://admin:SuperSecretPassword@db:5432/e7"
            raise RuntimeError(f"Database connection string: {conn}")

        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/api/v1/boom")
        assert res.status_code == 500
        data = res.json()
        assert data["error"]["code"] in {"internal.server_error", "server.internal_error"}
        # Secret and internal traceback must NOT be present
        assert "SuperSecretPassword" not in res.text
        assert "RuntimeError" not in res.text
        assert "Traceback" not in res.text
        assert "correlation_id" in data["error"]

    # -------------------------------------------------------------------------
    # 6. SQL-Injection Regression
    # -------------------------------------------------------------------------
    def test_sql_injection_hostile_strings_remain_literal_data(
        self, client: TestClient, security_context: SecurityTestContext
    ) -> None:
        """Hostile SQL payloads in search/filter parameters are handled safely without SQL error."""
        headers = security_context.get_auth_headers("auditor", TENANT_A)

        sql_injection_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE audit_events; --",
            "1 UNION SELECT null, null, null--",
            "admin'--",
            "\" OR \"\"=\"",
        ]

        for hostile_str in sql_injection_payloads:
            # Audit filter
            res = client.get(
                f"/api/v1/audit?event_type={hostile_str}",
                headers=headers,
            )
            # Must return 200 with empty list or 422 validation, never 500 SQL syntax error
            assert res.status_code in {200, 422}
            if res.status_code == 200:
                assert res.json() == []
