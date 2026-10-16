"""Unit tests for deterministic Google Calendar event-ID codec.

Part of Phase 7C Real Calendar Provider Adapter.
Verifies:
- All required test inputs from specification:
  - "e7-test-ev-1234abcd"
  - "erp-event-123"
  - "sis/class/2026/47"
  - "meeting_ABC-998"
  - "vendor:event:781"
  - Unicode input
  - Very long external ID
- Constraint compliance: ^[a-v0-9]{5,1024}$
- Determinism and stability across calls
- Same input produces same output
- Different inputs produce different outputs
- Idempotency: re-encoding an already-encoded ID returns identical value
- Rejection of empty/whitespace input
"""

import re

import pytest

from app.integrations.calendar.codec import (
    GOOGLE_EVENT_ID_PATTERN,
    google_calendar_event_id,
    is_encoded_e7_google_event_id,
    is_legal_google_calendar_event_id,
)


@pytest.mark.parametrize(
    "external_id",
    [
        "e7-test-ev-1234abcd",
        "erp-event-123",
        "sis/class/2026/47",
        "meeting_ABC-998",
        "vendor:event:781",
        "Unicode_Event_日本語_öäü_🎉_2026",
        "A" * 500,
        "uuid-550e8400-e29b-41d4-a716-446655440000",
        "e7_ev_tenant-123_sched-key-456_act-789",
    ],
)
def test_codec_maps_arbitrary_ids_to_legal_google_format(external_id: str) -> None:
    """Every generated Google ID must satisfy ^[a-v0-9]{5,1024}$."""
    encoded = google_calendar_event_id(external_id)

    # 1. Matches Google Calendar regex constraints
    assert is_legal_google_calendar_event_id(encoded)
    assert GOOGLE_EVENT_ID_PATTERN.match(encoded) is not None

    # 2. Strict character set check: only lowercase [0-9a-v]
    assert re.match(r"^[0-9a-v]+$", encoded) is not None
    assert "-" not in encoded
    assert "_" not in encoded
    assert "/" not in encoded
    assert ":" not in encoded

    # 3. Length constraints (5 to 1024, our SHA-256 strategy produces 66)
    assert 5 <= len(encoded) <= 1024
    assert len(encoded) == 66
    assert encoded.startswith("e7")

    # 4. Identified as valid encoded e7 ID
    assert is_encoded_e7_google_event_id(encoded)


def test_codec_determinism_across_calls() -> None:
    """Same input must always produce exact same output across repeated calls."""
    sample_inputs = [
        "e7-test-ev-1234abcd",
        "erp-event-123",
        "sis/class/2026/47",
        "meeting_ABC-998",
        "vendor:event:781",
    ]
    for inp in sample_inputs:
        out1 = google_calendar_event_id(inp)
        out2 = google_calendar_event_id(inp)
        out3 = google_calendar_event_id(inp)
        assert out1 == out2 == out3


def test_codec_distinct_inputs_produce_distinct_outputs() -> None:
    """Different inputs must produce different outputs (collision resistance)."""
    inputs = [
        "e7-test-ev-1234abcd",
        "erp-event-123",
        "sis/class/2026/47",
        "meeting_ABC-998",
        "vendor:event:781",
        "event_a",
        "event_b",
    ]
    outputs = [google_calendar_event_id(i) for i in inputs]
    assert len(outputs) == len(set(outputs))


def test_codec_idempotency_on_already_encoded_id() -> None:
    """Encoding an already-encoded ID must return identical string (idempotent)."""
    raw_id = "meeting_ABC-998"
    encoded = google_calendar_event_id(raw_id)
    re_encoded = google_calendar_event_id(encoded)
    assert re_encoded == encoded


def test_codec_rejects_empty_or_whitespace_identifiers() -> None:
    """Empty or whitespace-only inputs must be rejected with ValueError."""
    with pytest.raises(ValueError, match="must not be empty"):
        google_calendar_event_id("")

    with pytest.raises(ValueError, match="must not be empty"):
        google_calendar_event_id("   ")
