"""Unit tests for hardened readiness probes and dependency health distinction (Task 4).

Validates:
1. Shallow readiness probe (default / deep=False): returns HTTP 200 and probe='shallow'.
2. Deep readiness probe when PostgreSQL is healthy: returns HTTP 200, status='ready'.
3. Deep readiness probe when PostgreSQL is unreachable/timed out: returns HTTP 503.
4. Deep readiness probe when PostgreSQL is healthy but broker is degraded:
   - Non-strict mode: returns HTTP 200, status='degraded' (outbox accumulation).
   - Strict mode: returns HTTP 503, status='not_ready'.
5. Health liveness probe remains lightweight and returns HTTP 200 'ok'.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.schemas import HealthResponse, ReadinessResponse
from app.core.config import Settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_liveness_probe_lightweight(client: TestClient) -> None:
    """Verify /api/v1/health returns HTTP 200 without probing stateful backends."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    model = HealthResponse(**data)
    assert model.status == "ok"
    assert model.service == "operations-engine"


def test_shallow_readiness_probe_success(client: TestClient) -> None:
    """Verify shallow readiness returns ready without blocking on database connectivity."""
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    model = ReadinessResponse(**data)
    assert model.status == "ready"
    assert model.probe == "shallow"


@pytest.mark.asyncio
async def test_deep_readiness_probe_database_healthy(client: TestClient) -> None:
    """Verify deep readiness returns HTTP 200 when database connectivity probe succeeds."""
    mock_db = MagicMock()
    mock_db.check_readiness = AsyncMock(return_value=True)
    mock_db.get_pool_status = MagicMock(
        return_value={"size": 5, "checked_out": 1, "overflow": 0, "checked_in": 4}
    )

    mock_settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://app:app@localhost:5432/app"),
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.api.v1.composition.get_shared_db_manager", return_value=mock_db), \
         patch("app.events.health.check_broker_ready", new_callable=AsyncMock, return_value=True), \
         patch.dict("os.environ", {"E7_RABBITMQ_URL": "amqp://guest:guest@localhost:5672/"}):

        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 200
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "ready"
        assert model.probe == "deep"
        assert model.dependencies is not None
        assert model.dependencies["database"]["status"] == "healthy"
        assert model.dependencies["database"]["pool"]["size"] == 5
        assert model.dependencies["broker"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_deep_readiness_probe_database_unhealthy_returns_503(client: TestClient) -> None:
    """Verify deep readiness returns HTTP 503 when required database dependency fails."""
    mock_db = MagicMock()
    mock_db.check_readiness = AsyncMock(return_value=False)

    mock_settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://app:app@localhost:5432/app"),
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.api.v1.composition.get_shared_db_manager", return_value=mock_db), \
         patch("app.events.health.check_broker_ready", new_callable=AsyncMock, return_value=True):

        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 503
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "not_ready"
        assert model.probe == "deep"
        assert model.dependencies is not None
        assert model.dependencies["database"]["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_deep_readiness_probe_broker_degraded_async_policy(client: TestClient) -> None:
    """Verify deep readiness returns HTTP 200 degraded when broker is down but DB is healthy."""
    mock_db = MagicMock()
    mock_db.check_readiness = AsyncMock(return_value=True)
    mock_db.get_pool_status = MagicMock(
        return_value={"size": 5, "checked_out": 0, "overflow": 0, "checked_in": 5}
    )

    mock_settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://app:app@localhost:5432/app"),
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.api.v1.composition.get_shared_db_manager", return_value=mock_db), \
         patch(
             "app.events.health.check_broker_ready", new_callable=AsyncMock, return_value=False
         ), \
         patch.dict("os.environ", {"E7_RABBITMQ_URL": "amqp://guest:guest@localhost:5672/"}):

        # In standard mode, degraded broker preserves HTTP 200 (outbox writes still succeed)
        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 200
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "degraded"
        assert model.dependencies is not None
        assert model.dependencies["database"]["status"] == "healthy"
        assert model.dependencies["broker"]["status"] == "degraded"

        # In strict mode, broker outage fails readiness with HTTP 503
        strict_response = client.get("/api/v1/ready?deep=true&strict=true")
        assert strict_response.status_code == 503
        strict_data = strict_response.json()
        strict_model = ReadinessResponse(**strict_data)
        assert strict_model.status == "not_ready"


@pytest.mark.asyncio
async def test_deep_readiness_probe_missing_database_url_returns_503(client: TestClient) -> None:
    """Verify deep readiness returns HTTP 503 when required database is not configured."""
    mock_settings = Settings(
        database_url=None,
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.events.health.check_broker_ready", new_callable=AsyncMock, return_value=True), \
         patch.dict("os.environ", {"E7_RABBITMQ_URL": "amqp://guest:guest@localhost:5672/"}):

        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 503
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "not_ready"
        assert model.dependencies is not None
        assert model.dependencies["database"]["status"] == "not_configured"


@pytest.mark.asyncio
async def test_deep_readiness_probe_broker_not_configured_async_and_strict(
    client: TestClient,
) -> None:
    """Verify unconfigured broker yields degraded (200) in standard and not_ready (503) strict."""
    mock_db = MagicMock()
    mock_db.check_readiness = AsyncMock(return_value=True)
    mock_db.get_pool_status = MagicMock(
        return_value={"size": 5, "checked_out": 0, "overflow": 0, "checked_in": 5}
    )

    mock_settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://app:app@localhost:5432/app"),
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.api.v1.composition.get_shared_db_manager", return_value=mock_db), \
         patch.dict("os.environ", {}, clear=True):

        # Standard mode: degraded 200
        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 200
        data = response.json()
        model = ReadinessResponse(**data)
        assert model.status == "degraded"
        assert model.dependencies is not None
        assert model.dependencies["broker"]["status"] == "not_configured"

        # Strict mode: fails with 503
        strict_response = client.get("/api/v1/ready?deep=true&strict=true")
        assert strict_response.status_code == 503
        assert strict_response.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_deep_readiness_probe_never_discloses_credentials(client: TestClient) -> None:
    """Verify raw database and broker credentials never appear in deep readiness response."""
    raw_secret_pass = "P@ssw0rd999_SuperSecret"
    mock_db = MagicMock()
    mock_db.check_readiness = AsyncMock(return_value=True)
    mock_db.get_pool_status = MagicMock(
        return_value={"size": 1, "checked_out": 0, "overflow": 0, "checked_in": 1}
    )

    mock_settings = Settings(
        database_url=SecretStr(f"postgresql+asyncpg://super_user:{raw_secret_pass}@db.host:5432/app"),
        environment="test",
    )

    with patch("app.api.v1.router.get_settings", return_value=mock_settings), \
         patch("app.api.v1.composition.get_shared_db_manager", return_value=mock_db), \
         patch("app.events.health.check_broker_ready", new_callable=AsyncMock, return_value=True), \
         patch.dict("os.environ", {"E7_RABBITMQ_URL": f"amqp://broker_user:{raw_secret_pass}@rabbit.host:5672/"}):

        response = client.get("/api/v1/ready?deep=true")
        assert response.status_code == 200
        raw_text = response.text
        assert raw_secret_pass not in raw_text
        assert "super_user" not in raw_text
        assert "broker_user" not in raw_text
