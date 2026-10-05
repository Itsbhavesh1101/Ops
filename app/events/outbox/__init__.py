"""app.events.outbox package — Transactional Outbox Foundation.

Part of Phase 1 Foundation Task 1G.2, Task 1G.3B1, and Task 1G.3B2.
Exposes OutboxEventRecord model, OutboxRepository for durable event staging,
ClaimedOutboxEvent value object, OutboxClaimLostError, OutboxDispatchRepository
for dispatch state transitions, and OutboxDispatcher / OutboxDispatchPolicy
for single-event dispatch orchestration.
"""

from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.dispatch_repository import (
    OutboxClaimLostError,
    OutboxDispatchRepository,
)
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    DispatchResult,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.outbox.repository import OutboxRepository
from app.events.outbox.runner import (
    ManagedEventPublisher,
    OutboxDispatcherRunner,
    OutboxRunnerPolicy,
    RunnerState,
)

__all__ = [
    "ClaimedOutboxEvent",
    "DispatchOutcome",
    "DispatchResult",
    "ManagedEventPublisher",
    "OutboxClaimLostError",
    "OutboxDispatchPolicy",
    "OutboxDispatchRepository",
    "OutboxDispatcher",
    "OutboxDispatcherRunner",
    "OutboxEventRecord",
    "OutboxRepository",
    "OutboxRunnerPolicy",
    "RunnerState",
]
