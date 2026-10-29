"""Unit and contract tests for Google Calendar REST v3 adapter and webhook handling.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- Outbound create, update, delete operations.
- ETag concurrency (If-Match headers and 412 Precondition Failed handling).
- Incremental syncToken consumption and HTTP 410 Gone recovery.
- Google Calendar push notification webhook verification and replay protection.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.integrations.calendar.auth import (
    DeterministicCalendarTokenProvider,
)
from app.integrations.calendar.codec import (
    google_calendar_event_id,
    is_legal_google_calendar_event_id,
)
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.dtos import CalendarEventDTO, SyncStatus
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.webhook import (
    GoogleCalendarWebhookHandler,
)


class MockGoogleCalendarTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport simulating Google Calendar REST v3 API responses."""

    def __init__(self) -> None:
        self.events_db: dict[str, dict[str, Any]] = {}
        self.return_410_on_sync_token: bool = False
        self.return_412_on_etag_mismatch: bool = True
        self.force_get_404_on_event_ids: set[str] = set()
        self.requests_log: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests_log.append(request)
        url_path = request.url.path
        method = request.method

        # 0. OAuth2 token endpoint: POST https://oauth2.googleapis.com/token
        if "oauth2.googleapis.com" in str(request.url) or url_path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.mock_token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        # 1. Create event: POST /calendar/v3/calendars/{cal}/events
        if method == "POST" and url_path.endswith("/events"):
            data = json.loads(request.content.decode("utf-8"))
            event_id = data.get("id", f"ev-{len(self.events_db) + 1}")
            if event_id in self.events_db and self.events_db[event_id].get("status") != "cancelled":
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "errors": [
                                {
                                    "domain": "global",
                                    "reason": "duplicate",
                                    "message": "The requested identifier already exists.",
                                }
                            ],
                            "code": 409,
                            "message": "The requested identifier already exists.",
                        }
                    },
                )
            data["id"] = event_id
            canonical_id = (
                data.get("extendedProperties", {})
                .get("private", {})
                .get("external_event_id", event_id)
            )
            data["etag"] = f'"etag-{canonical_id}-v1"'
            data["updated"] = datetime.now(UTC).isoformat()
            self.events_db[event_id] = data
            return httpx.Response(201, json=data)

        # 2. Update event: PUT /calendar/v3/calendars/{cal}/events/{eventId}
        if method == "PUT" and "/events/" in url_path:
            event_id = url_path.split("/")[-1]
            if event_id not in self.events_db:
                return httpx.Response(404, json={"error": {"message": "Not Found"}})

            if_match = request.headers.get("if-match")
            current_etag = self.events_db[event_id].get("etag")
            if self.return_412_on_etag_mismatch and if_match and if_match != current_etag:
                return httpx.Response(412, json={"error": {"message": "Precondition Failed"}})

            data = json.loads(request.content.decode("utf-8"))
            data["id"] = event_id
            canonical_id = (
                data.get("extendedProperties", {})
                .get("private", {})
                .get("external_event_id", event_id)
            )
            data["etag"] = f'"etag-{canonical_id}-v2"'
            data["updated"] = datetime.now(UTC).isoformat()
            self.events_db[event_id] = data
            return httpx.Response(200, json=data)

        # 3. Delete event: DELETE /calendar/v3/calendars/{cal}/events/{eventId}
        if method == "DELETE" and "/events/" in url_path:
            event_id = url_path.split("/")[-1]
            if event_id in self.events_db:
                self.events_db[event_id]["status"] = "cancelled"
            return httpx.Response(204)

        # 4. Get single event: GET /calendar/v3/calendars/{cal}/events/{eventId}
        if method == "GET" and "/events/" in url_path:
            event_id = url_path.split("/")[-1]
            if event_id in self.force_get_404_on_event_ids:
                return httpx.Response(404, json={"error": {"message": "Not Found"}})
            if event_id in self.events_db and self.events_db[event_id].get("status") != "cancelled":
                return httpx.Response(200, json=self.events_db[event_id])
            return httpx.Response(404, json={"error": {"message": "Not Found"}})

        # 5. List / Sync events: GET /calendar/v3/calendars/{cal}/events
        if method == "GET" and url_path.endswith("/events"):
            sync_token = request.url.params.get("syncToken")
            if sync_token and self.return_410_on_sync_token:
                # 410 Gone: sync token expired
                self.return_410_on_sync_token = False  # Only 410 on first syncToken query
                return httpx.Response(410, json={"error": {"message": "Sync token is expired"}})

            items = list(self.events_db.values())
            return httpx.Response(
                200,
                json={
                    "items": items,
                    "nextSyncToken": "fresh-next-sync-token-999",
                },
            )

        return httpx.Response(404, json={"error": {"message": "Endpoint Not Found"}})


