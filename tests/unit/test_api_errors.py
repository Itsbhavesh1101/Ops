"""Unit tests for standardized API error contracts and safe FastAPI exception handlers.

Task 1I.1 of Phase 1 Foundation:
- Canonical nested error response envelope (ApiErrorResponse / ApiErrorBody).
- Bounded lowercase namespaced error code validation.
- Safe field-validation detail model with strict privacy minimization.
- Provider-neutral ApiError presentation exception.
- FastAPI / Starlette exception handlers (ApiError, 422, 404, 405, generic 500).
- Correlation ID propagation into error responses using Task-1B infrastructure.
- Zero database, messaging, or business logic dependencies.
- Database stability (Alembic head 0006, 4 canonical tables).
"""

import importlib
import uuid
from collections.abc import Generator

import pytest
from fastapi import FastAPI, Header, Query
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import (
    MAX_ERROR_CODE_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
    ApiError,
    ApiErrorBody,
    ApiErrorDetail,
    ApiErrorResponse,
    ErrorCode,
    FoundationErrorCode,
    register_api_exception_handlers,
    resolve_correlation_id,
    validate_error_code,
)
from app.core.correlation import correlation_id_ctx
from app.core.middleware import CorrelationAndLoggingMiddleware

# ---------------------------------------------------------------------------
# Synthetic Request Models and Test App Fixtures
# ---------------------------------------------------------------------------


class SyntheticItemRequest(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8)
    quantity: int = Field(ge=1, le=100)


