"""Deterministic Google Calendar event-ID codec.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- Google Calendar event resource ID constraints:
  - Allowed characters: base32hex [0-9a-v] (lowercase letters a-v and digits 0-9).
  - Length: between 5 and 1024 characters.
  - Unique within the calendar.
- Deterministic, restart-safe, collision-resistant mapping from arbitrary
  provider-neutral external event IDs (e.g. 'e7-test-ev-...', 'erp-event-123',
  'sis/class/2026/47', 'meeting_ABC-998', 'vendor:event:781') to legal Google IDs.
- Strategy: 'e7' + sha256(external_event_id.encode('utf-8')).hexdigest()
  - Total length: 66 characters (2 + 64).
  - Characters: 'e', '7', and lowercase hex [0-9a-f] (which is a strict subset of [0-9a-v]).
  - Idempotency: if an identifier is already in this format, returns as-is.
  - No Python hash() (no per-process randomized salt).
  - No random UUIDs (preserves create idempotency on retries).
  - No lossy character stripping.
  - No secrets involved.
"""

import hashlib
import re

GOOGLE_EVENT_ID_PATTERN = re.compile(r"^[a-v0-9]{5,1024}$")


def is_legal_google_calendar_event_id(value: str) -> bool:
    """Return True if value satisfies Google Calendar event.id constraints."""
    return bool(GOOGLE_EVENT_ID_PATTERN.match(value))


def is_encoded_e7_google_event_id(value: str) -> bool:
    """Return True if value is already an encoded 'e7' + SHA256 hex digest ID."""
    if len(value) == 66 and value.startswith("e7"):
        return all(c in "0123456789abcdef" for c in value[2:])
    return False


def google_calendar_event_id(external_event_id: str) -> str:
    """Map an arbitrary provider-neutral external event ID to a legal Google Calendar event ID.

    Requirements satisfied:
    - Deterministic across calls and process restarts.
    - Characters strictly in [a-v0-9] (using prefix 'e7' + SHA256 lowercase hex).
    - Length is 66 characters (well within [5, 1024]).
    - Idempotent: repeated encoding returns the same provider ID.
    - Preserves create idempotency: same tenant/calendar/external ID produces same Google ID.
    """
    if not external_event_id or not str(external_event_id).strip():
        raise ValueError("external_event_id must not be empty or whitespace")
    clean_id = str(external_event_id).strip()
    if is_encoded_e7_google_event_id(clean_id):
        return clean_id

    digest = hashlib.sha256(clean_id.encode("utf-8")).hexdigest()
    google_id = f"e7{digest}"
    return google_id


__all__ = [
    "GOOGLE_EVENT_ID_PATTERN",
    "google_calendar_event_id",
    "is_encoded_e7_google_event_id",
    "is_legal_google_calendar_event_id",
]