@pytest.fixture
def mock_transport() -> MockGoogleCalendarTransport:
    return MockGoogleCalendarTransport()


@pytest.fixture(scope="module")
def rsa_key_pem() -> str:
    """Generate in-memory RSA private key in PEM format for deterministic testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture
def test_settings(rsa_key_pem: str) -> GoogleCalendarSettings:
    return GoogleCalendarSettings(
        enabled=True,
        auth_mode="service_account_key",
        client_email="service-account@school.iam.gserviceaccount.com",
        private_key=SecretStr(rsa_key_pem),
        calendar_id="institutional-calendar-id@group.calendar.google.com",
        webhook_token=SecretStr("secret_webhook_shared_token_42"),
    )


@pytest.fixture
def google_adapter(
    test_settings: GoogleCalendarSettings, mock_transport: MockGoogleCalendarTransport
) -> GoogleCalendarAdapter:
    client = httpx.AsyncClient(transport=mock_transport)
    return GoogleCalendarAdapter(settings=test_settings, http_client=client)


# =====================================================================
# 1. Outbound Event Lifecycle Tests
# =====================================================================

@pytest.mark.asyncio
async def test_create_event_outbound_success(google_adapter: GoogleCalendarAdapter) -> None:
    """Creates event in Google Calendar and returns SYNCED status with ETag."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-001",
        external_calendar_id="institutional-calendar-id",
        title="Grade 10 Mathematics Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
        description="Term 2 Final Examination",
        location="Room 101",
    )
    result = await google_adapter.create_event("tenant-1", dto)
    assert result.status == SyncStatus.SYNCED
    assert result.external_event_id == "exam-slot-001"
    assert result.etag == '"etag-exam-slot-001-v1"'


