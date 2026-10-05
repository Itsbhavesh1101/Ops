"""app.events package — Canonical Event Foundation.

Part of Phase 1 Foundation Task 1G.1.
Exposes canonical versioned event envelope contract, factory, and serialization helper.
"""

from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.events.serialization import serialize_event_envelope

__all__ = [
    "EventCreate",
    "EventEnvelope",
    "create_event_envelope",
    "serialize_event_envelope",
]
