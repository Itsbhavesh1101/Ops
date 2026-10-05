"""Unit tests for Phase 4A canonical resource registry and availability intervals.

Verifies:
- Resource registration and retrieval.
- Timezone awareness validation.
- Half-open interval ordering.
- Tenant isolation enforcement.
- Generic resource types (no academic specialization).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.resources.schemas import (
    AvailabilityIntervalBase,
    AvailabilityIntervalCreate,
    IntervalType,
    ResourceCreate,
    ResourceType,
)
from app.resources.service import ResourceNotFoundError, ResourceService


def test_resource_registration_and_retrieval() -> None:
    """Verify resource can be registered and retrieved within tenant scope."""
    service = ResourceService()
    tenant_id = "tenant-res-001"
    create_payload = ResourceCreate(
        resource_key="res-room-101",
        type=ResourceType.ROOM,
        display_label="Conference Room A",
        capacity=20,
        skills=["PROJECTOR", "VIDEO_CONF"],
    )
    created = service.register_resource(tenant_id, create_payload)
    assert created.id is not None
    assert created.tenant_id == tenant_id
    assert created.capacity == 20
    assert "PROJECTOR" in created.skills

    fetched = service.get_resource(tenant_id, created.id)
    assert fetched.id == created.id
    assert fetched.display_label == "Conference Room A"


def test_resource_tenant_isolation() -> None:
    """Verify resource in tenant A cannot be fetched by tenant B."""
    service = ResourceService()
    created = service.register_resource(
        "tenant-a",
        ResourceCreate(
            resource_key="res-tech-1",
            type=ResourceType.PERSON,
            display_label="Alice Tech",
        ),
    )
    with pytest.raises(ResourceNotFoundError):
        service.get_resource("tenant-b", created.id)


def test_availability_timezone_validation() -> None:
    """Verify naive datetimes are rejected for availability intervals."""
    with pytest.raises(ValueError, match="timezone-aware"):
        AvailabilityIntervalBase(
            start_at=datetime(2026, 11, 9, 9, 0),  # Naive
            end_at=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
        )


def test_availability_ordering_validation() -> None:
    """Verify end_at before start_at is rejected."""
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="cannot be earlier"):
        AvailabilityIntervalBase(
            start_at=now,
            end_at=now - timedelta(hours=1),
        )


def test_record_and_filter_availability() -> None:
    """Verify availability intervals can be recorded and filtered by time window."""
    service = ResourceService()
    tenant_id = "tenant-avail-001"
    res = service.register_resource(
        tenant_id,
        ResourceCreate(
            resource_key="res-eq-1",
            type=ResourceType.EQUIPMENT,
            display_label="Diagnostic Meter",
        ),
    )

    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    service.record_availability(
        tenant_id,
        AvailabilityIntervalCreate(
            resource_id=res.id,
            start_at=t0,
            end_at=t0 + timedelta(hours=2),
            interval_type=IntervalType.UNAVAILABLE,
            reason="Calibration",
        ),
    )
    service.record_availability(
        tenant_id,
        AvailabilityIntervalCreate(
            resource_id=res.id,
            start_at=t0 + timedelta(hours=4),
            end_at=t0 + timedelta(hours=6),
            interval_type=IntervalType.UNAVAILABLE,
            reason="Routine Maintenance",
        ),
    )

    # Query all
    all_inv = service.get_availability(tenant_id, res.id)
    assert len(all_inv) == 2

    # Query window covering only second interval
    window_start = t0 + timedelta(hours=3)
    window_end = t0 + timedelta(hours=7)
    filtered = service.get_availability(tenant_id, res.id, window_start, window_end)
    assert len(filtered) == 1
    assert filtered[0].reason == "Routine Maintenance"
