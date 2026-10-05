"""Unit tests for platform health and readiness endpoints (app/api/v1/router.py)."""

from fastapi.testclient import TestClient

from app.api.v1.schemas import HealthResponse, ReadinessResponse
from app.main import app


def test_health_liveness_endpoint() -> None:
    """Verify /api/v1/health returns HTTP 200 with typed HealthResponse."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    # Contract validation against Pydantic schema
    health_obj = HealthResponse(**data)
    assert health_obj.status == "ok"
    assert health_obj.service == "operations-engine"
    assert health_obj.environment in {"local", "development", "test", "staging", "production"}


def test_readiness_endpoint() -> None:
    """Verify /api/v1/ready returns HTTP 200 with typed ReadinessResponse."""
    client = TestClient(app)
    response = client.get("/api/v1/ready")
    assert response.status_code == 200

    data = response.json()
    # Contract validation against Pydantic schema
    ready_obj = ReadinessResponse(**data)
    assert ready_obj.status == "ready"
    assert ready_obj.service == "operations-engine"
    assert ready_obj.environment in {"local", "development", "test", "staging", "production"}


def test_root_endpoint_metadata() -> None:
    """Verify root endpoint responses remain stable and safe."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "engine" in data
    assert data["status"] == "online"
    assert data["docs"] == "/docs"
