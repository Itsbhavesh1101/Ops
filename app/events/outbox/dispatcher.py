"""Single-event outbox dispatch orchestration.

Part of Phase 1 Foundation Task 1G.3B2.
Orchestrates the lifecycle of claiming an outbox event, publishing it to a provider-neutral
event transport outside any database transaction, and finalizing the event's lifecycle state
(mark published or reschedule failure) in a separate, short database transaction.

Guarantees:
- Database locks are NEVER held open across network publication.
- Exact three-phase execution:
  1. Transaction A (Claim): claim single event with SKIP LOCKED, commit claim, exit UoW.
  2. Network publication: publish exact EventEnvelope with timeout.
  3. Transaction B (Settlement): mark published OR reschedule failure in fresh UoW, commit.
- Deterministic capped exponential retry backoff without random jitter.
- Ambiguous network timeouts are treated safely as retryable failures.
- Zero broker-specific transport or aio-pika dependencies;
  depends only on provider-neutral EventPublisher.
"""

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import timedelta
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.telemetry.outbox import get_outbox_telemetry
from app.db.uow import SqlAlchemyUnitOfWork, UnitOfWork, UnitOfWorkError
from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.dispatch_repository import (
    OutboxClaimLostError,
    OutboxDispatchRepository,
)
from app.events.publishers.base import EventPublisher, EventPublishError


class OutboxDispatchPolicy(BaseModel):
    """Immutable technical infrastructure policy for outbox event dispatching.

    Defines lease timing, publication timeouts, and deterministic capped exponential backoff.
    Contains strictly explicit configuration without hidden defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_duration: timedelta
    publish_timeout: timedelta
    retry_base_delay: timedelta
    retry_max_delay: timedelta
    retry_multiplier: float
    batch_size: int = 1
    publish_concurrency: int = 1

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Validate invariant bounds across technical dispatch policy settings."""
        if self.lease_duration <= timedelta(0):
            raise ValueError(f"lease_duration must be strictly positive, got {self.lease_duration}")
        if self.publish_timeout <= timedelta(0):
            raise ValueError(
                f"publish_timeout must be strictly positive, got {self.publish_timeout}"
            )
        if self.publish_timeout >= self.lease_duration:
            raise ValueError(
                f"publish_timeout ({self.publish_timeout}) must be strictly less than "
                f"lease_duration ({self.lease_duration})"
            )
        if self.retry_base_delay <= timedelta(0):
            raise ValueError(
                f"retry_base_delay must be strictly positive, got {self.retry_base_delay}"
            )
        if self.retry_max_delay < self.retry_base_delay:
            raise ValueError(
                f"retry_max_delay ({self.retry_max_delay}) must be greater than or equal to "
                f"retry_base_delay ({self.retry_base_delay})"
            )
        if not math.isfinite(self.retry_multiplier) or self.retry_multiplier < 1.0:
            raise ValueError(
                f"retry_multiplier must be a finite number >= 1.0, got {self.retry_multiplier}"
            )
        if self.batch_size < 1 or self.batch_size > 100:
            raise ValueError(f"batch_size must be between 1 and 100, got {self.batch_size}")
        if self.publish_concurrency < 1 or self.publish_concurrency > 50:
            raise ValueError(
                f"publish_concurrency must be between 1 and 50, got {self.publish_concurrency}"
            )
        return self

    def compute_retry_delay(self, dispatch_attempt_count: int) -> timedelta:
        """Calculate deterministic capped exponential backoff delay.

        Rules:
        - attempt 1: base_delay
        - attempt 2: base_delay * multiplier
        - attempt 3: base_delay * multiplier^2
        - bounded by: retry_max_delay
        - overflow-safe: caps intermediate values to avoid float overflow on high attempts.
        """
        if dispatch_attempt_count <= 1 or self.retry_multiplier == 1.0:
            return min(self.retry_base_delay, self.retry_max_delay)

        exponent = min(dispatch_attempt_count - 1, 100)
        try:
            calculated_seconds = self.retry_base_delay.total_seconds() * (
                self.retry_multiplier**exponent
            )
        except OverflowError:
            return self.retry_max_delay

        capped_seconds = min(calculated_seconds, self.retry_max_delay.total_seconds())
        return timedelta(seconds=capped_seconds)


class DispatchOutcome(StrEnum):
    """Possible outcomes of a single outbox dispatch operation."""

    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    CLAIM_LOST_AFTER_PUBLISH = "CLAIM_LOST_AFTER_PUBLISH"
    CLAIM_LOST_AFTER_FAILURE = "CLAIM_LOST_AFTER_FAILURE"


