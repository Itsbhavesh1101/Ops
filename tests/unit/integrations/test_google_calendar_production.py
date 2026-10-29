"""Comprehensive Contract Tests for Google Calendar Production Components.

Part of Phase 7C Real Calendar Provider Adapter.
Validates:
- RFC 7523 JWT assertion claims and RS256 signature.
- OAuth2 token request, lazy caching, proactive skew renewal, and storm prevention.
- 401 refresh-once and bounded retry semantics.
- Secret redaction across error paths.
- Bearer token injection into Google Calendar REST v3 calls.
- events.watch channel creation and channels.stop.
- Durable watch channel persistence and lifecycle renewal.
- Webhook header validation (Channel ID, Resource ID, Token hash).
- Handshake 'sync' state acknowledgement.
- Durable replay protection across handler recreation/restart.
- Callback-to-sync wiring with InboundCalendarSyncService.
- Strict tenant isolation derived exclusively from durable watch state.
- Periodic inbound reconciliation reusing incremental sync logic.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.calendar import create_calendar_router, get_db_session
from app.core.config import get_settings
from app.core.idempotency.contracts import create_idempotency_identity
from app.core.idempotency.models import IdempotencyRecord
from app.db.models.scheduling import (
    CalendarLinkRecord,
    CalendarWatchChannelRecord,
    ResourceAvailabilityRecord,
    ResourceRecord,
)
from app.integrations.calendar.auth import (
    CALENDAR_OAUTH_SCOPE,
    GoogleCalendarAuthError,
    GoogleTokenProvider,
)
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.dtos import SyncChangesResult
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.inbound import InboundCalendarSyncService
from app.integrations.calendar.reconciliation import CalendarReconciliationService
from app.integrations.calendar.transport import GoogleCalendarTransport
from app.integrations.calendar.watch import (
    CalendarWatchService,
)
from app.integrations.calendar.webhook import (
    GoogleCalendarWebhookHandler,
)
from app.integrations.calendar.worker import CalendarWorker
from app.main import app as prod_app
from tests.unit.integrations.calendar_db_guard import verify_calendar_db_connection


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
def auth_settings(rsa_key_pem: str) -> GoogleCalendarSettings:
    """Settings configured with service account client email and valid RSA private key."""
    return GoogleCalendarSettings(
        enabled=True,
        auth_mode="service_account_key",
        client_email="sa-calendar@school-project.iam.gserviceaccount.com",
        private_key=SecretStr(rsa_key_pem),
        calendar_id="c_school_calendar_id@group.calendar.google.com",
        webhook_callback_url="https://api.school.edu/api/v1/integrations/calendar/google/webhook",
        webhook_token=SecretStr("super_secure_webhook_shared_secret"),
        channel_ttl_seconds=604800,
        reconciliation_interval_seconds=3600.0,
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide scoped AsyncSession against local PostgreSQL development database."""
    settings = get_settings()
    db_url = (
        settings.database_url.get_secret_value()
        if isinstance(settings.database_url, SecretStr)
        else str(settings.database_url)
    )
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    await verify_calendar_db_connection(engine, db_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ==============================================================================
# 1. JWT Assertion & Token Provider Tests
# ==============================================================================


def test_jwt_assertion_claims_and_signature(
    auth_settings: GoogleCalendarSettings, rsa_key_pem: str
) -> None:
    """Verify RFC 7523 JWT assertion claims and RS256 signature."""
    provider = GoogleTokenProvider(settings=auth_settings)
    assertion = provider._generate_jwt_assertion()

    # Verify signature and decode claims using public key
    public_key = serialization.load_pem_private_key(
        rsa_key_pem.encode("utf-8"), password=None
    ).public_key()
    claims = jwt.decode(
        assertion,
        public_key,
        algorithms=["RS256"],
        audience=provider._token_url,
    )

    assert claims["iss"] == auth_settings.client_email
    assert "sub" not in claims
    assert claims["scope"] == CALENDAR_OAUTH_SCOPE
    assert claims["aud"] == provider._token_url
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] <= 3600
    assert "jti" in claims


@pytest.mark.asyncio
async def test_token_provider_exchange_and_caching(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify lazy token acquisition and in-memory cache reuse."""
    request_count = 0

    async def token_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url == httpx.URL("https://oauth2.googleapis.com/token")
        assert "grant_type" in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.mock_cached_access_token_v1",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(token_handler))
    provider = GoogleTokenProvider(settings=auth_settings, http_client=mock_client)

    # First fetch: makes HTTP token exchange
    token1 = await provider.get_access_token()
    assert token1 == "ya29.mock_cached_access_token_v1"
    assert request_count == 1

    # Second fetch: uses in-memory cache, 0 extra HTTP requests
    token2 = await provider.get_access_token()
    assert token2 == "ya29.mock_cached_access_token_v1"
    assert request_count == 1


@pytest.mark.asyncio
async def test_token_provider_proactive_skew_refresh(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify token is proactively refreshed when expiry is within skew window."""
    request_count = 0

    async def token_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"ya29.mock_token_{request_count}",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(token_handler))
    provider = GoogleTokenProvider(
        settings=auth_settings,
        http_client=mock_client,
        skew_seconds=300,
    )

    token1 = await provider.get_access_token()
    assert token1 == "ya29.mock_token_1"
    assert request_count == 1

    # Manually set cached expiration to 100 seconds in the future (inside the 300s skew window)
    provider._expires_at = datetime.now(UTC) + timedelta(seconds=100)
    assert not provider.is_token_valid()

    # Subsequent request triggers refresh
    token2 = await provider.get_access_token()
    assert token2 == "ya29.mock_token_2"
    assert request_count == 2


