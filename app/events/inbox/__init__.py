"""app.events.inbox package — Transactional inbox and consumer deduplication.

Part of Phase 1 Foundation Task 1G.4A and Task 1G.4B.
Provides privacy-minimizing deduplication persistence and provider-neutral consumer orchestration.
"""

from app.events.inbox.models import InboxMessageRecord
from app.events.inbox.processor import (
    ConsumerEventHandler,
    ConsumerEventProcessor,
    ConsumerProcessingOutcome,
    ConsumerProcessingResult,
)
from app.events.inbox.repository import (
    CONSUMER_NAME_MAX_LENGTH,
    CONSUMER_NAME_REGEX,
    InboxRepository,
    validate_consumer_name,
)

__all__ = [
    "CONSUMER_NAME_MAX_LENGTH",
    "CONSUMER_NAME_REGEX",
    "ConsumerEventHandler",
    "ConsumerEventProcessor",
    "ConsumerProcessingOutcome",
    "ConsumerProcessingResult",
    "InboxMessageRecord",
    "InboxRepository",
    "validate_consumer_name",
]