@pytest.mark.asyncio
async def test_create_event_encodes_google_id_and_preserves_external_id(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """Proves external_event_id 'e7-test-ev-1234abcd' is NOT copied directly into Google 'id'.

    Instead it produces deterministic legal provider ID, and original external_event_id
    remains preserved for E-7 canonical mapping.
    """
    now = datetime.now(UTC)
    raw_external_id = "e7-test-ev-1234abcd"
    dto = CalendarEventDTO(
        external_event_id=raw_external_id,
        external_calendar_id="institutional-calendar-id",
        title="Test Encoding Event",
        start_at=now,
        end_at=now + timedelta(hours=1),
    )
    result = await google_adapter.create_event("tenant-1", dto)
    assert result.status == SyncStatus.SYNCED
    # Canonical ID is preserved in result
    assert result.external_event_id == raw_external_id

    # Inspect outgoing wire request captured by mock transport
    assert len(mock_transport.requests_log) > 0
    post_req = [r for r in mock_transport.requests_log if r.method == "POST"][-1]
    payload = json.loads(post_req.content.decode("utf-8"))

    # The wire payload ID is NOT the hyphenated raw external ID
    assert payload["id"] != raw_external_id
    assert "-" not in payload["id"]
    # The wire payload ID matches google_calendar_event_id and satisfies regex
    expected_google_id = google_calendar_event_id(raw_external_id)
    assert payload["id"] == expected_google_id
    assert is_legal_google_calendar_event_id(payload["id"])

    # Original external_event_id is preserved in extendedProperties
    assert (
        payload.get("extendedProperties", {})
        .get("private", {})
        .get("external_event_id")
        == raw_external_id
    )

    # Fetching the event resolves the canonical external ID
    fetched = await google_adapter.get_event("tenant-1", raw_external_id)
    assert fetched is not None
    assert fetched.external_event_id == raw_external_id
    assert fetched.title == "Test Encoding Event"


@pytest.mark.asyncio
async def test_create_event_ambiguous_retry_409_recovers_idempotently(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """Ambiguous create retry returns 409; adapter GETs existing event,
    confirms identity, and returns SYNCED.
    """
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="ambiguous-event-001",
        external_calendar_id="institutional-calendar-id",
        title="Ambiguous Retry Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    # 1. First create succeeds
    res1 = await google_adapter.create_event("tenant-1", dto)
    assert res1.status == SyncStatus.SYNCED
    initial_etag = res1.etag
    assert initial_etag is not None
    assert len(mock_transport.events_db) == 1

    # 2. Retry create with same external_event_id (simulating retry after ambiguous failure)
    res2 = await google_adapter.create_event("tenant-1", dto)
    assert res2.status == SyncStatus.SYNCED
    assert res2.external_event_id == "ambiguous-event-001"
    assert res2.etag == initial_etag
    # Proves no duplicate / second event was created in provider
    assert len(mock_transport.events_db) == 1


@pytest.mark.asyncio
async def test_create_event_409_collision_fails_closed(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """When 409 occurs and existing event has DIFFERENT canonical external ID,
    fails closed with CONFLICT.
    """
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="colliding-event-001",
        external_calendar_id="institutional-calendar-id",
        title="Colliding Event",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    google_id = google_calendar_event_id("colliding-event-001")
    # Pre-populate an existing event in mock with a DIFFERENT external_event_id
    mock_transport.events_db[google_id] = {
        "id": google_id,
        "summary": "Existing Unrelated Event",
        "start": {"dateTime": now.isoformat()},
        "end": {"dateTime": (now + timedelta(hours=1)).isoformat()},
        "etag": '"etag-different-1"',
        "extendedProperties": {
            "private": {
                "external_event_id": "different-canonical-id-999",
            }
        },
    }

    # Attempt create -> POST gets 409 -> GET verifies identity -> mismatch -> CONFLICT
    res = await google_adapter.create_event("tenant-1", dto)
    assert res.status == SyncStatus.CONFLICT
    assert res.external_event_id == "colliding-event-001"
    assert "ownership check failed" in (res.error_message or "")
    assert "different-canonical-id-999" in (res.error_message or "")


@pytest.mark.asyncio
async def test_create_event_409_get_404_fails_transiently(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """When 409 occurs but verification GET returns 404, fails closed with FAILED status."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="ghost-event-001",
        external_calendar_id="institutional-calendar-id",
        title="Ghost Event",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    google_id = google_calendar_event_id("ghost-event-001")
    # First create
    await google_adapter.create_event("tenant-1", dto)
    # Configure mock to return 404 on GET for this event_id
    mock_transport.force_get_404_on_event_ids.add(google_id)

    # Attempt create again -> POST gets 409 -> GET gets 404 -> FAILED
    res = await google_adapter.create_event("tenant-1", dto)
    assert res.status == SyncStatus.FAILED
    assert "404 Not Found" in (res.error_message or "")


@pytest.mark.asyncio
async def test_create_event_repeated_recovery_stability(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """Multiple consecutive retries of ambiguous create consistently recover to SYNCED."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="stable-retry-event-001",
        external_calendar_id="institutional-calendar-id",
        title="Stable Retry Event",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    # First create
    res0 = await google_adapter.create_event("tenant-1", dto)
    assert res0.status == SyncStatus.SYNCED
    initial_etag = res0.etag

    # Repeated calls all recover stably without drifting or corrupting state
    for _ in range(4):
        res = await google_adapter.create_event("tenant-1", dto)
        assert res.status == SyncStatus.SYNCED
        assert res.external_event_id == "stable-retry-event-001"
        assert res.etag == initial_etag

    assert len(mock_transport.events_db) == 1


@pytest.mark.asyncio
async def test_create_event_preserves_canonical_id_across_409_recovery(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """Canonical external_event_id remains unmodified across 409 recovery flow."""
    raw_id = "sis/class/2026/section-42"
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id=raw_id,
        external_calendar_id="institutional-calendar-id",
        title="Section 42",
        start_at=now,
        end_at=now + timedelta(hours=1),
    )
    res1 = await google_adapter.create_event("tenant-1", dto)
    assert res1.status == SyncStatus.SYNCED
    assert res1.external_event_id == raw_id

    # Second create (409 duplicate)
    res2 = await google_adapter.create_event("tenant-1", dto)
    assert res2.status == SyncStatus.SYNCED
    assert res2.external_event_id == raw_id


@pytest.mark.asyncio
async def test_create_event_no_credentials_logged_on_409(
    google_adapter: GoogleCalendarAdapter,
    mock_transport: MockGoogleCalendarTransport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure no auth tokens, Bearer tokens, or secret keys are logged during 409 recovery."""
    import logging

    caplog.set_level(logging.DEBUG)

    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="redaction-check-001",
        external_calendar_id="institutional-calendar-id",
        title="Redaction Check",
        start_at=now,
        end_at=now + timedelta(hours=1),
    )
    await google_adapter.create_event("tenant-1", dto)
    await google_adapter.create_event("tenant-1", dto)

    for record in caplog.records:
        msg = record.getMessage()
        assert "ya29." not in msg
        assert "Bearer " not in msg
        assert "private_key" not in msg
        assert "BEGIN PRIVATE KEY" not in msg


@pytest.mark.asyncio
async def test_update_event_with_matching_etag(google_adapter: GoogleCalendarAdapter) -> None:
    """Updates event when expected ETag matches provider."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-002",
        external_calendar_id="institutional-calendar-id",
        title="Grade 10 Science Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    # First create
    create_res = await google_adapter.create_event("tenant-1", dto)
    initial_etag = create_res.etag

    # Then update with matching ETag
    updated_dto = CalendarEventDTO(
        external_event_id="exam-slot-002",
        external_calendar_id="institutional-calendar-id",
        title="Grade 10 Science Exam (Updated)",
        start_at=now,
        end_at=now + timedelta(hours=3),
    )
    update_res = await google_adapter.update_event(
        "tenant-1", "exam-slot-002", updated_dto, expected_etag=initial_etag
    )
    assert update_res.status == SyncStatus.SYNCED
    assert update_res.etag == '"etag-exam-slot-002-v2"'


@pytest.mark.asyncio
async def test_update_event_etag_concurrency_conflict(
    google_adapter: GoogleCalendarAdapter,
) -> None:
    """Returns CONFLICT (412 Precondition Failed) when remote ETag does not match."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-003",
        external_calendar_id="institutional-calendar-id",
        title="Conflict Test Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    await google_adapter.create_event("tenant-1", dto)

    # Attempt update with stale/mismatched ETag
    stale_etag = '"stale-etag-value"'
    update_res = await google_adapter.update_event(
        "tenant-1", "exam-slot-003", dto, expected_etag=stale_etag
    )
    assert update_res.status == SyncStatus.CONFLICT
    assert "precondition failed" in str(update_res.error_message).lower()


@pytest.mark.asyncio
async def test_delete_event(google_adapter: GoogleCalendarAdapter) -> None:
    """Deletes an event from Google Calendar."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-004",
        external_calendar_id="institutional-calendar-id",
        title="Delete Test Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    await google_adapter.create_event("tenant-1", dto)

    deleted = await google_adapter.delete_event("tenant-1", "exam-slot-004")
    assert deleted is True

    # Subsequent get returns None
    fetched = await google_adapter.get_event("tenant-1", "exam-slot-004")
    assert fetched is None


# =====================================================================
# 2. Incremental Synchronization & 410 Recovery Tests
# =====================================================================

@pytest.mark.asyncio
async def test_incremental_sync_normal_flow(google_adapter: GoogleCalendarAdapter) -> None:
    """Fetches incremental changes and extracts nextSyncToken."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-005",
        external_calendar_id="institutional-calendar-id",
        title="Sync Test Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    await google_adapter.create_event("tenant-1", dto)

    changes = await google_adapter.fetch_incremental_changes(
        "tenant-1", sync_token="prior-token-123"
    )
    assert len(changes.created_or_updated) == 1
    assert changes.created_or_updated[0].external_event_id == "exam-slot-005"
    assert changes.next_sync_token == "fresh-next-sync-token-999"


@pytest.mark.asyncio
async def test_incremental_sync_recovers_from_410_gone(
    google_adapter: GoogleCalendarAdapter, mock_transport: MockGoogleCalendarTransport
) -> None:
    """When syncToken returns HTTP 410 Gone, automatically falls back to full sync."""
    now = datetime.now(UTC)
    dto = CalendarEventDTO(
        external_event_id="exam-slot-006",
        external_calendar_id="institutional-calendar-id",
        title="410 Recovery Exam",
        start_at=now,
        end_at=now + timedelta(hours=2),
    )
    await google_adapter.create_event("tenant-1", dto)

    # Force 410 on next query with syncToken
    mock_transport.return_410_on_sync_token = True

    changes = await google_adapter.fetch_incremental_changes(
        "tenant-1", sync_token="expired-token-410"
    )
    assert len(changes.created_or_updated) == 1
    assert changes.created_or_updated[0].external_event_id == "exam-slot-006"
    assert changes.next_sync_token == "fresh-next-sync-token-999"


# =====================================================================
# 3. Push Notification Webhook Verification & Replay Protection
# =====================================================================

def test_webhook_handler_handshake_sync_state() -> None:
    """Google initial 'sync' handshake is acknowledged with no side effects."""
    handler = GoogleCalendarWebhookHandler(expected_channel_token="secret_token_123")
    headers = {
        "X-Goog-Channel-ID": "channel-uuid-1",
        "X-Goog-Resource-State": "sync",
        "X-Goog-Channel-Token": "secret_token_123",
        "X-Goog-Message-Number": "1",
    }
    result = handler.handle_webhook_headers(headers)
    assert result.status == "SYNC_HANDSHAKE_ACKNOWLEDGED"
    assert result.channel_id == "channel-uuid-1"


def test_webhook_handler_token_mismatch_rejected() -> None:
    """Invalid channel token is rejected."""
    handler = GoogleCalendarWebhookHandler(expected_channel_token="correct_token")
    headers = {
        "X-Goog-Channel-ID": "channel-uuid-1",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Channel-Token": "wrong_token",
        "X-Goog-Message-Number": "2",
    }
    result = handler.handle_webhook_headers(headers)
    assert result.status == "REJECTED"
    assert "token verification failed" in result.message.lower()


def test_webhook_handler_replay_protection() -> None:
    """Replayed notification message with identical message number is deduplicated."""
    handler = GoogleCalendarWebhookHandler(expected_channel_token="secret_token_123")
    headers = {
        "X-Goog-Channel-ID": "channel-uuid-1",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Channel-Token": "secret_token_123",
        "X-Goog-Message-Number": "42",
    }

    # First delivery: PROCESSED
    first_res = handler.handle_webhook_headers(headers)
    assert first_res.status == "PROCESSED"

    # Replayed delivery: DUPLICATE_REPLAYED
    replay_res = handler.handle_webhook_headers(headers)
    assert replay_res.status == "DUPLICATE_REPLAYED"
    assert "Duplicate notification" in replay_res.message


# =====================================================================
# 4. Deterministic Calendar Token Provider & Auth Recovery Tests
# =====================================================================

@pytest.mark.asyncio
async def test_deterministic_token_provider_header_injection_and_success(
    mock_transport: MockGoogleCalendarTransport,
) -> None:
    """Verify DeterministicCalendarTokenProvider injects Bearer token and syncs."""
    settings = GoogleCalendarSettings(
        enabled=True,
        client_email="test@school.iam.gserviceaccount.com",
        calendar_id="primary",
    )
    token_provider = DeterministicCalendarTokenProvider(
        token="ya29.test_deterministic_bearer_token"
    )
    client = httpx.AsyncClient(transport=mock_transport)
    adapter = GoogleCalendarAdapter(
        settings=settings,
        http_client=client,
        token_provider=token_provider,
    )

    dto = CalendarEventDTO(
        external_event_id="det-event-101",
        external_calendar_id="primary",
        title="Physics Lab Assessment",
        start_at=datetime.now(UTC) + timedelta(days=2),
        end_at=datetime.now(UTC) + timedelta(days=2, hours=1),
    )
    result = await adapter.create_event(tenant_id="tenant-det-01", event=dto)
    assert result.status == SyncStatus.SYNCED
    assert result.etag is not None
    assert token_provider.access_count >= 1

    # Verify HTTP request was dispatched with Authorization Bearer header
    api_requests = [
        r for r in mock_transport.requests_log if "/calendar/v3/" in str(r.url)
    ]
    assert len(api_requests) >= 1
    assert (
        api_requests[0].headers.get("authorization")
        == "Bearer ya29.test_deterministic_bearer_token"
    )


@pytest.mark.asyncio
async def test_deterministic_token_provider_auth_failure_behavior(
    mock_transport: MockGoogleCalendarTransport,
) -> None:
    """Verify that token provider failure results in fail-closed SyncStatus.FAILED."""
    settings = GoogleCalendarSettings(
        enabled=True,
        client_email="test@school.iam.gserviceaccount.com",
        calendar_id="primary",
    )
    failing_provider = DeterministicCalendarTokenProvider(valid=False)
    client = httpx.AsyncClient(transport=mock_transport)
    adapter = GoogleCalendarAdapter(
        settings=settings,
        http_client=client,
        token_provider=failing_provider,
    )

    dto = CalendarEventDTO(
        external_event_id="det-event-102",
        external_calendar_id="primary",
        title="Chemistry Lab Assessment",
        start_at=datetime.now(UTC) + timedelta(days=2),
        end_at=datetime.now(UTC) + timedelta(days=2, hours=1),
    )
    result = await adapter.create_event(tenant_id="tenant-det-01", event=dto)
    assert result.status == SyncStatus.FAILED
    assert "Network exception" in (result.error_message or "")
    assert "Deterministic token provider is configured as invalid" in (
        result.error_message or ""
    )