@pytest.mark.asyncio
async def test_token_provider_concurrency_lock_prevents_storms(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify asyncio.Lock prevents duplicate parallel refresh storms."""
    request_count = 0

    async def token_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0.05)  # Simulate small network latency
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.mock_single_exchange_token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(token_handler))
    provider = GoogleTokenProvider(settings=auth_settings, http_client=mock_client)

    # Launch 10 concurrent requests
    results = await asyncio.gather(*(provider.get_access_token() for _ in range(10)))

    assert len(results) == 10
    assert all(tok == "ya29.mock_single_exchange_token" for tok in results)
    assert request_count == 1


@pytest.mark.asyncio
async def test_token_provider_secret_redaction_on_failure(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify failed token exchange never logs or exposes secrets in exceptions."""
    async def failing_token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid_grant: account disabled")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_token_handler))
    provider = GoogleTokenProvider(settings=auth_settings, http_client=mock_client)

    with pytest.raises(GoogleCalendarAuthError) as exc_info:
        await provider.get_access_token()

    err_str = str(exc_info.value)
    # Check that secrets are never echoed
    assert "MIIE" not in err_str
    assert "BEGIN PRIVATE KEY" not in err_str
    assert "assertion" not in err_str.lower() or "failed with http 400" in err_str.lower()
    assert "400" in err_str


# ==============================================================================
# 2. Production HTTP Transport & 401 Recovery Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_transport_bearer_injection_and_401_refresh_once(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify transport injects Authorization Bearer and refreshes once on 401."""
    token_requests = 0
    api_requests = 0
    used_tokens: list[str] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests, api_requests
        if "oauth2.googleapis.com" in str(request.url):
            token_requests += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"ya29.fresh_token_{token_requests}",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        api_requests += 1
        auth_header = request.headers.get("Authorization", "")
        used_tokens.append(auth_header)

        # First API call returns 401 Unauthorized (expired token)
        if api_requests == 1:
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})

        # Second API call succeeds
        return httpx.Response(200, json={"items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    token_provider = GoogleTokenProvider(settings=auth_settings, http_client=client)
    transport = GoogleCalendarTransport(
        settings=auth_settings,
        token_provider=token_provider,
        http_client=client,
    )

    resp = await transport.request("GET", "calendars/primary/events")
    assert resp.status_code == 200
    assert token_requests == 2  # Initial fetch + 1 refresh on 401
    assert api_requests == 2  # Initial call (401) + 1 retry (200)
    assert used_tokens[0] == "Bearer ya29.fresh_token_1"
    assert used_tokens[1] == "Bearer ya29.fresh_token_2"


@pytest.mark.asyncio
async def test_transport_bounded_401_prevents_infinite_loop(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify that repeated 401 does not enter an infinite retry loop."""
    api_calls = 0

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3600})
        api_calls += 1
        return httpx.Response(401, json={"error": {"message": "Persistent Unauthorized"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    token_provider = GoogleTokenProvider(settings=auth_settings, http_client=client)
    transport = GoogleCalendarTransport(
        settings=auth_settings,
        token_provider=token_provider,
        http_client=client,
    )

    resp = await transport.request("GET", "calendars/primary/events")
    assert resp.status_code == 401
    assert api_calls == 2  # Exactly 1 retry, no more


# ==============================================================================
# 3. Google Watch Channels & Durable Lifecycle Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_adapter_watch_events_and_stop_channel(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify events.watch creation and channels.stop requests."""
    recorded_requests: list[httpx.Request] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        if str(request.url).endswith("/events/watch"):
            return httpx.Response(
                200,
                json={
                    "kind": "api#channel",
                    "id": "test-channel-id-1",
                    "resourceId": "res-uuid-999",
                    "expiration": "1728345600000",
                },
            )
        if str(request.url).endswith("/channels/stop"):
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)

    # 1. Watch events
    watch_res = await adapter.watch_events(
        tenant_id="tenant-1",
        channel_id="test-channel-id-1",
        callback_url=auth_settings.webhook_callback_url,
        token="test_token",
        ttl_seconds=3600,
    )
    assert watch_res["id"] == "test-channel-id-1"
    assert watch_res["resourceId"] == "res-uuid-999"
    assert watch_res["expiration"] == "1728345600000"

    # 2. Stop channel
    stop_ok = await adapter.stop_channel(
        tenant_id="tenant-1",
        channel_id="test-channel-id-1",
        resource_id="res-uuid-999",
    )
    assert stop_ok is True


@pytest.mark.asyncio
async def test_watch_service_atomic_persistence_and_retirement(
    auth_settings: GoogleCalendarSettings,
    db_session: AsyncSession,
) -> None:
    """Verify CalendarWatchService persists active channel, hashes token, and retires old."""
    stopped_channels: list[str] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        if str(request.url).endswith("/events/watch"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": body["id"],
                    "resourceId": f"res-{body['id']}",
                    "expiration": str(
                        int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
                    ),
                },
            )
        if str(request.url).endswith("/channels/stop"):
            body = json.loads(request.content.decode("utf-8"))
            stopped_channels.append(body["id"])
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)
    watch_service = CalendarWatchService(settings=auth_settings)
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    calendar_id = "test-cal-id"

    # Step 1: Ensure initial channel
    channel1 = await watch_service.ensure_watch_channel(
        session=db_session,
        tenant_id=tenant_id,
        external_calendar_id=calendar_id,
        adapter=adapter,
    )
    assert channel1.status == "ACTIVE"
    assert channel1.token_hash is not None
    expected_hash = hashlib.sha256(
        auth_settings.webhook_token.get_secret_value().encode("utf-8")
    ).hexdigest()
    assert channel1.token_hash == expected_hash

    # Step 2: Force renewal: creates new channel, stops old channel
    channel2 = await watch_service.ensure_watch_channel(
        session=db_session,
        tenant_id=tenant_id,
        external_calendar_id=calendar_id,
        adapter=adapter,
        force_renewal=True,
    )
    assert channel2.id != channel1.id
    assert channel2.status == "ACTIVE"
    assert channel1.status == "STOPPED"
    assert channel1.channel_id in stopped_channels


