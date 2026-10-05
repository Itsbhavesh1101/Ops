"""Unit tests for request correlation tracking and middleware (app/core/correlation.py)."""

import uuid

from fastapi.testclient import TestClient

from app.core.correlation import (
    correlation_id_ctx,
    get_correlation_id,
    validate_correlation_id,
)
from app.main import app


def test_validate_correlation_id_missing() -> None:
    """Missing or empty correlation ID produces a fresh UUID4."""
    cid = validate_correlation_id(None)
    assert cid is not None
    # Valid UUID check
    uuid_obj = uuid.UUID(cid)
    assert str(uuid_obj) == cid

    cid_empty = validate_correlation_id("   ")
    assert cid_empty is not None
    assert str(uuid.UUID(cid_empty)) == cid_empty


def test_validate_correlation_id_valid() -> None:
    """Valid correlation IDs are preserved verbatim."""
    valid_ids = [
        "req-12345-abc",
        "service_a.job_b:42",
        "UUID-123-456_789",
    ]
    for vid in valid_ids:
        assert validate_correlation_id(vid) == vid


def test_validate_correlation_id_malformed() -> None:
    """Malformed correlation IDs containing unsafe characters are replaced with fresh UUID4."""
    malformed_ids = [
        "id-with-newline\n",
        "id with space",
        "id;drop table;",
        "<script>alert(1)</script>",
        "control\x00char",
    ]
    for mid in malformed_ids:
        safe_id = validate_correlation_id(mid)
        assert safe_id != mid
        assert str(uuid.UUID(safe_id)) == safe_id


def test_validate_correlation_id_oversized() -> None:
    """Oversized correlation IDs exceeding 128 characters are replaced with fresh UUID4."""
    long_id = "a" * 129
    safe_id = validate_correlation_id(long_id)
    assert safe_id != long_id
    assert str(uuid.UUID(safe_id)) == safe_id


def test_middleware_missing_header_generates_id() -> None:
    """Client request without correlation header receives a generated ID in response header."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "X-Correlation-ID" in response.headers
    cid = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(cid)) == cid


def test_middleware_preserves_valid_incoming_id() -> None:
    """Client request with valid correlation header preserves the ID in response."""
    client = TestClient(app)
    custom_id = "test-client-corr-id-99"
    response = client.get("/", headers={"X-Correlation-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_id


def test_middleware_replaces_malformed_incoming_id() -> None:
    """Client request with unsafe correlation header receives a safe replacement ID."""
    client = TestClient(app)
    unsafe_id = "bad\nid\r\nInjected-Header: evil"
    response = client.get("/", headers={"X-Correlation-ID": unsafe_id})
    assert response.status_code == 200
    res_id = response.headers.get("X-Correlation-ID")
    assert res_id is not None
    assert res_id != unsafe_id
    assert "\n" not in res_id


def test_contextvar_isolation_across_requests() -> None:
    """Ensure contextvar does not leak between requests or after request completion."""
    client = TestClient(app)
    # Outside of active request, contextvar should be None
    assert get_correlation_id() is None

    client.get("/", headers={"X-Correlation-ID": "req-1"})
    assert get_correlation_id() is None

    client.get("/", headers={"X-Correlation-ID": "req-2"})
    assert get_correlation_id() is None

    assert correlation_id_ctx.get() is None
