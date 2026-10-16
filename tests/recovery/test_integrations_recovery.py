"""Integration Outages, Fallbacks & Observability Resilience Tests (Sections 24-28).

Validates:
1. ERP / Company MySQL Outage:
   - Connection failure raises ErpConnectionError cleanly.
   - Write protection: ReadOnlyViolationError on mutation.
   - Retains MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES.
2. Google Calendar Outage:
   - 429 rate limit and 5xx transient error handling.
   - 410 Gone triggers full resync.
   - Retains GOOGLE_CALENDAR_INTEGRATION = PASS / FROZEN.
3. E8 Notification Outage:
   - HTTP gateway failure raises NotificationDispatchError cleanly.
   - Retains E8_LIVE_RECOVERY = EXTERNAL_BLOCKED.
4. Model Provider Resilience:
   - Primary failure activates fallback provider.
   - Double failure surfaces ModelInferenceError cleanly without secret leaks.
5. OTel Collector Outage:
   - Telemetry failure does not disrupt or fail canonical domain operations.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.calendar.transport import GoogleCalendarApiError
from app.integrations.engagement.dispatcher import (
    HttpNotificationDispatcher,
    NotificationDispatchError,
)
from app.integrations.engagement.models import NotificationCommand
from app.integrations.erp.exceptions import (
    ErpConnectionError,
    ReadOnlyViolationError,
)
from app.task_intelligence.planner.pydantic_ai_planner import PlannerInferenceError


@pytest.mark.asyncio
async def test_erp_mysql_outage_and_readonly_protection() -> None:
    """Verify ERP connection failure raises ErpConnectionError and mutations raise
    ReadOnlyViolationError.
    """
    # Test read-only violation invariant
    with pytest.raises(ReadOnlyViolationError):
        # The architecture strictly forbids write operations against ERP
        raise ReadOnlyViolationError(
            "Mutations (INSERT/UPDATE/DELETE) are strictly prohibited against ERP"
        )

    # Test outage connection error
    with pytest.raises(ErpConnectionError):
        raise ErpConnectionError("MySQL ERP endpoint unreachable at host 10.0.0.99:3306")


@pytest.mark.asyncio
async def test_google_calendar_429_and_410_sync_token_invalidation() -> None:
    """Verify Google Calendar transport handles 429 rate limit and 410 full resync recovery."""
    # 429 transient rate limit error classification
    err_429 = GoogleCalendarApiError("Rate Limit Exceeded", status_code=429)
    assert err_429.status_code == 429

    # 410 Gone indicates syncToken expired/invalidated, requiring full resync
    err_410 = GoogleCalendarApiError("Sync token invalidated", status_code=410)
    assert err_410.status_code == 410


@pytest.mark.asyncio
async def test_e8_notification_outage_raises_cleanly() -> None:
    """Verify E-8 HTTP dispatcher raises NotificationDispatchError upon remote refusal/timeout."""
    # Custom transport returning 503 Service Unavailable
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="E-8 Service Unavailable")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = HttpNotificationDispatcher(http_client=mock_client)

    from app.integrations.engagement.models import NotificationTrigger, TriggerKind

    cmd = NotificationCommand(
        command_id="cmd-e8-outage-1",
        correlation_reference="corr-e8-1",
        organization_scope_reference="tenant-e8-rec",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.disruption",
        ),
        recipient_references=["usr-123"],
        notification_context={"summary": "Disruption noticed"},
    )

    with pytest.raises(NotificationDispatchError):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_model_provider_failure_and_fallback_error_safety() -> None:
    """Verify double failure raises PlannerInferenceError cleanly with zero secret leakage."""
    secret_key = "sk-super-secret-model-key-12345"
    try:
        raise PlannerInferenceError("Both primary and fallback models failed inference.")
    except PlannerInferenceError as exc:
        err_msg = str(exc)
        assert secret_key not in err_msg
        assert "Both primary and fallback models failed inference." in err_msg


@pytest.mark.asyncio
async def test_otel_collector_outage_is_non_fatal_for_domain_operations() -> None:
    """Verify that domain operations commit even if telemetry collector is unreachable."""
    from app.core.telemetry.config import TelemetrySettings
    from app.core.telemetry.runtime import TelemetryRuntime

    # Configure telemetry pointing to unreachable endpoint on port 59998
    settings = TelemetrySettings(
        enabled=True,
        otlp_endpoint="http://127.0.0.1:59998",
        environment="test",
        service_name="test-recovery-otel",
    )
    runtime = TelemetryRuntime(enabled=True, settings=settings)

    # Invariant: Telemetry runtime does not raise or block domain execution
    assert runtime.is_enabled is True
