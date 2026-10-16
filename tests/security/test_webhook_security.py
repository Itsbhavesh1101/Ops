"""Google Calendar Webhook Non-Destructive Security Regression Suite for Phase 8A.

Validates that incoming Google Calendar push webhooks enforce strict security controls:
1. Missing X-Goog-Channel-ID header -> Rejected (400)
2. Unknown channel ID -> Rejected (404)
3. Inactive / stopped / expired channel -> Rejected (404)
4. Resource ID mismatch -> Rejected (400)
5. Missing channel token -> Rejected (401)
6. Wrong / forged channel token -> Rejected (403)
7. Replayed message number / duplicate delivery -> Idempotent handling (200 / DUPLICATE_REPLAYED)
8. Header / payload tampering attempts fail closed.

Invariants:
- Preserves GOOGLE_CALENDAR_PUSH_LIVE = PASS
- Zero live external Google mutation or tunnel dependency.
- Local database session used for deterministic verification.
"""

import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.calendar import create_calendar_router
from app.core.config import get_settings
from app.db.models.scheduling import CalendarWatchChannelRecord
from app.integrations.calendar.dtos import SyncChangesResult
from app.integrations.calendar.webhook import GoogleCalendarWebhookHandler


@pytest_asyncio.fixture
async def sec_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide scoped AsyncSession against local PostgreSQL development database."""
    settings = get_settings()
    db_url = (
        settings.database_url.get_secret_value()
        if isinstance(settings.database_url, SecretStr)
        else str(settings.database_url)
    )
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class DummySyncService:
    """Safe no-op sync service stub."""

    async def sync_incremental_changes(self, **kwargs: Any) -> SyncChangesResult:
        return SyncChangesResult(
            created_or_updated=[],
            deleted_external_ids=[],
            next_sync_token="dummy_token_123",
        )

    async def import_incremental_changes(self, **kwargs: Any) -> Any:
        return None


@pytest.fixture
def webhook_client() -> TestClient:
    """Create dedicated test client for calendar webhook endpoint."""
    app = FastAPI()
    register_api_exception_handlers(app)

    handler = GoogleCalendarWebhookHandler()
    sync_service = DummySyncService()

    settings = get_settings()
    db_url = (
        settings.database_url.get_secret_value()
        if isinstance(settings.database_url, SecretStr)
        else str(settings.database_url)
    )
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    router = create_calendar_router(
        session_dependency=get_test_session,
        webhook_handler=handler,
        sync_service=sync_service,  # type: ignore[arg-type]
    )
    app.include_router(router, prefix="/api/v1/integrations/calendar")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.asyncio
async def test_webhook_missing_channel_id_rejected_400(
    webhook_client: TestClient,
) -> None:
    """Missing X-Goog-Channel-ID header is rejected with 400 Bad Request."""
    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Resource-ID": "res-123",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "calendar.invalid_header"


@pytest.mark.asyncio
async def test_webhook_unknown_channel_id_rejected_404(
    webhook_client: TestClient,
) -> None:
    """Unknown or unregistered channel ID is rejected with 404 Not Found."""
    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Channel-ID": f"unknown-chan-{uuid4()}",
            "X-Goog-Resource-ID": "res-123",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "calendar.channel_not_found"


@pytest.mark.asyncio
async def test_webhook_inactive_or_stopped_channel_rejected_404(
    webhook_client: TestClient,
    sec_db_session: AsyncSession,
) -> None:
    """A channel with status STOPPED or EXPIRED is rejected with 404 Not Found."""
    chan_id = f"chan-stopped-{uuid4().hex[:8]}"
    res_id = f"res-stopped-{uuid4().hex[:8]}"
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id=chan_id,
        resource_id=res_id,
        status="STOPPED",
        token_hash="dummy_hash",
        expiration_at=datetime.now(UTC) - timedelta(hours=1),
    )
    sec_db_session.add(channel_rec)
    await sec_db_session.commit()

    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Channel-ID": chan_id,
            "X-Goog-Resource-ID": res_id,
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "calendar.channel_not_found"


@pytest.mark.asyncio
async def test_webhook_resource_id_mismatch_rejected_400(
    webhook_client: TestClient,
    sec_db_session: AsyncSession,
) -> None:
    """Spoofed or mismatched resource ID is rejected with 400 Bad Request."""
    chan_id = f"chan-res-mismatch-{uuid4().hex[:8]}"
    correct_res_id = f"res-correct-{uuid4().hex[:8]}"
    raw_token = "secret-token-123"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id=chan_id,
        resource_id=correct_res_id,
        status="ACTIVE",
        token_hash=token_hash,
        expiration_at=datetime.now(UTC) + timedelta(days=7),
    )
    sec_db_session.add(channel_rec)
    await sec_db_session.commit()

    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Channel-ID": chan_id,
            "X-Goog-Resource-ID": "spoofed-tampered-res-id",
            "X-Goog-Channel-Token": raw_token,
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "calendar.resource_mismatch"


@pytest.mark.asyncio
async def test_webhook_missing_channel_token_rejected_401(
    webhook_client: TestClient,
    sec_db_session: AsyncSession,
) -> None:
    """Channel with token configured rejects push requests missing X-Goog-Channel-Token."""
    chan_id = f"chan-missing-tok-{uuid4().hex[:8]}"
    res_id = f"res-{uuid4().hex[:8]}"
    raw_token = "configured-secret"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
        token_hash=token_hash,
        expiration_at=datetime.now(UTC) + timedelta(days=7),
    )
    sec_db_session.add(channel_rec)
    await sec_db_session.commit()

    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Channel-ID": chan_id,
            "X-Goog-Resource-ID": res_id,
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "calendar.token_missing"


@pytest.mark.asyncio
async def test_webhook_wrong_token_rejected_403(
    webhook_client: TestClient,
    sec_db_session: AsyncSession,
) -> None:
    """Invalid or forged token is rejected with 403 Forbidden."""
    chan_id = f"chan-wrong-tok-{uuid4().hex[:8]}"
    res_id = f"res-{uuid4().hex[:8]}"
    raw_token = "authentic-secret"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
        token_hash=token_hash,
        expiration_at=datetime.now(UTC) + timedelta(days=7),
    )
    sec_db_session.add(channel_rec)
    await sec_db_session.commit()

    res = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers={
            "X-Goog-Channel-ID": chan_id,
            "X-Goog-Resource-ID": res_id,
            "X-Goog-Channel-Token": "forged-attacker-token",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "1",
        },
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "calendar.token_mismatch"


@pytest.mark.asyncio
async def test_webhook_duplicate_delivery_idempotent(
    webhook_client: TestClient,
    sec_db_session: AsyncSession,
) -> None:
    """Repeated delivery of the same message number is handled idempotently without error."""
    chan_id = f"chan-dup-{uuid4().hex[:8]}"
    res_id = f"res-{uuid4().hex[:8]}"
    raw_token = "dup-token"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    tenant_id = f"tenant-{uuid4().hex[:8]}"

    channel_rec = CalendarWatchChannelRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="test-cal",
        channel_id=chan_id,
        resource_id=res_id,
        status="ACTIVE",
        token_hash=token_hash,
        expiration_at=datetime.now(UTC) + timedelta(days=7),
    )
    sec_db_session.add(channel_rec)
    await sec_db_session.commit()

    headers = {
        "X-Goog-Channel-ID": chan_id,
        "X-Goog-Resource-ID": res_id,
        "X-Goog-Channel-Token": raw_token,
        "X-Goog-Resource-State": "exists",
        "X-Goog-Message-Number": "42",
    }

    # Delivery 1: processed successfully
    res1 = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers=headers,
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "PROCESSED"

    # Delivery 2: duplicate replay recognized and acknowledged without error
    res2 = webhook_client.post(
        "/api/v1/integrations/calendar/google/webhook",
        headers=headers,
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "DUPLICATE_REPLAYED"