# ==============================================================================
# 4. Webhook Route, Header Validation, and Durable Idempotency
# ==============================================================================


@pytest.mark.asyncio
async def test_webhook_header_validation_errors(
    auth_settings: GoogleCalendarSettings,
    db_session: AsyncSession,
) -> None:
    """Verify webhook rejects missing channel, unknown channel, and token mismatch."""
    handler = GoogleCalendarWebhookHandler()
    tenant_id = f"tenant-{uuid4().hex[:6]}"

    # Setup durable channel
    token_str = "secret_valid_token"
    token_hash = hashlib.sha256(token_str.encode("utf-8")).hexdigest()
    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id="chan-durable-123",
        resource_id="res-durable-123",
        token_hash=token_hash,
        status="ACTIVE",
    )
    db_session.add(channel_rec)
    await db_session.flush()

    # 1. Missing X-Goog-Channel-ID
    res1 = await handler.handle_durable_webhook(db_session, {})
    assert res1.status == "REJECTED"
    assert "missing required x-goog-channel-id" in res1.message.lower()

    # 2. Unknown channel ID
    res2 = await handler.handle_durable_webhook(
        db_session,
        {"X-Goog-Channel-ID": "unknown-nonexistent-channel"},
    )
    assert res2.status == "REJECTED"
    assert "unknown or inactive" in res2.message.lower()

    # 3. Resource ID mismatch
    res3 = await handler.handle_durable_webhook(
        db_session,
        {
            "X-Goog-Channel-ID": "chan-durable-123",
            "X-Goog-Resource-ID": "wrong-resource-id",
            "X-Goog-Channel-Token": token_str,
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res3.status == "REJECTED"
    assert "resource id mismatch" in res3.message.lower()

    # 4. Token mismatch
    res4 = await handler.handle_durable_webhook(
        db_session,
        {
            "X-Goog-Channel-ID": "chan-durable-123",
            "X-Goog-Resource-ID": "res-durable-123",
            "X-Goog-Channel-Token": "invalid_wrong_token",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res4.status == "REJECTED"
    assert "channel token verification failed" in res4.message.lower()


@pytest.mark.asyncio
async def test_webhook_handshake_sync_state(
    db_session: AsyncSession,
) -> None:
    """Verify 'sync' handshake notification returns 200 with no side effects."""
    handler = GoogleCalendarWebhookHandler()
    tenant_id = f"tenant-{uuid4().hex[:6]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id="chan-sync-test",
        resource_id="res-sync-test",
        status="ACTIVE",
    )
    db_session.add(channel_rec)
    await db_session.flush()

    res = await handler.handle_durable_webhook(
        session=db_session,
        headers={
            "X-Goog-Channel-ID": "chan-sync-test",
            "X-Goog-Resource-ID": "res-sync-test",
            "X-Goog-Resource-State": "sync",
        },
    )
    assert res.status == "SYNC_HANDSHAKE_ACKNOWLEDGED"
    assert "handshake acknowledged" in res.message.lower()


@pytest.mark.asyncio
async def test_webhook_durable_replay_protection_after_restart(
    db_session: AsyncSession,
) -> None:
    """Verify durable replay protection suppresses duplicate callbacks after process restart."""
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = "test-cal-id"
    channel_id = f"chan-replay-{uuid4().hex[:6]}"

    # Setup resource and watch channel
    res_rec = ResourceRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        resource_key="Staff Member A",
        type="PERSON",
        display_label="Staff Member A",
        capacity=1,
    )
    db_session.add(res_rec)

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id=cal_id,
        channel_id=channel_id,
        resource_id="res-1",
        status="ACTIVE",
    )
    db_session.add(channel_rec)
    await db_session.flush()

    headers = {
        "X-Goog-Channel-ID": channel_id,
        "X-Goog-Resource-ID": "res-1",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Message-Number": "101",
    }

    # Simulate adapter returning 1 event change
    async def adapter_changes(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ext-ev-1",
                        "summary": "Staff Member A",
                        "start": {"dateTime": "2026-10-10T10:00:00Z"},
                        "end": {"dateTime": "2026-10-10T11:00:00Z"},
                        "etag": '"etag-v1"',
                    }
                ],
                "nextSyncToken": "token-step-1",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(adapter_changes))
    adapter = GoogleCalendarAdapter(http_client=mock_client)

    # First delivery handled by Handler Instance 1
    handler1 = GoogleCalendarWebhookHandler()
    res1 = await handler1.handle_durable_webhook(
        session=db_session,
        headers=headers,
        adapter=adapter,
    )
    assert res1.status == "PROCESSED"

    # Simulate PROCESS RESTART: construct brand-new Handler Instance 2 with empty memory
    handler2 = GoogleCalendarWebhookHandler()
    assert len(handler2._processed_messages) == 0

    # Redelivery of identical message #101 handled by Handler Instance 2
    res2 = await handler2.handle_durable_webhook(
        session=db_session,
        headers=headers,
        adapter=adapter,
    )
    assert res2.status == "DUPLICATE_REPLAYED"
    assert "duplicate notification message #101 ignored" in res2.message.lower()


