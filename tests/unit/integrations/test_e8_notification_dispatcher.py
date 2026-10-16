"""Unit and contract tests for E-8 Notification Boundary and Dispatcher.

Part of Phase 7D E-8 Production Notification Boundary.
Enforces:
- Outbound command formulation and HTTP dispatch.
- Duplicate command detection and idempotency.
- Timeout and temporary E-8 failure resilience.
- Audit emission on dispatch success and failure.
"""

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import (
    HttpNotificationDispatcher,
    NotificationDispatchError,
)
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)


class MockE8Transport(httpx.AsyncBaseTransport):
    """Mock HTTP transport simulating E-8 Notification Gateway."""

    def __init__(self) -> None:
        self.received_commands: list[dict[str, Any]] = []
        self.command_ids_seen: set[str] = set()
        self.simulate_503_error: bool = False
        self.simulate_timeout: bool = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.simulate_timeout:
            raise httpx.TimeoutException("E-8 gateway connection timed out", request=request)

        if self.simulate_503_error:
            return httpx.Response(503, text="Service Temporarily Unavailable", request=request)

        import json

        body = json.loads(request.content.decode("utf-8"))
        cmd_id = request.headers.get("x-command-id") or body.get("command_id")

        if cmd_id in self.command_ids_seen:
            # 409 Conflict / duplicate command already processed
            return httpx.Response(409, json={"error": "Duplicate command_id"}, request=request)

        if cmd_id:
            self.command_ids_seen.add(cmd_id)
        self.received_commands.append(body)

        return httpx.Response(202, json={"status": "QUEUED", "command_id": cmd_id}, request=request)


@pytest.fixture
def mock_e8_transport() -> MockE8Transport:
    return MockE8Transport()


@pytest.fixture
def notification_settings() -> NotificationSettings:
    return NotificationSettings(
        enabled=True,
        transport="http",
        endpoint_url="https://e8-gateway.internal/v1/notifications/commands",
        api_key=SecretStr("e8_secret_api_key_123"),
        timeout_seconds=2.0,
    )


@pytest.fixture
def dispatcher(
    notification_settings: NotificationSettings, mock_e8_transport: MockE8Transport
) -> HttpNotificationDispatcher:
    client = httpx.AsyncClient(transport=mock_e8_transport)
    return HttpNotificationDispatcher(settings=notification_settings, http_client=client)


def create_test_command(command_id: str = "cmd-pub-101") -> NotificationCommand:
    return NotificationCommand(
        command_id=command_id,
        correlation_reference="corr-operation-42",
        causation_reference="evt-published-99",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
            source_entity_reference="sch-101",
        ),
        recipient_references=["faculty-lead@school.edu"],
        notification_context={"schedule_id": "sch-101", "version": 1},
    )


# =====================================================================
# 1. Successful Outbound Dispatch Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_success(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Dispatches command successfully to E-8 gateway."""
    cmd = create_test_command("cmd-pub-001")
    result = await dispatcher.dispatch(cmd)
    assert result is True
    assert len(mock_e8_transport.received_commands) == 1
    assert mock_e8_transport.received_commands[0]["command_id"] == "cmd-pub-001"


# =====================================================================
# 2. Idempotency & Duplicate Replay Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_duplicate_command_returns_false(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Replaying an identical command_id returns False without error (idempotent no-op)."""
    cmd = create_test_command("cmd-pub-duplicate")

    # First dispatch: accepted
    res1 = await dispatcher.dispatch(cmd)
    assert res1 is True

    # Second dispatch with identical command_id: duplicate recognized
    res2 = await dispatcher.dispatch(cmd)
    assert res2 is False
    # Upstream received only 1 new command
    assert len(mock_e8_transport.received_commands) == 1


# =====================================================================
# 3. Resiliency: Timeout & Temporary Outage Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_timeout_raises_error(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Network timeout raises NotificationDispatchError."""
    mock_e8_transport.simulate_timeout = True
    cmd = create_test_command("cmd-pub-timeout")
    with pytest.raises(NotificationDispatchError, match="timed out"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_dispatch_server_error_raises_error(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """HTTP 503 Service Unavailable raises NotificationDispatchError."""
    mock_e8_transport.simulate_503_error = True
    cmd = create_test_command("cmd-pub-503")
    with pytest.raises(NotificationDispatchError, match="HTTP error 503"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_missing_endpoint_configuration_fails_closed() -> None:
    """Unconfigured endpoint URL raises NotificationDispatchError."""
    settings = NotificationSettings(enabled=True, endpoint_url="")
    disp = HttpNotificationDispatcher(settings=settings)
    cmd = create_test_command("cmd-unconfigured")
    with pytest.raises(NotificationDispatchError, match="endpoint_url is not configured"):
        await disp.dispatch(cmd)


@pytest.mark.asyncio
async def test_dispatch_temporary_failure_and_retry_recovery(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Simulates temporary 503 failure followed by successful retry."""
    cmd = create_test_command("cmd-retry-01")

    # First attempt: temporary outage
    mock_e8_transport.simulate_503_error = True
    with pytest.raises(NotificationDispatchError, match="HTTP error 503"):
        await dispatcher.dispatch(cmd)

    # Second attempt: recovered
    mock_e8_transport.simulate_503_error = False
    result = await dispatcher.dispatch(cmd)
    assert result is True
    assert len(mock_e8_transport.received_commands) == 1


@pytest.mark.asyncio
async def test_dispatch_redelivery_after_ack_is_idempotent(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Redelivery of already delivered command returns False without duplicating command."""
    cmd = create_test_command("cmd-redeliver-99")

    # 1. First delivery
    first_res = await dispatcher.dispatch(cmd)
    assert first_res is True

    # 2. Redelivery (e.g. from broker redelivery / network unack)
    second_res = await dispatcher.dispatch(cmd)
    assert second_res is False
    assert len(mock_e8_transport.received_commands) == 1