class DispatchResult(BaseModel):
    """Immutable result of a single outbox dispatch operation.

    Contains outcome classification and safe operational identifiers.
    Contains zero credentials, payloads, connection strings, or raw exception traces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DispatchOutcome
    event_id: UUID | None = None
    dispatch_attempt_count: int | None = None
    retry_delay: timedelta | None = None


class OutboxDispatcher:
    """Orchestrates single-event outbox dispatch operations across transaction boundaries."""

    def __init__(
        self,
        *,
        uow_factory: (
            Callable[[], SqlAlchemyUnitOfWork] | Callable[[], UnitOfWork] | Callable[[], Any]
        ),
        publisher: EventPublisher,
        policy: OutboxDispatchPolicy,
    ) -> None:
        """Initialize OutboxDispatcher with dependencies.

        Args:
            uow_factory: Callable producing a fresh UnitOfWork instance per database transition.
            publisher: Started provider-neutral EventPublisher instance.
            policy: Validated technical dispatch policy.

        Raises:
            TypeError: If arguments do not conform to expected protocols/types.
        """
        if not callable(uow_factory):
            raise TypeError("uow_factory must be a callable returning a UnitOfWork")
        if not isinstance(publisher, EventPublisher):
            raise TypeError(
                f"publisher must implement EventPublisher protocol, got {type(publisher).__name__}"
            )
        if not isinstance(policy, OutboxDispatchPolicy):
            raise TypeError(f"policy must be an OutboxDispatchPolicy, got {type(policy).__name__}")

        self._uow_factory = uow_factory
        self._publisher = publisher
        self._policy = policy

    async def dispatch_once(self, *, event_id: UUID | None = None) -> DispatchResult:
        """Execute a single bounded outbox event dispatch attempt.

        Phases:
        1. Transaction A (Claim):
           - Opens fresh UnitOfWork.
           - Claims up to 1 ready event with policy.lease_duration
             (optionally targeted by event_id).
           - If 0 events: exits UoW, returns IDLE.
           - If 1 event: commits UoW, exits UoW.
           - If >1 events: invariant violation, raises RuntimeError.
           - If commit fails: propagates exception (NO publish occurs).

        2. Network Publication (Outside any DB transaction):
           - Bounds await publisher.publish(claimed.envelope) with policy.publish_timeout.
           - If timeout or expected EventPublishError: marks expected_failure = True.
           - If unexpected exception (including PublisherStateError) or CancelledError:
             propagates immediately (NO catch).

        3. Transaction B (Settlement):
           - If publish_succeeded:
             - Opens fresh UnitOfWork.
             - Calls mark_published(claimed.event_id, claimed.claim_token).
             - Commits UoW.
             - Returns PUBLISHED (or CLAIM_LOST_AFTER_PUBLISH if OutboxClaimLostError).
           - If expected_failure:
             - Computes retry_delay via policy.
             - Opens fresh UnitOfWork.
             - Calls reschedule_failure(claimed.event_id, claimed.claim_token, retry_delay).
             - Commits UoW.
             - Returns RETRY_SCHEDULED (or CLAIM_LOST_AFTER_FAILURE if OutboxClaimLostError).
        """
        # --- Phase 1: Claim Transaction ---
        claimed_events: tuple[ClaimedOutboxEvent, ...]
        async with self._uow_factory() as claim_uow:
            session = getattr(claim_uow, "session", None)
            if session is None:
                raise UnitOfWorkError("UnitOfWork does not provide an active database session")
            claim_repo = OutboxDispatchRepository(session)
            claimed_events = await claim_repo.claim_ready(
                limit=1,
                lease_duration=self._policy.lease_duration,
                event_id=event_id,
            )
            if len(claimed_events) > 1:
                raise RuntimeError(
                    f"Invariant violation: claim_ready returned {len(claimed_events)} events, "
                    "expected at most 1"
                )
            if claimed_events:
                await claim_uow.commit()

        # If no event was claimed, return IDLE immediately.
        if not claimed_events:
            return DispatchResult(outcome=DispatchOutcome.IDLE)

        claimed = claimed_events[0]
        telemetry = get_outbox_telemetry()
        try:
            telemetry.record_attempt()
        except BaseException:
            pass
        start_time = time.monotonic()
        error_category = "unexpected"

        dispatch_span_ctx: Any
        try:
            dispatch_span_ctx = telemetry.span_dispatch(
                event_type=claimed.envelope.event_type,
                retry_attempt_count=claimed.dispatch_attempt_count,
                correlation_id=claimed.envelope.correlation_id,
            )
        except BaseException:
            dispatch_span_ctx = nullcontext()

        with dispatch_span_ctx as span:
            # --- Phase 2: Network Publication (No DB transaction active) ---
            publish_succeeded = False
            try:
                async with asyncio.timeout(self._policy.publish_timeout.total_seconds()):
                    await self._publisher.publish(claimed.envelope)
                publish_succeeded = True
            except TimeoutError:
                publish_succeeded = False
                error_category = "timeout"
            except EventPublishError:
                publish_succeeded = False
                error_category = "publish_error"

            # --- Phase 3: Settlement Transaction ---
            if publish_succeeded:
                try:
                    async with self._uow_factory() as settlement_uow:
                        session = getattr(settlement_uow, "session", None)
                        if session is None:
                            raise UnitOfWorkError(
                                "UnitOfWork does not provide an active database session"
                            )
                        repo = OutboxDispatchRepository(session)
                        await repo.mark_published(
                            event_id=claimed.event_id,
                            claim_token=claimed.claim_token,
                        )
                        await settlement_uow.commit()
                except OutboxClaimLostError:
                    try:
                        if hasattr(span, "is_recording") and span.is_recording():
                            span.set_attribute("dispatch_outcome", "claim_lost_after_publish")
                    except BaseException:
                        pass
                    try:
                        telemetry.record_failure("database_error", time.monotonic() - start_time)
                    except BaseException:
                        pass
                    return DispatchResult(
                        outcome=DispatchOutcome.CLAIM_LOST_AFTER_PUBLISH,
                        event_id=claimed.event_id,
                        dispatch_attempt_count=claimed.dispatch_attempt_count,
                    )
                except BaseException:
                    try:
                        telemetry.record_failure("database_error", time.monotonic() - start_time)
                    except BaseException:
                        pass
                    raise

                try:
                    if hasattr(span, "is_recording") and span.is_recording():
                        span.set_attribute("dispatch_outcome", "published")
                except BaseException:
                    pass
                try:
                    telemetry.record_success(time.monotonic() - start_time)
                except BaseException:
                    pass
                return DispatchResult(
                    outcome=DispatchOutcome.PUBLISHED,
                    event_id=claimed.event_id,
                    dispatch_attempt_count=claimed.dispatch_attempt_count,
                )

            # Failure / Timeout path: Reschedule with backoff
            retry_delay = self._policy.compute_retry_delay(claimed.dispatch_attempt_count)
            try:
                async with self._uow_factory() as settlement_uow:
                    session = getattr(settlement_uow, "session", None)
                    if session is None:
                        raise UnitOfWorkError(
                            "UnitOfWork does not provide an active database session"
                        )
                    repo = OutboxDispatchRepository(session)
                    await repo.reschedule_failure(
                        event_id=claimed.event_id,
                        claim_token=claimed.claim_token,
                        retry_delay=retry_delay,
                    )
                    await settlement_uow.commit()
            except OutboxClaimLostError:
                try:
                    if hasattr(span, "is_recording") and span.is_recording():
                        span.set_attribute("dispatch_outcome", "claim_lost_after_failure")
                except BaseException:
                    pass
                try:
                    telemetry.record_failure("database_error", time.monotonic() - start_time)
                except BaseException:
                    pass
                return DispatchResult(
                    outcome=DispatchOutcome.CLAIM_LOST_AFTER_FAILURE,
                    event_id=claimed.event_id,
                    dispatch_attempt_count=claimed.dispatch_attempt_count,
                    retry_delay=retry_delay,
                )
            except BaseException:
                try:
                    telemetry.record_failure("database_error", time.monotonic() - start_time)
                except BaseException:
                    pass
                raise

            try:
                if hasattr(span, "is_recording") and span.is_recording():
                    span.set_attribute("dispatch_outcome", "retry_scheduled")
            except BaseException:
                pass
            try:
                telemetry.record_failure(error_category, time.monotonic() - start_time)
            except BaseException:
                pass
            return DispatchResult(
                outcome=DispatchOutcome.RETRY_SCHEDULED,
                event_id=claimed.event_id,
                dispatch_attempt_count=claimed.dispatch_attempt_count,
                retry_delay=retry_delay,
            )

    async def dispatch_batch(
        self,
        *,
        batch_size: int | None = None,
        publish_concurrency: int | None = None,
    ) -> list[DispatchResult]:
        """Claim and dispatch a bounded batch of outbox events across transaction boundaries.

        Phases:
        1. Transaction A (Batch Claim):
           - Opens fresh UnitOfWork.
           - Claims up to batch_size ready events with SKIP LOCKED and policy.lease_duration.
           - If 0 events: commits and returns [].
           - If >0 events: commits claim UoW once.

        2. Pipelined Network Publication (Outside DB transactions):
           - Publishes claimed events concurrently bounded by publish_concurrency semaphore.
           - Uses individual per-event timeout (policy.publish_timeout) and publisher confirms.
           - Isolates per-event errors (timeout or EventPublishError marks only that event failed).

        3. Transaction B (Batch Settlement):
           - Opens a single fresh UnitOfWork.
           - Marks all successfully published events as DISPATCHED.
           - Reschedules all failed events with exponential backoff retry delays.
           - Commits UoW once for the entire batch.
        """
        size = batch_size if batch_size is not None else getattr(self._policy, "batch_size", 1)
        concurrency = (
            publish_concurrency
            if publish_concurrency is not None
            else getattr(self._policy, "publish_concurrency", 1)
        )

        # --- Phase 1: Batch Claim Transaction ---
        claimed_events: tuple[ClaimedOutboxEvent, ...]
        async with self._uow_factory() as claim_uow:
            session = getattr(claim_uow, "session", None)
            if session is None:
                raise UnitOfWorkError("UnitOfWork does not provide an active database session")
            claim_repo = OutboxDispatchRepository(session)
            claimed_events = await claim_repo.claim_ready(
                limit=size,
                lease_duration=self._policy.lease_duration,
            )
            if claimed_events:
                await claim_uow.commit()

        if not claimed_events:
            return []

        # --- Phase 2: Pipelined Network Publication ---
        telemetry = get_outbox_telemetry()
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _publish_single(
            claimed: ClaimedOutboxEvent,
        ) -> tuple[ClaimedOutboxEvent, bool, str, float]:
            try:
                telemetry.record_attempt()
            except BaseException:
                pass
            start_t = time.monotonic()
            async with sem:
                try:
                    async with asyncio.timeout(self._policy.publish_timeout.total_seconds()):
                        await self._publisher.publish(claimed.envelope)
                    dur = time.monotonic() - start_t
                    try:
                        telemetry.record_success(dur)
                    except BaseException:
                        pass
                    return (claimed, True, "", dur)
                except TimeoutError:
                    dur = time.monotonic() - start_t
                    try:
                        telemetry.record_failure("timeout", dur)
                    except BaseException:
                        pass
                    return (claimed, False, "timeout", dur)
                except EventPublishError:
                    dur = time.monotonic() - start_t
                    try:
                        telemetry.record_failure("publish_error", dur)
                    except BaseException:
                        pass
                    return (claimed, False, "publish_error", dur)

        pub_outcomes = await asyncio.gather(*(_publish_single(e) for e in claimed_events))

        # --- Phase 3: Batch Settlement Transaction ---
        results: list[DispatchResult] = []
        async with self._uow_factory() as settlement_uow:
            session = getattr(settlement_uow, "session", None)
            if session is None:
                raise UnitOfWorkError("UnitOfWork does not provide an active database session")
            repo = OutboxDispatchRepository(session)

            for claimed, success, _err_cat, _ in pub_outcomes:
                if success:
                    try:
                        await repo.mark_published(
                            event_id=claimed.event_id,
                            claim_token=claimed.claim_token,
                        )
                        results.append(
                            DispatchResult(
                                outcome=DispatchOutcome.PUBLISHED,
                                event_id=claimed.event_id,
                                dispatch_attempt_count=claimed.dispatch_attempt_count,
                            )
                        )
                    except OutboxClaimLostError:
                        results.append(
                            DispatchResult(
                                outcome=DispatchOutcome.CLAIM_LOST_AFTER_PUBLISH,
                                event_id=claimed.event_id,
                                dispatch_attempt_count=claimed.dispatch_attempt_count,
                            )
                        )
                else:
                    delay = self._policy.compute_retry_delay(claimed.dispatch_attempt_count)
                    try:
                        await repo.reschedule_failure(
                            event_id=claimed.event_id,
                            claim_token=claimed.claim_token,
                            retry_delay=delay,
                        )
                        results.append(
                            DispatchResult(
                                outcome=DispatchOutcome.RETRY_SCHEDULED,
                                event_id=claimed.event_id,
                                dispatch_attempt_count=claimed.dispatch_attempt_count,
                                retry_delay=delay,
                            )
                        )
                    except OutboxClaimLostError:
                        results.append(
                            DispatchResult(
                                outcome=DispatchOutcome.CLAIM_LOST_AFTER_FAILURE,
                                event_id=claimed.event_id,
                                dispatch_attempt_count=claimed.dispatch_attempt_count,
                                retry_delay=delay,
                            )
                        )

            await settlement_uow.commit()

        return results

    # Alias for dispatch_once
    dispatch_one = dispatch_once