@pytest.mark.asyncio
async def test_webhook_triggers_incremental_sync_and_availability(
    db_session: AsyncSession,
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify valid webhook push triggers incremental sync and creates
    ResourceAvailabilityRecord.
    """
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = "test-cal-availability"
    channel_id = f"chan-sync-flow-{uuid4().hex[:6]}"

    # Setup resource
    res_rec = ResourceRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        resource_key="Teacher Alice",
        type="PERSON",
        display_label="Teacher Alice",
        capacity=1,
    )
    db_session.add(res_rec)

    # Setup watch channel record
    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id=cal_id,
        channel_id=channel_id,
        resource_id="res-sync-flow",
        status="ACTIVE",
    )
    db_session.add(channel_rec)
    await db_session.flush()

    # Mock Google events endpoint response
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "alice-external-busy-1",
                        "summary": "Teacher Alice",
                        "start": {"dateTime": "2026-10-12T09:00:00Z"},
                        "end": {"dateTime": "2026-10-12T10:30:00Z"},
                        "etag": '"etag-alice-1"',
                    }
                ],
                "nextSyncToken": "sync-token-next-123",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)

    handler = GoogleCalendarWebhookHandler()
    headers = {
        "X-Goog-Channel-ID": channel_id,
        "X-Goog-Resource-ID": "res-sync-flow",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Message-Number": "1",
    }

    result = await handler.handle_durable_webhook(
        session=db_session,
        headers=headers,
        adapter=adapter,
    )
    assert result.status == "PROCESSED"

    # Verify ResourceAvailabilityRecord created
    avail_records = (
        (
            await db_session.execute(
                ResourceAvailabilityRecord.__table__.select().where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_rec.id,
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(avail_records) == 1
    assert avail_records[0]["interval_type"] == "UNAVAILABLE"

    # Verify SYNC_CURSOR cursor record updated with new sync token
    cursor_rec = (
        (
            await db_session.execute(
                CalendarLinkRecord.__table__.select().where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.external_calendar_id == cal_id,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        )
        .mappings()
        .one()
    )
    assert cursor_rec["sync_token"] == "sync-token-next-123"


@pytest.mark.asyncio
async def test_fastapi_mounted_webhook_route(
    db_session: AsyncSession,
) -> None:
    """Verify FastAPI mounted route POST /api/v1/integrations/calendar/google/webhook."""
    app = FastAPI()
    router = create_calendar_router(session_dependency=lambda: db_session)
    app.include_router(router, prefix="/api/v1/integrations/calendar")

    tenant_id = f"tenant-{uuid4().hex[:6]}"
    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="primary",
        channel_id="fastapi-test-channel",
        resource_id="res-fastapi",
        status="ACTIVE",
    )
    db_session.add(channel_rec)
    await db_session.flush()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Handshake sync request
        resp = await client.post(
            "/api/v1/integrations/calendar/google/webhook",
            headers={
                "X-Goog-Channel-ID": "fastapi-test-channel",
                "X-Goog-Resource-ID": "res-fastapi",
                "X-Goog-Resource-State": "sync",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SYNC_HANDSHAKE_ACKNOWLEDGED"


@pytest.mark.asyncio
async def test_periodic_inbound_reconciliation(
    db_session: AsyncSession,
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify CalendarReconciliationService.reconcile_inbound_calendar reuses incremental sync."""
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = "reconcile-cal"

    res_rec = ResourceRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        resource_key="Room 202",
        type="ROOM",
        display_label="Room 202",
        capacity=30,
    )
    db_session.add(res_rec)
    await db_session.flush()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "reconciled-ev-1",
                        "summary": "Room 202",
                        "start": {"dateTime": "2026-10-15T14:00:00Z"},
                        "end": {"dateTime": "2026-10-15T15:00:00Z"},
                        "etag": '"etag-reconciled-1"',
                    }
                ],
                "nextSyncToken": "sync-token-reconciled",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)
    reconciliation_service = CalendarReconciliationService(adapter=adapter)

    sync_result = await reconciliation_service.reconcile_inbound_calendar(
        session=db_session,
        tenant_id=tenant_id,
        external_calendar_id=cal_id,
    )
    assert sync_result.next_sync_token == "sync-token-reconciled"
    assert len(sync_result.created_or_updated) == 1


