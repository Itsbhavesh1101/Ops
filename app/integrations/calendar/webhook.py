"""Google Calendar webhook / push notification handler with durable replay protection.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- X-Goog-Channel-ID, X-Goog-Resource-ID, and X-Goog-Channel-Token verification.
- Resolution of tenant_id and external_calendar_id strictly from durable watch state.
- Safe handshake handling (acknowledges 'sync' with zero side effects).
- Durable replay protection using IdempotencyRepository.
- Incremental sync triggering via InboundCalendarSyncService.
- Truthful reporting: never assumes body contains event content (Google push is a signal to sync).
"""

import hashlib

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency.contracts import (
    RequestFingerprint,
    create_idempotency_identity,
)
from app.core.idempotency.repository import (
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.inbound import InboundCalendarSyncService
from app.integrations.calendar.watch import CalendarWatchRepository

logger = structlog.get_logger(__name__)


class WebhookProcessingResult(BaseModel):
    """Result of processing an inbound Google Calendar push notification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    resource_state: str
    status: str  # 'PROCESSED', 'SYNC_HANDSHAKE_ACKNOWLEDGED', 'DUPLICATE_REPLAYED', 'REJECTED'
    message: str


class GoogleCalendarWebhookHandler:
    """Handler for Google Calendar push notifications with in-memory and durable support."""

    def __init__(
        self,
        expected_channel_token: str | None = None,
        allowed_channel_ids: set[str] | None = None,
    ) -> None:
        self._expected_channel_token = expected_channel_token
        self._allowed_channel_ids = allowed_channel_ids or set()
        # In-memory replay cache for legacy/mock testing: (channel_id, message_number) -> processed
        self._processed_messages: set[tuple[str, int]] = set()

    def register_channel(self, channel_id: str) -> None:
        """Register an allowed channel ID."""
        self._allowed_channel_ids.add(channel_id)

    def handle_webhook_headers(
        self, headers: dict[str, str]
    ) -> WebhookProcessingResult:
        """Process inbound Google push notification headers with in-memory deduplication."""
        norm = {k.lower(): v for k, v in headers.items()}

        channel_id = norm.get("x-goog-channel-id", "").strip()
        resource_state = norm.get("x-goog-resource-state", "").strip().lower()
        channel_token = norm.get("x-goog-channel-token", "").strip()
        msg_num_str = norm.get("x-goog-message-number", "").strip()

        if not channel_id:
            return WebhookProcessingResult(
                channel_id="",
                resource_state=resource_state,
                status="REJECTED",
                message="Missing required X-Goog-Channel-ID header.",
            )

        if self._allowed_channel_ids and channel_id not in self._allowed_channel_ids:
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="REJECTED",
                message=f"Unauthorized channel ID '{channel_id}'.",
            )

        if self._expected_channel_token and channel_token != self._expected_channel_token:
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="REJECTED",
                message="Channel token verification failed.",
            )

        if resource_state == "sync":
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="SYNC_HANDSHAKE_ACKNOWLEDGED",
                message="Google Calendar watch channel handshake acknowledged.",
            )

        message_number = 0
        if msg_num_str:
            try:
                message_number = int(msg_num_str)
            except ValueError:
                pass

        if message_number > 0:
            replay_key = (channel_id, message_number)
            if replay_key in self._processed_messages:
                return WebhookProcessingResult(
                    channel_id=channel_id,
                    resource_state=resource_state,
                    status="DUPLICATE_REPLAYED",
                    message=f"Duplicate notification message #{message_number} ignored.",
                )
            self._processed_messages.add(replay_key)

        return WebhookProcessingResult(
            channel_id=channel_id,
            resource_state=resource_state,
            status="PROCESSED",
            message="Push notification accepted for incremental synchronization.",
        )

    async def handle_durable_webhook(
        self,
        session: AsyncSession,
        headers: dict[str, str],
        adapter: CalendarAdapter | None = None,
        sync_service: InboundCalendarSyncService | None = None,
    ) -> WebhookProcessingResult:
        """Process inbound Google push notification using durable watch registration
        and idempotency.
        """
        norm = {k.lower(): v for k, v in headers.items()}

        channel_id = norm.get("x-goog-channel-id", "").strip()
        resource_id = norm.get("x-goog-resource-id", "").strip()
        resource_state = norm.get("x-goog-resource-state", "").strip().lower()
        channel_token = norm.get("x-goog-channel-token", "").strip()
        msg_num_str = norm.get("x-goog-message-number", "").strip()

        if not channel_id:
            return WebhookProcessingResult(
                channel_id="",
                resource_state=resource_state,
                status="REJECTED",
                message="Missing required X-Goog-Channel-ID header.",
            )

        # 1. Resolve tenant and calendar from durable watch state
        watch_repo = CalendarWatchRepository(session)
        watch_record = await watch_repo.get_by_channel_id(channel_id)
        if not watch_record or watch_record.status != "ACTIVE":
            if resource_state == "sync":
                # Google delivers initial 'sync' handshake immediately upon watch creation,
                # which can arrive concurrently before local durable registration commits.
                # Acknowledging 'sync' safely with zero side effects satisfies Google's handshake.
                return WebhookProcessingResult(
                    channel_id=channel_id,
                    resource_state=resource_state,
                    status="SYNC_HANDSHAKE_ACKNOWLEDGED",
                    message=(
                        "Google Calendar watch channel handshake acknowledged "
                        "(concurrent registration)."
                    ),
                )
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="REJECTED",
                message=f"Unknown or inactive watch channel ID '{channel_id}'.",
            )

        # 2. Validate Resource ID if recorded
        if watch_record.resource_id and resource_id and watch_record.resource_id != resource_id:
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="REJECTED",
                message="Resource ID mismatch.",
            )

        # 3. Validate Channel Token Hash if configured
        if watch_record.token_hash:
            if not channel_token:
                return WebhookProcessingResult(
                    channel_id=channel_id,
                    resource_state=resource_state,
                    status="REJECTED",
                    message="Missing channel token.",
                )
            token_hash = hashlib.sha256(channel_token.encode("utf-8")).hexdigest()
            if token_hash != watch_record.token_hash:
                return WebhookProcessingResult(
                    channel_id=channel_id,
                    resource_state=resource_state,
                    status="REJECTED",
                    message="Channel token verification failed.",
                )

        # 4. Handle initial sync handshake
        if resource_state == "sync":
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="SYNC_HANDSHAKE_ACKNOWLEDGED",
                message="Google Calendar watch channel handshake acknowledged.",
            )

        # 5. Parse and validate message number
        if not msg_num_str or not msg_num_str.isdigit() or int(msg_num_str) <= 0:
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="REJECTED",
                message="Malformed or missing X-Goog-Message-Number header.",
            )
        message_number = int(msg_num_str)

        # 6. Durable Replay Protection via IdempotencyRepository
        tenant_id = watch_record.tenant_id
        key_raw = f"google-calendar:{channel_id}:{message_number}"
        fingerprint_raw = f"{channel_id}:{resource_id}:{message_number}:{resource_state}"
        fingerprint_digest = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()

        identity = create_idempotency_identity(
            tenant_id=tenant_id,
            operation_name="calendar.google_webhook",
            raw_key=key_raw,
        )
        idemp_repo = IdempotencyRepository(session)
        decision = await idemp_repo.acquire_or_observe(
            identity=identity,
            request_fingerprint=RequestFingerprint(fingerprint_digest),
        )

        if decision.decision != IdempotencyDecisionType.ACQUIRED:
            return WebhookProcessingResult(
                channel_id=channel_id,
                resource_state=resource_state,
                status="DUPLICATE_REPLAYED",
                message=f"Duplicate notification message #{message_number} ignored.",
            )

        # 7. Signal-to-sync: Trigger incremental sync
        effective_adapter = adapter or GoogleCalendarAdapter()
        effective_sync = sync_service or InboundCalendarSyncService()

        try:
            await effective_sync.import_incremental_changes(
                session=session,
                tenant_id=tenant_id,
                adapter=effective_adapter,
                external_calendar_id=watch_record.external_calendar_id,
                provider=watch_record.provider,
            )
            await session.commit()
        except Exception as sync_exc:
            await session.rollback()
            logger.error(
                "calendar_webhook_sync_failed",
                channel_id=channel_id,
                tenant_id=tenant_id,
                error=str(sync_exc),
            )
            raise

        return WebhookProcessingResult(
            channel_id=channel_id,
            resource_state=resource_state,
            status="PROCESSED",
            message="Push notification accepted and synchronized.",
        )


__all__ = [
    "GoogleCalendarWebhookHandler",
    "WebhookProcessingResult",
]