def create_test_app() -> FastAPI:
    """Create a local isolated FastAPI app for Task 1I.1 testing.

    Does NOT modify app/main.py.
    """
    test_app = FastAPI(title="Task1I1TestApp")
    test_app.add_middleware(CorrelationAndLoggingMiddleware)
    register_api_exception_handlers(test_app)

    @test_app.get("/synthetic/ok")
    def ok_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.post("/synthetic/validate")
    def validate_endpoint(body: SyntheticItemRequest) -> dict[str, str]:
        return {"name": body.name}

    @test_app.get("/synthetic/header-validate")
    def header_validate_endpoint(
        x_api_secret: str = Header(..., min_length=10),
    ) -> dict[str, str]:
        return {"secret_len": str(len(x_api_secret))}

    @test_app.get("/synthetic/query-validate")
    def query_validate_endpoint(count: int = Query(..., ge=1)) -> dict[str, int]:
        return {"count": count}

    @test_app.get("/synthetic/raise-api-error-simple")
    def raise_api_error_simple() -> None:
        raise ApiError(
            status_code=409,
            code="synthetic.resource_conflict",
            message="A synthetic resource conflict occurred.",
        )

    @test_app.get("/synthetic/raise-api-error-detailed")
    def raise_api_error_detailed() -> None:
        raise ApiError(
            status_code=422,
            code="synthetic.validation_failed",
            message="Synthetic validation failed.",
            details=[
                ApiErrorDetail(
                    location=("body", "field_a"),
                    code="custom_code",
                    message="Custom field error message.",
                )
            ],
            headers={"X-Custom-Header": "custom-value"},
        )

    @test_app.get("/synthetic/raise-http-404")
    def raise_http_404() -> None:
        raise StarletteHTTPException(status_code=404, detail="Raw internal detail to suppress")

    @test_app.get("/synthetic/raise-fastapi-http-404")
    def raise_fastapi_http_404() -> None:
        raise FastAPIHTTPException(status_code=404, detail="FastAPI detail to suppress")

    @test_app.get("/synthetic/raise-http-415")
    def raise_http_415() -> None:
        raise StarletteHTTPException(status_code=415, detail="Unsupported media")

    @test_app.get("/synthetic/raise-http-403")
    def raise_http_403() -> None:
        raise StarletteHTTPException(status_code=403, detail="Forbidden detail to suppress")

    @test_app.get("/synthetic/raise-unexpected-500")
    def raise_unexpected_500() -> None:
        raise RuntimeError("VERY_SECRET_INTERNAL_FAILURE_123")

    @test_app.get("/synthetic/get-only")
    def get_only_endpoint() -> dict[str, str]:
        return {"method": "GET"}

    return test_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Yield a TestClient bound to a fresh test FastAPI application."""
    test_app = create_test_app()
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Error Code Validation Tests (Section 29)
# ---------------------------------------------------------------------------


class TestErrorCodeValidation:
    """Verify machine-readable namespaced error code validation contracts."""

    @pytest.mark.parametrize(
        "valid_code",
        [
            "request.validation_failed",
            "request.not_found",
            "request.method_not_allowed",
            "request.unsupported_media_type",
            "internal.server_error",
            "task.not_found",
            "workflow.invalid_transition",
            "auth.token_expired",
            "a.b",
            "a_1.b_2.c_3",
            "module.sub_module.specific_error",
        ],
    )
    def test_valid_error_codes_accepted(self, valid_code: str) -> None:
        assert validate_error_code(valid_code) == valid_code
        ec = ErrorCode(valid_code)
        assert ec.value == valid_code
        assert str(ec) == valid_code
        assert repr(ec) == f"ErrorCode('{valid_code}')"

    @pytest.mark.parametrize(
        ("invalid_code", "expected_err_type"),
        [
            ("", ValueError),
            ("   ", ValueError),
            ("request.validation_failed ", ValueError),
            (" request.validation_failed", ValueError),
            ("request.\tvalidation_failed", ValueError),
            ("request.\nvalidation_failed", ValueError),
            ("request.not_found\r", ValueError),
            ("single_segment", ValueError),
            ("Request.not_found", ValueError),
            ("REQUEST.VALIDATION_FAILED", ValueError),
            (".request.not_found", ValueError),
            ("request.not_found.", ValueError),
            ("request..not_found", ValueError),
            ("request.not-found", ValueError),  # hyphens rejected; underscores required
            ("1request.invalid", ValueError),  # leading digit in first segment rejected
            ("request.1invalid", ValueError),  # leading digit in second segment rejected
            (123, TypeError),
            (None, TypeError),
            (["request.not_found"], TypeError),
        ],
    )
    def test_invalid_error_codes_rejected(
        self, invalid_code: object, expected_err_type: type[Exception]
    ) -> None:
        with pytest.raises(expected_err_type):
            validate_error_code(invalid_code)

    def test_oversized_error_code_rejected(self) -> None:
        long_segment = "a" * (MAX_ERROR_CODE_LENGTH - 3)
        oversized = f"req.{long_segment}z"
        assert len(oversized) > MAX_ERROR_CODE_LENGTH
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_error_code(oversized)

    def test_error_code_value_object_immutability(self) -> None:
        ec = ErrorCode("test.code")
        with pytest.raises(ValidationError):
            # Mutating a frozen pydantic model raises ValidationError
            ec.value = "other.code"  # type: ignore[misc]

    def test_error_code_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ErrorCode(value="test.code", extra_field="forbidden")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. Canonical Error Models Tests (Section 30, 25)
# ---------------------------------------------------------------------------


class TestCanonicalErrorModels:
    """Verify shape, immutability, extra="forbid", and privacy invariants of error models."""

    def test_api_error_detail_immutability_and_structure(self) -> None:
        detail = ApiErrorDetail(
            location=["body", "items", 0, "name"],
            code="string_too_short",
            message="String should have at least 3 characters.",
        )
        assert detail.location == ("body", "items", 0, "name")
        assert detail.code == "string_too_short"
        assert detail.message == "String should have at least 3 characters."

        # Model is frozen
        with pytest.raises(ValidationError):
            detail.code = "new_code"  # type: ignore[misc]

        # Extra fields forbidden (specifically raw input or ctx)
        with pytest.raises(ValidationError):
            ApiErrorDetail(
                location=["body", "name"],
                code="invalid",
                message="bad",
                input="SECRET_INPUT",  # type: ignore[call-arg]
            )

    def test_api_error_body_immutability_and_invariants(self) -> None:
        body = ApiErrorBody(
            code="request.not_found",
            message="Resource not found.",
            correlation_id="test-corr-id-123",
        )
        assert body.code == "request.not_found"
        assert body.message == "Resource not found."
        assert body.correlation_id == "test-corr-id-123"
        assert body.details is None

        # Model is frozen
        with pytest.raises(ValidationError):
            body.message = "New message"  # type: ignore[misc]

        # Extra fields forbidden
        with pytest.raises(ValidationError):
            ApiErrorBody(
                code="request.not_found",
                message="Resource not found.",
                correlation_id="test-corr-id-123",
                stack_trace="Traceback...",  # type: ignore[call-arg]
            )

    def test_api_error_response_envelope(self) -> None:
        body = ApiErrorBody(
            code="request.validation_failed",
            message="Request validation failed.",
            correlation_id="test-corr-id-123",
            details=[
                ApiErrorDetail(
                    location=["body", "name"],
                    code="string_too_short",
                    message="Too short",
                )
            ],
        )
        resp = ApiErrorResponse(error=body)
        dumped = resp.model_dump(mode="json", exclude_none=True)

        assert "error" in dumped
        assert dumped["error"]["code"] == "request.validation_failed"
        assert dumped["error"]["message"] == "Request validation failed."
        assert dumped["error"]["correlation_id"] == "test-corr-id-123"
        assert len(dumped["error"]["details"]) == 1
        assert dumped["error"]["details"][0] == {
            "location": ["body", "name"],
            "code": "string_too_short",
            "message": "Too short",
        }

        # Verify forbidden field names do not exist on the model
        for forbidden in (
            "stack",
            "exception",
            "traceback",
            "raw_input",
            "raw_request",
            "debug",
        ):
            assert not hasattr(resp, forbidden)
            assert not hasattr(body, forbidden)

    def test_api_error_body_message_max_length(self) -> None:
        oversized_msg = "x" * (MAX_ERROR_MESSAGE_LENGTH + 1)
        with pytest.raises(ValueError, match="message exceeds maximum length"):
            ApiErrorBody(
                code="request.not_found",
                message=oversized_msg,
                correlation_id="test-corr-id",
            )


# ---------------------------------------------------------------------------
# 3. ApiError Exception Tests (Section 31, 9, 10, 18)
# ---------------------------------------------------------------------------


class TestApiErrorException:
    """Verify ApiError exception behavior, status validation, and presentation mapping."""

    def test_api_error_validation_rules(self) -> None:
        # Valid 4xx
        err400 = ApiError(status_code=400, code="request.bad_request", message="Bad request.")
        assert err400.status_code == 400
        assert err400.code == "request.bad_request"
        assert err400.message == "Bad request."
        assert err400.details is None
        assert err400.headers is None

        # Valid 5xx
        err503 = ApiError(status_code=503, code="service.unavailable", message="Unavailable.")
        assert err503.status_code == 503

        # Invalid status codes (< 400 or > 599)
        with pytest.raises(ValueError, match="status_code must be an HTTP error status"):
            ApiError(status_code=200, code="test.ok", message="OK")

        with pytest.raises(ValueError, match="status_code must be an HTTP error status"):
            ApiError(status_code=302, code="test.redirect", message="Redirect")

        with pytest.raises(ValueError, match="status_code must be an HTTP error status"):
            ApiError(status_code=600, code="test.invalid", message="Too high")

        # Invalid status_code type (boolean, float, string)
        with pytest.raises(TypeError, match="status_code must be an integer"):
            ApiError(status_code=True, code="test.invalid", message="Bool status")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="status_code must be an integer"):
            ApiError(status_code="400", code="test.invalid", message="Str status")  # type: ignore[arg-type]

        # Invalid empty or oversized message
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            ApiError(status_code=400, code="test.invalid", message="")

        with pytest.raises(ValueError, match="message exceeds maximum length"):
            ApiError(
                status_code=400,
                code="test.invalid",
                message="a" * (MAX_ERROR_MESSAGE_LENGTH + 1),
            )

    def test_api_error_accepts_error_code_value_object(self) -> None:
        ec = ErrorCode("domain.custom_failure")
        err = ApiError(status_code=422, code=ec, message="Custom failure occurred.")
        assert err.code == "domain.custom_failure"

    def test_api_error_endpoint_simple(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-api-error-simple",
            headers={"X-Correlation-ID": "corr-api-error-1"},
        )
        assert res.status_code == 409
        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == "synthetic.resource_conflict"
        assert data["error"]["message"] == "A synthetic resource conflict occurred."
        assert data["error"]["correlation_id"] == "corr-api-error-1"
        assert "details" not in data["error"]  # omitted when absent

    def test_api_error_endpoint_detailed(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-api-error-detailed",
            headers={"X-Correlation-ID": "corr-api-error-2"},
        )
        assert res.status_code == 422
        assert res.headers.get("X-Custom-Header") == "custom-value"
        data = res.json()
        assert data["error"]["code"] == "synthetic.validation_failed"
        assert data["error"]["message"] == "Synthetic validation failed."
        assert data["error"]["correlation_id"] == "corr-api-error-2"
        assert len(data["error"]["details"]) == 1
        assert data["error"]["details"][0] == {
            "location": ["body", "field_a"],
            "code": "custom_code",
            "message": "Custom field error message.",
        }


# ---------------------------------------------------------------------------
# 4. Request Validation Handler Tests (Section 32, 11, 12, 13, 37)
# ---------------------------------------------------------------------------


class TestRequestValidationHandler:
    """Verify RequestValidationError handling, 422 status, and strict privacy protection."""

    def test_body_validation_failure_sanitization_and_privacy(self, client: TestClient) -> None:
        secret_value = "SYNTHETIC_SECRET_DO_NOT_EXPOSE_987654321"
        payload = {
            "name": "a",  # min_length=3 failure
            "password": secret_value,  # length ok, but we test asserting secret is not exposed
            "quantity": 999,  # le=100 failure
        }
        res = client.post(
            "/synthetic/validate",
            json=payload,
            headers={"X-Correlation-ID": "corr-val-1"},
        )
        assert res.status_code == 422
        assert res.headers["content-type"].startswith("application/json")

        text_body = res.text
        # CRITICAL PRIVACY INVARIANT: secret must not appear anywhere in serialized response
        assert secret_value not in text_body

        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_VALIDATION_FAILED
        assert data["error"]["message"] == "Request validation failed."
        assert data["error"]["correlation_id"] == "corr-val-1"
        assert "details" in data["error"]

        # Assert details structure contains location, code, message and NO input
        details = data["error"]["details"]
        assert len(details) >= 1
        for d in details:
            assert "location" in d
            assert "code" in d
            assert "message" in d
            assert "input" not in d
            assert "ctx" not in d
            assert secret_value not in str(d)

    def test_missing_body_validation_failure(self, client: TestClient) -> None:
        res = client.post(
            "/synthetic/validate",
            content=b"",
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": "corr-val-missing",
            },
        )
        assert res.status_code == 422
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_VALIDATION_FAILED
        assert data["error"]["correlation_id"] == "corr-val-missing"

    def test_query_parameter_validation_failure(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/query-validate?count=-5",
            headers={"X-Correlation-ID": "corr-query-val"},
        )
        assert res.status_code == 422
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_VALIDATION_FAILED
        assert data["error"]["correlation_id"] == "corr-query-val"
        details = data["error"]["details"]
        assert any(d["location"] == ["query", "count"] for d in details)
        # Assert no input field
        for d in details:
            assert "input" not in d

    def test_header_validation_failure_does_not_echo_secret_value(self, client: TestClient) -> None:
        short_secret = "val_short"  # min_length=10 failure
        res = client.get(
            "/synthetic/header-validate",
            headers={
                "x-api-secret": short_secret,
                "X-Correlation-ID": "corr-header-val",
            },
        )
        assert res.status_code == 422
        text_body = res.text
        # Assert short_secret value is NOT echoed in the response
        assert short_secret not in text_body

        data = res.json()
        details = data["error"]["details"]
        assert any(d["location"] == ["header", "x-api-secret"] for d in details)
        for d in details:
            assert "input" not in d


# ---------------------------------------------------------------------------
# 5. Routing Error Handlers Tests: 404, 405, 415 (Section 33, 34, 14)
# ---------------------------------------------------------------------------


class TestRoutingErrorHandlers:
    """Verify 404, 405, and framework HTTP exception canonical handling."""

    def test_404_not_found_on_unknown_path(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/nonexistent-route-9999",
            headers={"X-Correlation-ID": "corr-404-1"},
        )
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/json")
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_NOT_FOUND
        assert data["error"]["message"] == "Resource not found."
        assert data["error"]["correlation_id"] == "corr-404-1"
        assert "details" not in data["error"]
        # Raw framework detail ("Not Found") must not be top-level or override canonical message
        assert "detail" not in data

    def test_404_raised_from_endpoint(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-http-404",
            headers={"X-Correlation-ID": "corr-404-2"},
        )
        assert res.status_code == 404
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_NOT_FOUND
        assert data["error"]["message"] == "Resource not found."
        assert "Raw internal detail to suppress" not in res.text

    def test_404_fastapi_http_exception(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-fastapi-http-404",
            headers={"X-Correlation-ID": "corr-404-3"},
        )
        assert res.status_code == 404
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_NOT_FOUND
        assert data["error"]["message"] == "Resource not found."
        assert "FastAPI detail to suppress" not in res.text

    def test_405_method_not_allowed(self, client: TestClient) -> None:
        res = client.post(
            "/synthetic/get-only",
            headers={"X-Correlation-ID": "corr-405-1"},
        )
        assert res.status_code == 405
        assert res.headers["content-type"].startswith("application/json")
        # Starlette 405 header compliance
        assert "allow" in res.headers or "Allow" in res.headers
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_METHOD_NOT_ALLOWED
        assert data["error"]["message"] == "Method not allowed."
        assert data["error"]["correlation_id"] == "corr-405-1"
        assert "details" not in data["error"]

    def test_415_unsupported_media_type(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-http-415",
            headers={"X-Correlation-ID": "corr-415-1"},
        )
        assert res.status_code == 415
        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.REQUEST_UNSUPPORTED_MEDIA_TYPE
        assert data["error"]["message"] == "Unsupported media type."
        assert data["error"]["correlation_id"] == "corr-415-1"

    def test_other_starlette_http_exception_suppresses_raw_detail(self, client: TestClient) -> None:
        res = client.get(
            "/synthetic/raise-http-403",
            headers={"X-Correlation-ID": "corr-403-1"},
        )
        assert res.status_code == 403
        data = res.json()
        assert data["error"]["code"] == "http.error_403"
        assert data["error"]["message"] == "Forbidden."
        assert "Forbidden detail to suppress" not in res.text


# ---------------------------------------------------------------------------
# 6. Generic Unhandled Exception Handler Tests: 500 (Section 35, 16, 17)
# ---------------------------------------------------------------------------


class TestUnhandledExceptionHandler:
    """Verify unexpected 500 exceptions return safe fixed messages without leakage."""

    def test_generic_500_masks_all_internal_details(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        res = client.get(
            "/synthetic/raise-unexpected-500",
            headers={"X-Correlation-ID": "corr-500-1"},
        )
        assert res.status_code == 500
        assert res.headers["content-type"].startswith("application/json")

        text_body = res.text
        # Assert complete absence of internal exception details
        assert "VERY_SECRET_INTERNAL_FAILURE_123" not in text_body
        assert "RuntimeError" not in text_body
        assert "traceback" not in text_body.lower()
        assert "line " not in text_body

        data = res.json()
        assert data["error"]["code"] == FoundationErrorCode.INTERNAL_SERVER_ERROR
        assert data["error"]["message"] == "An internal server error occurred."
        assert data["error"]["correlation_id"] == "corr-500-1"
        assert "details" not in data["error"]


# ---------------------------------------------------------------------------
# 7. Correlation Propagation Tests (Section 36, 8)
# ---------------------------------------------------------------------------


class TestCorrelationPropagation:
    """Verify correlation ID propagation across all error types and fallback generation."""

    def test_correlation_propagated_on_valid_incoming_header(self, client: TestClient) -> None:
        custom_cid = "custom-client-cid-abc.123_xyz"

        # ApiError
        r1 = client.get(
            "/synthetic/raise-api-error-simple",
            headers={"X-Correlation-ID": custom_cid},
        )
        assert r1.json()["error"]["correlation_id"] == custom_cid
        assert r1.headers.get("X-Correlation-ID") == custom_cid

        # Validation error
        r2 = client.post(
            "/synthetic/validate",
            json={"name": "x"},
            headers={"X-Correlation-ID": custom_cid},
        )
        assert r2.json()["error"]["correlation_id"] == custom_cid
        assert r2.headers.get("X-Correlation-ID") == custom_cid

        # 404
        r3 = client.get(
            "/synthetic/unmatched-404-path",
            headers={"X-Correlation-ID": custom_cid},
        )
        assert r3.json()["error"]["correlation_id"] == custom_cid
        assert r3.headers.get("X-Correlation-ID") == custom_cid

        # 500
        r4 = client.get(
            "/synthetic/raise-unexpected-500",
            headers={"X-Correlation-ID": custom_cid},
        )
        assert r4.json()["error"]["correlation_id"] == custom_cid
        assert r4.headers.get("X-Correlation-ID") == custom_cid

    def test_correlation_generated_when_missing(self, client: TestClient) -> None:
        res = client.get("/synthetic/raise-api-error-simple")
        data = res.json()
        cid = data["error"]["correlation_id"]
        assert cid is not None
        # Must be valid UUID4
        uuid_obj = uuid.UUID(cid)
        assert str(uuid_obj) == cid
        # Header matches body
        assert res.headers.get("X-Correlation-ID") == cid

    def test_correlation_replaced_when_malformed(self, client: TestClient) -> None:
        unsafe_cid = "bad\nid\r\nInjected-Header: evil"
        res = client.get(
            "/synthetic/raise-api-error-simple",
            headers={"X-Correlation-ID": unsafe_cid},
        )
        data = res.json()
        safe_cid = data["error"]["correlation_id"]
        assert safe_cid != unsafe_cid
        assert "\n" not in safe_cid
        assert str(uuid.UUID(safe_cid)) == safe_cid
        assert res.headers.get("X-Correlation-ID") == safe_cid

    def test_resolve_correlation_id_fallback_without_context(self) -> None:
        # Contextvar is empty
        token = correlation_id_ctx.set(None)
        try:
            cid = resolve_correlation_id(None)
            assert cid is not None
            assert str(uuid.UUID(cid)) == cid
        finally:
            correlation_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# 8. Details Optionality Canonical Behavior (Section 38)
# ---------------------------------------------------------------------------


class TestDetailsOptionality:
    """Verify consistent serialization behavior: details omitted when absent."""

    def test_details_omitted_when_none(self, client: TestClient) -> None:
        # 404
        r404 = client.get("/nonexistent-path")
        assert "details" not in r404.json()["error"]

        # 405
        r405 = client.post("/synthetic/get-only")
        assert "details" not in r405.json()["error"]

        # 500
        r500 = client.get("/synthetic/raise-unexpected-500")
        assert "details" not in r500.json()["error"]

        # Simple ApiError without details
        r_api = client.get("/synthetic/raise-api-error-simple")
        assert "details" not in r_api.json()["error"]

    def test_details_present_when_provided(self, client: TestClient) -> None:
        # Validation error has details
        r_val = client.post("/synthetic/validate", json={"name": "a"})
        assert "details" in r_val.json()["error"]
        assert isinstance(r_val.json()["error"]["details"], list)

        # ApiError with explicit details has details
        r_det = client.get("/synthetic/raise-api-error-detailed")
        assert "details" in r_det.json()["error"]
        assert isinstance(r_det.json()["error"]["details"], list)


# ---------------------------------------------------------------------------
# 9. Handler Registration and Import-Time Side Effects (Section 27, 28, 39)
# ---------------------------------------------------------------------------


class TestHandlerRegistrationAndSideEffects:
    """Verify explicit registration, idempotency, and pure import safety."""

    def test_registration_idempotency(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)
        # Register second time must succeed without error
        register_api_exception_handlers(app)

        # Check registered handler types
        handlers = app.exception_handlers
        assert ApiError in handlers
        assert RequestValidationError in handlers
        assert StarletteHTTPException in handlers
        assert FastAPIHTTPException in handlers
        assert Exception in handlers

    def test_no_import_time_side_effects(self) -> None:
        # Re-importing app.api.errors must not create global FastAPI apps or routes
        mod = importlib.import_module("app.api.errors")
        assert not hasattr(mod, "app")
        assert not hasattr(mod, "router")


# ---------------------------------------------------------------------------
# 10. Architectural Isolation Tests (Section 40)
# ---------------------------------------------------------------------------


class TestArchitecturalIsolation:
    """Verify API error code imports ZERO database, persistence, or messaging modules."""

    def test_zero_forbidden_imports_in_errors_module(self) -> None:
        import app.api.errors as errors_mod

        # Inspect the module source / attributes
        forbidden_terms = [
            "sqlalchemy",
            "AsyncSession",
            "OutboxRepository",
            "InboxRepository",
            "IdempotencyRepository",
            "rabbitmq",
            "aio_pika",
            "alembic",
        ]
        module_text = open(errors_mod.__file__, encoding="utf-8").read()
        for term in forbidden_terms:
            assert f"import {term}" not in module_text, f"Forbidden import: {term}"
            assert f"from {term}" not in module_text, f"Forbidden import: {term}"

        # Check sys.modules for any direct connection made by errors
        assert not hasattr(errors_mod, "Session")
        assert not hasattr(errors_mod, "AsyncSession")
        assert not hasattr(errors_mod, "Base")


# ---------------------------------------------------------------------------
# 11. Database and Schema Stability Tests (Section 41)
# ---------------------------------------------------------------------------


class TestDatabaseSchemaStability:
    """Verify database schema is unchanged: exactly 4 tables, head at 0006."""

    def test_metadata_tables_are_exact_five(self) -> None:
        import app.db.models  # noqa: F401
        from app.db.base import Base

        phase1_tables = {
            "audit_events",
            "outbox_events",
            "inbox_messages",
            "idempotency_records",
            "jobs",
        }
        canonical_phase4_tables = phase1_tables | {
            "goals",
            "plans",
            "milestones",
            "tasks",
            "task_dependencies",
            "task_risks",
            "workflow_definitions",
            "workflow_instances",
            "workflow_task_mappings",
            "sla_policies",
            "workflow_artifacts",
            # Phase 4A Scheduling (7)
            "resources",
            "resource_availabilities",
            "constraint_definitions",
            "solver_profiles",
            "schedule_jobs",
            "schedules",
            "schedule_assignments",
            # Phase 4B Calendar (1)
            "calendar_links",
            # Phase 7 Calendar Watch (1)
            "calendar_watch_channels",
            # Phase 8D Service Credentials Persistence (1)
            "service_api_credentials",
        }
        actual_tables = set(Base.metadata.tables.keys())
        missing = phase1_tables - actual_tables
        assert phase1_tables <= actual_tables, f"Missing Phase-1 tables: {missing}"
        assert actual_tables == canonical_phase4_tables, (
            f"Expected canonical Phase-4 tables {canonical_phase4_tables}, got {actual_tables}"
        )
        assert len(actual_tables) == 26

    def test_alembic_head_remains_0007(self) -> None:
        import os

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert heads == ["0016"], f"Alembic heads expected ['0016'], got {heads}"

        rev_0007 = script.get_revision("0007")
        assert rev_0007 is not None
        assert rev_0007.down_revision == "0006"

        rev_0008 = script.get_revision("0008")
        assert rev_0008 is not None
        assert rev_0008.down_revision == "0007"

        rev_0009 = script.get_revision("0009")
        assert rev_0009 is not None
        assert rev_0009.down_revision == "0008"

        rev_0010 = script.get_revision("0010")
        assert rev_0010 is not None
        assert rev_0010.down_revision == "0009"

        rev_0011 = script.get_revision("0011")
        assert rev_0011 is not None
        assert rev_0011.down_revision == "0010"

        rev_0012 = script.get_revision("0012")
        assert rev_0012 is not None
        assert rev_0012.down_revision == "0011"

        rev_0013 = script.get_revision("0013")
        assert rev_0013 is not None
        assert rev_0013.down_revision == "0012"

        rev_0014 = script.get_revision("0014")
        assert rev_0014 is not None
        assert rev_0014.down_revision == "0013"

        rev_0015 = script.get_revision("0015")
        assert rev_0015 is not None
        assert rev_0015.down_revision == "0014"

        rev_0016 = script.get_revision("0016")
        assert rev_0016 is not None
        assert rev_0016.down_revision == "0015"

        # Verify no 0017 migration file exists
        versions_dir = os.path.join("app", "db", "migrations", "versions")
        files = os.listdir(versions_dir)
        migration_0017 = [f for f in files if "0017" in f]
        assert len(migration_0017) == 0, f"Found unexpected migration 0017: {migration_0017}"