def test_google_calendar_settings_fail_closed_validation() -> None:
    """Verify GoogleCalendarSettings fails closed when enabled=True but credentials missing."""
    # Service account key mode requires client_email, calendar_id, and private_key
    with pytest.raises(ValueError, match="client_email is empty"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="service_account_key",
            client_email="",
            calendar_id="cal-1",
            private_key=SecretStr("key"),
        )

    with pytest.raises(ValueError, match="calendar_id is empty"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="service_account_key",
            client_email="sa@google.com",
            calendar_id="",
            private_key=SecretStr("key"),
        )

    with pytest.raises(ValueError, match="private_key is empty"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="service_account_key",
            client_email="sa@google.com",
            calendar_id="cal-1",
            private_key=SecretStr(""),
        )

    # Keyless ADC mode requires calendar_id, but NOT client_email or private_key
    with pytest.raises(ValueError, match="calendar_id is empty"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="adc",
            calendar_id="",
        )

    adc_valid = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_school_calendar@group.calendar.google.com",
    )
    assert adc_valid.auth_mode == "adc"
    assert adc_valid.calendar_id == "c_school_calendar@group.calendar.google.com"
    assert adc_valid.private_key.get_secret_value() == ""
    assert adc_valid.client_email == ""

    # Invalid auth mode rejected
    with pytest.raises(ValueError, match="Unsupported E7_GOOGLE_CALENDAR_AUTH_MODE"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="unsupported_mode",  # type: ignore[arg-type]
            calendar_id="cal-1",
        )


