"""Safe Opt-in Live End-to-End Test for Google Calendar Production Provider.

Part of Phase 7C Real Calendar Provider Adapter.
Gated by:
- E7_RUN_GOOGLE_CALENDAR_LIVE=1 (CRUD live verification)
- E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 (Push notification live verification)

Safety Invariants:
- SKIPPED by default when gate variable is absent.
- Fails clearly if gate is enabled but required credentials or calendar ID are missing.
- Strictly rejects "primary" calendar ID to prevent touching personal/production calendars.
- Operates exclusively on dedicated test calendars.
- All test event summaries prefixed with [E7-TEST].
- All created events are deleted in a finally block (zero orphaned test events).
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db.engine import DatabaseManager
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.dtos import CalendarEventDTO, SyncStatus
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.watch import CalendarWatchService
from app.integrations.calendar.webhook import GoogleCalendarWebhookHandler


@pytest.mark.asyncio
async def test_google_calendar_live_crud_flow() -> None:
    """Safe live CRUD verification against real Google Calendar REST v3 API."""
    if os.getenv("E7_RUN_GOOGLE_CALENDAR_LIVE") != "1":
        pytest.skip("Skipped: E7_RUN_GOOGLE_CALENDAR_LIVE=1 gate is not set.")

    # 1. Load configuration and validate calendar ID and auth mode
    settings = GoogleCalendarSettings()
    cal_id = settings.calendar_id.strip()
    if not cal_id:
        pytest.fail(
            "E7_RUN_GOOGLE_CALENDAR_LIVE=1 is set, but E7_GOOGLE_CALENDAR_CALENDAR_ID is missing."
        )

    # Safety invariant: NEVER operate against 'primary' calendar
    if cal_id.lower() == "primary":
        pytest.fail(
            "Safety violation: E7_GOOGLE_CALENDAR_CALENDAR_ID cannot be 'primary' in live test."
        )

    # In legacy service_account_key mode, check credentials; in ADC mode, no private key needed.
    if settings.auth_mode == "service_account_key":
        if not settings.client_email or not settings.client_email.strip():
            pytest.fail(
                "E7_RUN_GOOGLE_CALENDAR_LIVE=1 is set with service_account_key mode, "
                "but E7_GOOGLE_CALENDAR_CLIENT_EMAIL is missing."
            )
        private_key_val = settings.private_key.get_secret_value()
        if not private_key_val or not private_key_val.strip():
            pytest.fail(
                "E7_RUN_GOOGLE_CALENDAR_LIVE=1 is set with service_account_key mode, "
                "but E7_GOOGLE_CALENDAR_PRIVATE_KEY is missing."
            )

    adapter = GoogleCalendarAdapter(settings=settings)
    tenant_id = "test-live-tenant"
    test_run_id = uuid4().hex[:8]
    external_event_id = f"e7-test-ev-{test_run_id}"
    created_id: str | None = None

    now = datetime.now(UTC)
    start_time = now + timedelta(days=2)
    end_time = start_time + timedelta(hours=1)

    event_dto = CalendarEventDTO(
        external_event_id=external_event_id,
        external_calendar_id=cal_id,
        title=f"[E7-TEST] Live Verification Event {test_run_id}",
        start_at=start_time,
        end_at=end_time,
        description="Automated live verification test event for Phase 7C.",
        location="Room 101",
    )

    try:
        # Step 1: Create test event
        create_res = await adapter.create_event(
            tenant_id, event_dto, external_calendar_id=cal_id
        )
        assert create_res.status == SyncStatus.SYNCED, (
            f"Create event failed: {create_res.error_message}"
        )
        created_id = create_res.external_event_id
        initial_etag = create_res.etag
        assert created_id is not None
        assert initial_etag is not None

        # Step 1b: Ambiguous create retry (prove real Google Calendar REST v3 HTTP 409 recovery)
        retry_res = await adapter.create_event(
            tenant_id, event_dto, external_calendar_id=cal_id
        )
        assert retry_res.status == SyncStatus.SYNCED, (
            f"Ambiguous create retry failed: {retry_res.error_message}"
        )
        assert retry_res.external_event_id == external_event_id
        assert retry_res.etag == initial_etag

        # Step 2: Fetch created event
        fetched = await adapter.get_event(tenant_id, created_id, external_calendar_id=cal_id)
        assert fetched is not None
        assert fetched.title == event_dto.title
        assert fetched.etag is not None

        # Step 3: Update event with If-Match
        updated_dto = CalendarEventDTO(
            external_event_id=created_id,
            external_calendar_id=cal_id,
            title=f"[E7-TEST] Live Verification Event Updated {test_run_id}",
            start_at=start_time,
            end_at=end_time + timedelta(minutes=30),
            description="Updated description in live test.",
            etag=initial_etag,
        )
        update_res = await adapter.update_event(
            tenant_id,
            created_id,
            updated_dto,
            expected_etag=initial_etag,
            external_calendar_id=cal_id,
        )
        assert update_res.status == SyncStatus.SYNCED, (
            f"Update event failed: {update_res.error_message}"
        )
        assert update_res.etag != initial_etag

        # Step 4: Incremental sync delta
        changes = await adapter.fetch_incremental_changes(
            tenant_id=tenant_id,
            sync_token=None,
            external_calendar_id=cal_id,
        )
        assert changes.next_sync_token is not None

    finally:
        # Guaranteed cleanup in finally
        if created_id:
            try:
                await adapter.delete_event(
                    tenant_id,
                    created_id,
                    external_calendar_id=cal_id,
                )
            except Exception:
                pass


@pytest.mark.asyncio
async def test_google_calendar_live_push_watch_flow() -> None:
    """Safe live push notification watch channel registration test."""
    if os.getenv("E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE") != "1":
        pytest.skip(
            "Skipped: E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 gate is not set "
            "(requires public HTTPS callback URL)."
        )

    settings = GoogleCalendarSettings()
    cal_id = settings.calendar_id.strip()
    if not cal_id:
        pytest.fail(
            "E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 is set, but "
            "E7_GOOGLE_CALENDAR_CALENDAR_ID is missing."
        )

    if cal_id.lower() == "primary":
        pytest.fail(
            "Safety violation: E7_GOOGLE_CALENDAR_CALENDAR_ID cannot be 'primary' in live test."
        )

    if not settings.webhook_callback_url.startswith("https://"):
        pytest.fail(
            "E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 is set, but "
            "E7_GOOGLE_CALENDAR_WEBHOOK_CALLBACK_URL is not a public HTTPS URL."
        )

    if settings.auth_mode == "service_account_key":
        if not settings.client_email or not settings.client_email.strip():
            pytest.fail(
                "E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 is set with service_account_key, "
                "but E7_GOOGLE_CALENDAR_CLIENT_EMAIL is missing."
            )
        private_key_val = settings.private_key.get_secret_value()
        if not private_key_val or not private_key_val.strip():
            pytest.fail(
                "E7_RUN_GOOGLE_CALENDAR_PUSH_LIVE=1 is set with service_account_key, "
                "but E7_GOOGLE_CALENDAR_PRIVATE_KEY is missing."
            )

    adapter = GoogleCalendarAdapter(settings=settings)
    watch_service = CalendarWatchService(settings=settings)
    tenant_id = f"test-live-push-{uuid4().hex[:6]}"
    db = DatabaseManager()
    created_channel_id: str | None = None

    try:
        # Step 1: Durable watch channel registration on real Google Calendar
        async with db.session_factory() as session:
            record = await watch_service.ensure_watch_channel(
                session=session,
                tenant_id=tenant_id,
                external_calendar_id=cal_id,
                adapter=adapter,
                force_renewal=True,
            )
            await session.commit()
            created_channel_id = record.channel_id
            assert record.channel_id.startswith("e7-watch-")
            assert record.resource_id is not None
            assert record.status == "ACTIVE"
            assert record.token_hash is not None

        # Step 2: Durable webhook validation & replay protection
        handler = GoogleCalendarWebhookHandler()
        raw_token = settings.webhook_token.get_secret_value()
        replay_headers = {
            "x-goog-channel-id": record.channel_id,
            "x-goog-resource-id": record.resource_id,
            "x-goog-resource-state": "exists",
            "x-goog-message-number": "42",
            "x-goog-channel-token": raw_token,
        }
        async with db.session_factory() as session:
            res1 = await handler.handle_durable_webhook(session, replay_headers, adapter)
            assert res1.status in ("PROCESSED", "SYNC_HANDSHAKE_ACKNOWLEDGED")
            # Duplicate redelivery of identical message
            res2 = await handler.handle_durable_webhook(session, replay_headers, adapter)
            assert res2.status == "DUPLICATE_REPLAYED"

    finally:
        if created_channel_id:
            async with db.session_factory() as session:
                await watch_service.stop_watch_channel(
                    session=session,
                    tenant_id=tenant_id,
                    channel_id=created_channel_id,
                    adapter=adapter,
                )
                await session.commit()
        await db.engine.dispose()
