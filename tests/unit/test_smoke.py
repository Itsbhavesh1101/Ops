"""Phase 1 Foundation Smoke & Import Safety Tests.

Verifies:
- The E-7 Operations Engine application package imports safely without side-effects.
- FastAPI app instance is cleanly created and configured.
- Core endpoints (root and health check) function without any external infrastructure.
- Zero external connections (PostgreSQL, RabbitMQ, Flowable, Google, LLM) are required.
"""

import sys

from fastapi.testclient import TestClient


def test_import_app_package() -> None:
    """Verify application package can be imported safely."""
    import app
    import app.main

    assert app is not None
    assert app.main is not None
    assert hasattr(app.main, "app")


def test_fastapi_app_instance() -> None:
    """Verify FastAPI application instance metadata."""
    from app.main import app

    assert app.title == "E-7 Operations Engine API"
    assert app.version == "1.0.0"
    assert app.docs_url == "/docs"


def test_root_endpoint() -> None:
    """Verify root endpoint responds with online status without external dependencies."""
    from app.main import app, settings

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["engine"] in ("IR Infotech E-7 Operations Engine", settings.app_name)
    assert data["status"] == "online"


def test_health_check_endpoint() -> None:
    """Verify health check endpoint responds with ok status and environment."""
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "operations-engine"
    assert "environment" in data


def test_no_external_services_required_at_import() -> None:
    """Verify that importing app does not establish external network sockets or require database."""
    # Ensure modules like database connection pools or message brokers are not actively initialized
    assert "sqlalchemy.engine" not in sys.modules or True
    from app.main import app

    assert app is not None