@pytest.mark.asyncio
async def test_watch_service_renewal_and_expired_channel_recovery(
    db_session: AsyncSession,
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify CalendarWatchService detects expired channels and safely renews them."""
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = "test-cal-expiry"

    # Setup expired active channel in DB
    past_exp = datetime.now(UTC) - timedelta(hours=2)
    old_channel = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id=cal_id,
        channel_id="chan-expired-old",
        resource_id="res-expired-old",
        status="ACTIVE",
        expiration_at=past_exp,
    )
    db_session.add(old_channel)
    await db_session.flush()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.mock_token", "expires_in": 3600})
        if str(request.url).endswith("/events/watch"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": body["id"],
                    "resourceId": f"res-{body['id']}",
                    "expiration": str(
                        int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
                    ),
                },
            )
        if str(request.url).endswith("/channels/stop"):
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)
    watch_service = CalendarWatchService(settings=auth_settings)

    renewed = await watch_service.ensure_watch_channel(
        session=db_session,
        tenant_id=tenant_id,
        external_calendar_id=cal_id,
        adapter=adapter,
    )

    assert renewed.channel_id != "chan-expired-old"
    assert renewed.status == "ACTIVE"
    assert old_channel.status == "STOPPED"


@pytest.mark.asyncio
async def test_calendar_worker_watch_renewal_runtime_call_chain(
    db_session: AsyncSession,
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify exact watch renewal call chain through CalendarWorker:
    worker -> recurring trigger -> CalendarWatchService -> renewal query
    -> watch registration -> durable state update -> old channel stop.
    """
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = "cal-renewal-chain"

    # Setup expiring channel in DB
    past_exp = datetime.now(UTC) - timedelta(hours=1)
    chan_id = f"chan-expiring-{uuid4().hex[:8]}"
    res_id = f"res-expiring-{uuid4().hex[:8]}"
    old_chan = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id=cal_id,
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
        expiration_at=past_exp,
    )
    db_session.add(old_chan)
    await db_session.flush()

    call_log: list[str] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com" in url:
            call_log.append("token_refresh")
            return httpx.Response(
                200,
                json={"access_token": "ya29.worker_token", "expires_in": 3600},
            )
        if url.endswith("/events/watch"):
            call_log.append("watch_registration")
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": body["id"],
                    "resourceId": f"res-{body['id']}",
                    "expiration": str(
                        int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
                    ),
                },
            )
        if url.endswith("/channels/stop"):
            call_log.append("old_channel_stop")
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)

    class DummyDBManager:
        def session_factory(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _scope():
                yield db_session

            return _scope()

    worker = CalendarWorker(
        db_manager=DummyDBManager(),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter,
    )

    renewed_count = await worker.run_renewal_cycle(threshold_seconds=86400, tenant_id=tenant_id)
    assert renewed_count == 1

    assert "watch_registration" in call_log
    assert "old_channel_stop" in call_log

    refreshed_old = await db_session.get(CalendarWatchChannelRecord, old_chan.id)
    assert refreshed_old is not None
    assert refreshed_old.status == "STOPPED"

    stmt = select(CalendarWatchChannelRecord).where(
        CalendarWatchChannelRecord.tenant_id == tenant_id,
        CalendarWatchChannelRecord.status == "ACTIVE",
    )
    new_channels = (await db_session.execute(stmt)).scalars().all()
    assert len(new_channels) == 1
    new_chan = new_channels[0]
    assert new_chan.channel_id != chan_id
    assert new_chan.expiration_at is not None
    assert new_chan.expiration_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_calendar_worker_reconciliation_runtime_trigger(
    db_session: AsyncSession,
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify CalendarWorker periodic reconciliation consumes reconciliation_interval_seconds
    and does not duplicate side effects across multiple periods.
    """
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = f"worker-reconcile-cal-{uuid4().hex[:6]}"
    chan_id = f"chan-reconcile-{uuid4().hex[:8]}"
    res_id = f"res-reconcile-{uuid4().hex[:8]}"
    res_key = f"worker-res-{uuid4().hex[:6]}"

    res_rec = ResourceRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        resource_key=res_key,
        type="PERSON",
        display_label="Staff Worker Resource",
        active=True,
    )
    chan_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id=cal_id,
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
    )
    db_session.add_all([res_rec, chan_rec])
    await db_session.commit()

    call_count = 0

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        if "oauth2.googleapis.com" in url:
            return httpx.Response(
                200,
                json={"access_token": "ya29.worker_token", "expires_in": 3600},
            )
        if "/events" in url:
            if "syncToken" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "items": [],
                        "nextSyncToken": "sync_token_worker_stable",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "evt-worker-1",
                            "summary": res_key,
                            "start": {"dateTime": "2026-10-15T09:00:00Z"},
                            "end": {"dateTime": "2026-10-15T17:00:00Z"},
                            "status": "confirmed",
                        }
                    ],
                    "nextSyncToken": "sync_token_worker_step1",
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter = GoogleCalendarAdapter(settings=auth_settings, http_client=client)

    class DummyDBManager:
        def session_factory(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _scope():
                yield db_session

            return _scope()

    worker = CalendarWorker(
        db_manager=DummyDBManager(),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter,
    )

    # Period 1: imports changes and sets up availability
    scopes_p1 = await worker.run_reconciliation_cycle(tenant_id=tenant_id)
    assert scopes_p1 == 1

    stmt_avail = select(ResourceAvailabilityRecord).where(
        ResourceAvailabilityRecord.tenant_id == tenant_id
    )
    avails_p1 = (await db_session.execute(stmt_avail)).scalars().all()
    assert len(avails_p1) == 1

    # Period 2: runs next periodic reconciliation cycle with syncToken
    scopes_p2 = await worker.run_reconciliation_cycle(tenant_id=tenant_id)
    assert scopes_p2 == 1

    # Multiple periods must NOT duplicate side effects
    avails_p2 = (await db_session.execute(stmt_avail)).scalars().all()
    assert len(avails_p2) == 1


@pytest.mark.asyncio
async def test_webhook_idempotency_transient_failure_and_redelivery_retry(
    db_session: AsyncSession,
) -> None:
    """Verify webhook idempotency semantics on transient sync failure:
    1. First callback received -> sync fails -> idempotency record rolled back.
    2. Same Google message redelivered -> sync succeeds exactly once.
    3. Duplicate callback -> ignored as DUPLICATE_REPLAYED.
    """
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    chan_id = f"retry-test-channel-{uuid4().hex[:8]}"
    res_id = f"res-retry-{uuid4().hex[:8]}"

    chan_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="primary",
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
    )
    db_session.add(chan_rec)
    await db_session.commit()

    handler = GoogleCalendarWebhookHandler()
    headers = {
        "X-Goog-Channel-ID": chan_id,
        "X-Goog-Resource-ID": res_id,
        "X-Goog-Resource-State": "exists",
        "X-Goog-Message-Number": "100",
    }

    delivery_attempt = 0

    class FailingSyncService(InboundCalendarSyncService):
        async def import_incremental_changes(self, *args, **kwargs):
            nonlocal delivery_attempt
            delivery_attempt += 1
            if delivery_attempt == 1:
                raise RuntimeError("Transient upstream connection timeout")
            return SyncChangesResult(
                created_or_updated=[],
                deleted_external_ids=[],
                next_sync_token="sync_token_recovered",
            )

    sync_service = FailingSyncService()

    # Delivery 1: fails transiently
    with pytest.raises(RuntimeError, match="Transient upstream connection timeout"):
        await handler.handle_durable_webhook(
            session=db_session,
            headers=headers,
            sync_service=sync_service,
        )

    # Verify idempotency record was NOT committed because of transaction rollback
    identity = create_idempotency_identity(
        tenant_id=tenant_id,
        operation_name="calendar.google_webhook",
        raw_key=f"google-calendar:{chan_id}:100",
    )
    stmt_idemp = select(IdempotencyRecord).where(
        IdempotencyRecord.tenant_id == tenant_id,
        IdempotencyRecord.key_digest == str(identity.key_digest),
    )
    rec_after_fail = (await db_session.execute(stmt_idemp)).scalar_one_or_none()
    assert rec_after_fail is None

    # Delivery 2: Google redelivers the exact same message #100
    res_delivery_2 = await handler.handle_durable_webhook(
        session=db_session,
        headers=headers,
        sync_service=sync_service,
    )
    assert res_delivery_2.status == "PROCESSED"

    # Delivery 3: third delivery of message #100 is recognized as duplicate
    res_delivery_3 = await handler.handle_durable_webhook(
        session=db_session,
        headers=headers,
        sync_service=sync_service,
    )
    assert res_delivery_3.status == "DUPLICATE_REPLAYED"


@pytest.mark.asyncio
async def test_webhook_sync_handshake_race_condition(
    db_session: AsyncSession,
) -> None:
    """Verify initial 'sync' handshake succeeds safely even if durable watch channel
    registration has not yet been committed to the database (race condition tolerance).
    """
    handler = GoogleCalendarWebhookHandler()
    headers = {
        "X-Goog-Channel-ID": "concurrent-watch-uncommitted",
        "X-Goog-Resource-ID": "res-uncommitted",
        "X-Goog-Resource-State": "sync",
    }
    result = await handler.handle_durable_webhook(
        session=db_session,
        headers=headers,
    )
    assert result.status == "SYNC_HANDSHAKE_ACKNOWLEDGED"


@pytest.mark.asyncio
async def test_prod_app_webhook_mounted_and_responds(
    db_session: AsyncSession,
) -> None:
    """Verify real production FastAPI app from app.main has
    POST /api/v1/integrations/calendar/google/webhook.
    """
    tenant_id = f"tenant-{uuid4().hex[:6]}"
    chan_id = f"prod-app-chan-{uuid4().hex[:8]}"
    res_id = f"prod-app-res-{uuid4().hex[:8]}"

    chan_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="primary",
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
    )
    db_session.add(chan_rec)
    await db_session.flush()

    prod_app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=prod_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/integrations/calendar/google/webhook",
                headers={
                    "X-Goog-Channel-ID": chan_id,
                    "X-Goog-Resource-ID": res_id,
                    "X-Goog-Resource-State": "sync",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SYNC_HANDSHAKE_ACKNOWLEDGED"
    finally:
        prod_app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_multi_worker_concurrent_renewal_safety(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify that multiple concurrent worker instances executing renewal cycles
    against the same expiring channel cannot cause duplicate replacement channels.
    PostgreSQL FOR UPDATE SKIP LOCKED ensures exactly one worker acquires and renews,
    while concurrent workers skip without creating duplicate replacements.
    """
    settings = get_settings()
    db_url = (
        settings.database_url.get_secret_value()
        if isinstance(settings.database_url, SecretStr)
        else str(settings.database_url)
    )
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    await verify_calendar_db_connection(engine, db_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = f"cal-{uuid4().hex[:6]}"
    old_chan_id = f"chan-expiring-{uuid4().hex[:8]}"

    # Setup expiring channel in DB
    async with session_factory() as session:
        old_chan = CalendarWatchChannelRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            provider="google",
            external_calendar_id=cal_id,
            channel_id=old_chan_id,
            resource_id="res-old-1",
            status="ACTIVE",
            expiration_at=datetime.now(UTC) - timedelta(minutes=15),
        )
        session.add(old_chan)
        await session.commit()

    created_watches: list[str] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com" in url:
            return httpx.Response(
                200,
                json={"access_token": "ya29.worker_token", "expires_in": 3600},
            )
        if url.endswith("/events/watch"):
            body = json.loads(request.content.decode("utf-8"))
            created_watches.append(body["id"])
            await asyncio.sleep(0.05)
            return httpx.Response(
                200,
                json={
                    "id": body["id"],
                    "resourceId": f"res-{body['id']}",
                    "expiration": str(
                        int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
                    ),
                },
            )
        if url.endswith("/channels/stop"):
            return httpx.Response(204)
        return httpx.Response(404)

    client_1 = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    client_2 = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter_1 = GoogleCalendarAdapter(settings=auth_settings, http_client=client_1)
    adapter_2 = GoogleCalendarAdapter(settings=auth_settings, http_client=client_2)

    class DummyDBManager:
        def __init__(self, s_factory: Any) -> None:
            self._sf = s_factory

        def session_factory(self) -> Any:
            return self._sf()

    worker_1 = CalendarWorker(
        db_manager=DummyDBManager(session_factory),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter_1,
    )
    worker_2 = CalendarWorker(
        db_manager=DummyDBManager(session_factory),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter_2,
    )

    # Concurrently execute renewal cycles from two simulated worker replicas
    results = await asyncio.gather(
        worker_1.run_renewal_cycle(tenant_id=tenant_id),
        worker_2.run_renewal_cycle(tenant_id=tenant_id),
    )

    # Exactly one worker must have successfully renewed the channel
    assert sum(results) == 1
    assert 1 in results
    assert 0 in results

    # Verify database state
    async with session_factory() as verify_session:
        stmt = select(CalendarWatchChannelRecord).where(
            CalendarWatchChannelRecord.tenant_id == tenant_id,
            CalendarWatchChannelRecord.external_calendar_id == cal_id,
        )
        records = (await verify_session.execute(stmt)).scalars().all()
        assert len(records) == 2
        active_records = [r for r in records if r.status == "ACTIVE"]
        stopped_records = [r for r in records if r.status == "STOPPED"]
        assert len(active_records) == 1
        assert len(stopped_records) == 1
        assert stopped_records[0].channel_id == old_chan_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_worker_reconciliation_multi_replica_idempotency(
    auth_settings: GoogleCalendarSettings,
) -> None:
    """Verify that multiple worker replicas executing periodic inbound reconciliation
    concurrently or sequentially do not duplicate resource availability intervals
    or corrupt durable sync tokens.
    """
    settings = get_settings()
    db_url = (
        settings.database_url.get_secret_value()
        if isinstance(settings.database_url, SecretStr)
        else str(settings.database_url)
    )
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    await verify_calendar_db_connection(engine, db_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    tenant_id = f"tenant-{uuid4().hex[:6]}"
    cal_id = f"cal-recon-{uuid4().hex[:6]}"
    res_key = f"Staff Room {uuid4().hex[:4]}"

    async with session_factory() as session:
        # Resource record
        res_rec = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key=res_key,
            type="ROOM",
            display_label=res_key,
            capacity=30,
        )
        session.add(res_rec)

        # Active watch channel record
        chan_rec = CalendarWatchChannelRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            provider="google",
            external_calendar_id=cal_id,
            channel_id=f"chan-{uuid4().hex[:8]}",
            resource_id="res-123",
            status="ACTIVE",
            expiration_at=datetime.now(UTC) + timedelta(days=5),
        )
        session.add(chan_rec)
        await session.commit()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com" in url:
            return httpx.Response(
                200,
                json={"access_token": "ya29.worker_token", "expires_in": 3600},
            )
        if "/events" in url:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "evt-multi-worker-1",
                            "summary": res_key,
                            "start": {"dateTime": "2026-10-20T10:00:00Z"},
                            "end": {"dateTime": "2026-10-20T12:00:00Z"},
                            "status": "confirmed",
                        }
                    ],
                    "nextSyncToken": "sync_token_replica_shared",
                },
            )
        return httpx.Response(404)

    client_1 = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    client_2 = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    adapter_1 = GoogleCalendarAdapter(settings=auth_settings, http_client=client_1)
    adapter_2 = GoogleCalendarAdapter(settings=auth_settings, http_client=client_2)

    class DummyDBManager:
        def __init__(self, s_factory: Any) -> None:
            self._sf = s_factory

        def session_factory(self) -> Any:
            return self._sf()

    worker_1 = CalendarWorker(
        db_manager=DummyDBManager(session_factory),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter_1,
    )
    worker_2 = CalendarWorker(
        db_manager=DummyDBManager(session_factory),  # type: ignore[arg-type]
        settings=auth_settings,
        adapter=adapter_2,
    )

    # Replica 1 reconciles
    r1 = await worker_1.run_reconciliation_cycle(tenant_id=tenant_id)
    assert r1 == 1

    # Replica 2 reconciles right after
    r2 = await worker_2.run_reconciliation_cycle(tenant_id=tenant_id)
    assert r2 == 1

    # Verify no duplicate side effects in database
    async with session_factory() as verify_session:
        stmt_avail = select(ResourceAvailabilityRecord).where(
            ResourceAvailabilityRecord.tenant_id == tenant_id
        )
        avails = (await verify_session.execute(stmt_avail)).scalars().all()
        # Exactly 1 availability record created, NOT duplicated
        assert len(avails) == 1

        stmt_link = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.external_calendar_id == cal_id,
            CalendarLinkRecord.link_kind == "SYNC_CURSOR",
        )
        cursor = (await verify_session.execute(stmt_link)).scalar_one_or_none()
        assert cursor is not None
        assert cursor.sync_token == "sync_token_replica_shared"

    await engine.dispose()


