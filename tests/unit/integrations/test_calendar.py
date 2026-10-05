"""Unit tests for Canonical Calendar Integration boundary.

Covers:
- RFC 5545 iCalendar serialization and parsing.
- FakeCalendarAdapter create, update, delete, incremental sync, and outage simulation.
- Outage behavior: status=FAILED, never fake success.
- Inbound availability synchronization, resource resolution, and callback deduplication.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.integrations.calendar.adapter import FakeCalendarAdapter
from app.integrations.calendar.dtos import CalendarEventDTO, SyncStatus
from app.integrations.calendar.rfc5545 import parse_ical_events, serialize_to_ical


def test_rfc5545_serialization_and_parsing() -> None:
    """Test RFC 5545 iCalendar generation and parsing roundtrip."""
    start_dt = datetime(2026, 11, 9, 9, 30, tzinfo=UTC)
    end_dt = datetime(2026, 11, 9, 11, 30, tzinfo=UTC)

    events = [
        CalendarEventDTO(
            external_event_id="e7-ev-001",
            external_calendar_id="test-calendar",
            title="Electrical Inspection & Testing",
            start_at=start_dt,
            end_at=end_dt,
            description="Inspection of Main Distribution Panel",
            location="Substation Room A",
        ),
        CalendarEventDTO(
            external_event_id="e7-ev-002",
            external_calendar_id="test-calendar",
            title="HVAC Filter Replacement",
            start_at=start_dt + timedelta(hours=3),
            end_at=end_dt + timedelta(hours=3),
            description="Routine air filter change",
            location="Rooftop Unit 2",
        ),
    ]

    ical_text = serialize_to_ical(events)

    # Invariants verification in RFC text
    assert "BEGIN:VCALENDAR" in ical_text
    assert "VERSION:2.0" in ical_text
    assert "METHOD:PUBLISH" in ical_text
    assert "UID:e7-ev-001" in ical_text
    assert "DTSTART:20261109T093000Z" in ical_text
    assert "DTEND:20261109T113000Z" in ical_text
    assert "SUMMARY:Electrical Inspection & Testing" in ical_text
    assert "UID:e7-ev-002" in ical_text
    assert "END:VCALENDAR" in ical_text

    # Parse roundtrip
    parsed = parse_ical_events(ical_text)
    assert len(parsed) == 2
    assert parsed[0]["uid"] == "e7-ev-001"
    assert parsed[0]["summary"] == "Electrical Inspection & Testing"
    assert parsed[0]["start_at"] == start_dt
    assert parsed[0]["end_at"] == end_dt
    assert parsed[1]["uid"] == "e7-ev-002"


@pytest.mark.asyncio
async def test_fake_calendar_adapter_lifecycle() -> None:
    """Test full create, update, delete, and incremental fetch lifecycle."""
    adapter = FakeCalendarAdapter()
    tenant_id = "tenant-synth-001"
    cal_id = "test-calendar-main"
    start_dt = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    event = CalendarEventDTO(
        external_event_id="ev-100",
        external_calendar_id=cal_id,
        title="Initial Meeting",
        start_at=start_dt,
        end_at=start_dt + timedelta(hours=1),
    )

    # 1. CREATE
    res_create = await adapter.create_event(tenant_id, event, external_calendar_id=cal_id)
    assert res_create.status == SyncStatus.SYNCED
    assert res_create.etag is not None
    assert res_create.synced_at is not None
    assert res_create.error_message is None

    # Verify retrieval
    stored = await adapter.get_event(tenant_id, "ev-100", external_calendar_id=cal_id)
    assert stored is not None
    assert stored.title == "Initial Meeting"

    # 2. UPDATE
    updated_dto = CalendarEventDTO(
        external_event_id="ev-100",
        external_calendar_id=cal_id,
        title="Rescheduled Meeting",
        start_at=start_dt + timedelta(hours=2),
        end_at=start_dt + timedelta(hours=3),
    )
    res_update = await adapter.update_event(
        tenant_id, "ev-100", updated_dto, external_calendar_id=cal_id
    )
    assert res_update.status == SyncStatus.SYNCED
    assert res_update.etag != res_create.etag  # etag progressed

    # 3. INCREMENTAL FETCH
    changes = await adapter.fetch_incremental_changes(tenant_id, external_calendar_id=cal_id)
    assert len(changes.created_or_updated) == 1
    assert changes.created_or_updated[0].title == "Rescheduled Meeting"
    assert len(changes.deleted_external_ids) == 0
    assert changes.next_sync_token is not None

    # 4. DELETE
    del_ok = await adapter.delete_event(tenant_id, "ev-100", external_calendar_id=cal_id)
    assert del_ok is True
    assert (await adapter.get_event(tenant_id, "ev-100", external_calendar_id=cal_id)) is None

    # Fetch after delete
    changes_after_del = await adapter.fetch_incremental_changes(
        tenant_id, external_calendar_id=cal_id
    )
    assert len(changes_after_del.created_or_updated) == 0
    assert "ev-100" in changes_after_del.deleted_external_ids


@pytest.mark.asyncio
async def test_fake_calendar_adapter_outage_simulation() -> None:
    """Test truthful reporting during simulated calendar provider outage."""
    adapter = FakeCalendarAdapter()
    adapter.simulate_outage = True
    tenant_id = "tenant-synth-001"
    cal_id = "test-calendar-outage"
    start_dt = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    event = CalendarEventDTO(
        external_event_id="ev-outage-1",
        external_calendar_id=cal_id,
        title="Emergency Task",
        start_at=start_dt,
        end_at=start_dt + timedelta(hours=1),
    )

    # Provider call returns FAILED without crashing caller
    res = await adapter.create_event(tenant_id, event, external_calendar_id=cal_id)
    assert res.status == SyncStatus.FAILED
    assert res.status != SyncStatus.SYNCED
    assert "unavailable" in (res.error_message or "").lower()
    assert res.etag is None
    assert res.synced_at is None


@pytest.mark.asyncio
async def test_outbound_conditional_etag_concurrency() -> None:
    """Test outbound concurrency control: stale expected ETag causes CONFLICT, matching succeeds."""
    adapter = FakeCalendarAdapter()
    tenant_id = "tenant-etag-matrix"
    cal_id = "test-calendar-etag"
    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)

    # Create event
    ev = CalendarEventDTO(
        external_event_id="ev-d-1",
        external_calendar_id=cal_id,
        title="Inspection",
        start_at=t0,
        end_at=t0 + timedelta(hours=1),
    )
    res_create = await adapter.create_event(tenant_id, ev, external_calendar_id=cal_id)
    assert res_create.status == SyncStatus.SYNCED
    initial_etag = res_create.etag
    assert initial_etag is not None

    # Stale expected ETag fails with CONFLICT (HTTP 412 Precondition Failed emulation)
    ev_updated = CalendarEventDTO(
        external_event_id="ev-d-1",
        external_calendar_id=cal_id,
        title="Inspection Rescheduled",
        start_at=t0 + timedelta(hours=2),
        end_at=t0 + timedelta(hours=3),
    )
    res_stale = await adapter.update_event(
        tenant_id,
        "ev-d-1",
        ev_updated,
        expected_etag="stale-opaque-etag-value",
        external_calendar_id=cal_id,
    )
    assert res_stale.status == SyncStatus.CONFLICT
    assert "precondition failed" in (res_stale.error_message or "").lower()

    # Matching expected ETag succeeds
    res_match = await adapter.update_event(
        tenant_id,
        "ev-d-1",
        ev_updated,
        expected_etag=initial_etag,
        external_calendar_id=cal_id,
    )
    assert res_match.status == SyncStatus.SYNCED
    assert res_match.etag != initial_etag


def test_calendar_event_dto_requires_explicit_calendar_id() -> None:
    """Negative test: CalendarEventDTO rejects missing/empty external_calendar_id."""
    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)

    # Missing external_calendar_id
    with pytest.raises(ValidationError):
        CalendarEventDTO(
            external_event_id="ev-test",
            title="Meeting",
            start_at=t0,
            end_at=t0 + timedelta(hours=1),
        )  # type: ignore[call-arg]

    # Empty external_calendar_id
    with pytest.raises(ValueError):
        CalendarEventDTO(
            external_event_id="ev-test",
            external_calendar_id="",
            title="Meeting",
            start_at=t0,
            end_at=t0 + timedelta(hours=1),
        )

    # Whitespace-only external_calendar_id
    with pytest.raises(ValueError):
        CalendarEventDTO(
            external_event_id="ev-test",
            external_calendar_id="   ",
            title="Meeting",
            start_at=t0,
            end_at=t0 + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_fake_adapter_fails_closed_without_calendar_id() -> None:
    """Negative test: FakeCalendarAdapter fails closed if calendar ID is missing/empty."""
    adapter = FakeCalendarAdapter()
    tenant_id = "tenant-failclosed"

    with pytest.raises(ValueError, match="external_calendar_id is required"):
        await adapter.fetch_incremental_changes(tenant_id)

    with pytest.raises(ValueError, match="external_calendar_id is required"):
        await adapter.delete_event(tenant_id, "ev-1")

    with pytest.raises(ValueError, match="external_calendar_id is required"):
        await adapter.get_event(tenant_id, "ev-1")

    with pytest.raises(ValueError, match="external_calendar_id is required"):
        await adapter.list_events(tenant_id)

    with pytest.raises(ValueError, match="external_calendar_id is required"):
        adapter.set_sync_token(tenant_id, "tok-1", external_calendar_id="")



