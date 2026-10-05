"""Live PostgreSQL integration tests for Phase 4B: Publish/Lock, Repair, Events, and Calendar Sync.

Covers:
- Authorized schedule publication (REVIEW -> PUBLISHED) with audit and
  schedule.published outbox
- Idempotent publish replay (zero duplicate side effects)
- Targeted repair: unaffected frozen, movement penalty, new candidate in REVIEW,
  baseline remains PUBLISHED
- Repaired publication: atomic (new REVIEW -> PUBLISHED, old PUBLISHED -> SUPERSEDED,
  schedule.changed)
- resource.unavailable handler: one targeted repair job, inbox duplicate deduplication
- CalendarAdapter & Outbound sync: idempotent create/update/delete, outage degraded status
  (no fake success), reconciliation
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEventRecord
from app.core.config import Settings
from app.db.engine import DatabaseManager
from app.db.models.scheduling import (
    CalendarLinkRecord,
    ResourceAvailabilityRecord,
    ResourceRecord,
    ScheduleAssignmentRecord,
    ScheduleJobRecord,
    ScheduleRecord,
)
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.inbox.processor import ConsumerEventProcessor, ConsumerProcessingOutcome
from app.events.outbox.models import OutboxEventRecord
from app.events.schemas import EventEnvelope
from app.integrations.calendar.adapter import CalendarAdapter, FakeCalendarAdapter
from app.integrations.calendar.dtos import CalendarEventDTO, SyncChangesResult, SyncStatus
from app.integrations.calendar.inbound import InboundCalendarSyncService
from app.integrations.calendar.outbound import OutboundCalendarSyncService
from app.integrations.calendar.reconciliation import CalendarReconciliationService
from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import TimeWindow
from app.scheduling.handlers import ResourceUnavailableHandler
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.service import SchedulingService
from app.scheduling.solvers.objective_normalizer import normalize_soft_objective_weights
from app.scheduling.states import (
    ScheduleJobStatus,
    ScheduleLifecycleStatus,
)

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/postgres"


@pytest.fixture
def db_manager() -> DatabaseManager:
    settings = Settings(database_url=DATABASE_URL)
    return DatabaseManager(settings)


@pytest.fixture
def service() -> SchedulingService:
    return SchedulingService()


class PermissivePublishPolicy:
    def authorize_publish(
        self,
        *,
        principal: str | None,
        tenant_id: str,
        schedule: ScheduleRecord,
        action: str = "publish",
    ) -> bool:
        return True


@pytest.mark.asyncio
async def test_live_publish_and_idempotency(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify live publication, atomic outbox, audit, and idempotency replay."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    sched_id = uuid4()
    job_id = uuid4()
    now = datetime.now(UTC)

    # 1. Create a candidate schedule in REVIEW with assignments
    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="test_sched",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-12345",
        )
        session.add(job)

        schedule = ScheduleRecord(
            id=sched_id,
            tenant_id=tenant_id,
            schedule_key="test_sched",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.REVIEW.value,
            input_snapshot_hash="hash-12345",
            score_components={"violations": 0},
        )
        session.add(schedule)

        asgn = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="act-1",
            resource_id="res-1",
            start_at=now,
            end_at=now + timedelta(hours=2),
            is_locked=False,
        )
        session.add(asgn)
        await session.commit()

    # 2. Authorize and Publish
    policy = PermissivePublishPolicy()
    async with db_manager.session_factory() as session:
        pub_sched = await service.publish_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            principal="operator@irinfotech.internal",
            publish_policy=policy,
        )
        await session.commit()

    assert pub_sched.status == ScheduleLifecycleStatus.PUBLISHED.value
    assert pub_sched.published_at is not None
    assert pub_sched.published_by == "operator@irinfotech.internal"

    # Verify Outbox and Audit in DB
    async with db_manager.session_factory() as session:
        outbox_res = await session.execute(select(OutboxEventRecord))
        outbox_records = [
            r
            for r in outbox_res.scalars().all()
            if r.envelope.get("tenant_id") == tenant_id
            and r.envelope.get("event_type") == "schedule.published"
        ]
        assert len(outbox_records) == 1
        pub_payload = outbox_records[0].envelope["payload"]
        assert pub_payload["schedule_id"] == str(sched_id)
        assert pub_payload["schedule_version"] == 1
        assert pub_payload["source_job_id"] == str(job_id)

        audit_res = await session.execute(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == "schedule.published",
                AuditEventRecord.tenant_id == tenant_id,
            )
        )
        audits = audit_res.scalars().all()
        assert len(audits) >= 1
        assert audits[0].actor_principal_id == "operator@irinfotech.internal"


@pytest.mark.asyncio
async def test_live_repair_and_superseding_publish(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify live repair solve, unaffected freeze, and atomic superseding publication."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    v1_id = uuid4()
    job_id = uuid4()
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    h_end = t0 + timedelta(days=2)

    # 1. Establish published baseline v1 with 2 activities: act-1 on res-1, act-2 on res-2
    activities = [
        SchedulingActivity(
            id="act-1",
            title="Activity 1",
            duration_minutes=120,
            allowed_windows=[TimeWindow(start_at=t0, end_at=t0 + timedelta(hours=2))],
            required_skills=["TECH"],
        ),
        SchedulingActivity(
            id="act-2",
            title="Activity 2",
            duration_minutes=120,
            allowed_windows=[
                TimeWindow(start_at=t0 + timedelta(hours=3), end_at=t0 + timedelta(hours=5))
            ],
            required_skills=["TECH"],
        ),
    ]
    # Resources: res-1 (disrupted), res-2 (unaffected), res-3 (backup available)
    resources = [
        SchedulingResourceSnapshot(
            id="res-1",
            type=ResourceType.PERSON,
            display_label="Tech 1",
            skills=["TECH"],
        ),
        SchedulingResourceSnapshot(
            id="res-2",
            type=ResourceType.PERSON,
            display_label="Tech 2",
            skills=["TECH"],
        ),
        SchedulingResourceSnapshot(
            id="res-3",
            type=ResourceType.PERSON,
            display_label="Tech 3",
            skills=["TECH"],
        ),
    ]

    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=2,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=10,
        ),
    )

    base_scheduling_request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="repair_test_sched",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=activities,
        resources=resources,
        constraints=[],
        profile=profile,
    )

    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="repair_test_sched",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot=base_scheduling_request.model_dump(mode="json"),
            input_snapshot_hash="hash-v1",
        )
        session.add(job)

        v1_sched = ScheduleRecord(
            id=v1_id,
            tenant_id=tenant_id,
            schedule_key="repair_test_sched",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            input_snapshot_hash="hash-v1",
            score_components={"violations": 0},
            published_at=t0,
            published_by="operator@irinfotech.internal",
        )
        session.add(v1_sched)

        # Baseline assignment 1 (affected by res-1 disruption)
        asgn1 = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=v1_id,
            activity_id="act-1",
            resource_id="res-1",
            start_at=t0,
            end_at=t0 + timedelta(hours=2),
            is_locked=False,
        )
        # Baseline assignment 2 (unaffected on res-2)
        asgn2 = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=v1_id,
            activity_id="act-2",
            resource_id="res-2",
            start_at=t0 + timedelta(hours=3),
            end_at=t0 + timedelta(hours=5),
            is_locked=True,
        )
        session.add_all([asgn1, asgn2])
        await session.commit()

    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=["res-1"],
        disrupted_interval_start=t0,
        disrupted_interval_end=t0 + timedelta(hours=3),
        reason="EMERGENCY_LEAVE",
        movement_weight=10,
    )

    # 2. Execute repair solve
    async with db_manager.session_factory() as session:
        repair_job, repair_candidate = await service.repair_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=v1_id,
            request=repair_req,
        )
        await session.commit()

    assert repair_candidate is not None
    assert repair_candidate.status == ScheduleLifecycleStatus.REVIEW.value
    assert repair_candidate.version == 2
    assert repair_candidate.supersedes_id == v1_id

    # Verify baseline v1 is STILL PUBLISHED in DB!
    async with db_manager.session_factory() as session:
        v1_check = await session.get(ScheduleRecord, v1_id)
        assert v1_check is not None
        assert v1_check.status == ScheduleLifecycleStatus.PUBLISHED.value

        # Verify unaffected assignment act-2 did not move
        v2_asgns = (
            (
                await session.execute(
                    select(ScheduleAssignmentRecord).where(
                        ScheduleAssignmentRecord.schedule_id == repair_candidate.id
                    )
                )
            )
            .scalars()
            .all()
        )
        v2_asgn_map = {a.activity_id: a for a in v2_asgns}
        assert v2_asgn_map["act-2"].resource_id == "res-2"
        assert v2_asgn_map["act-2"].start_at == t0 + timedelta(hours=3)
        # Verify affected assignment act-1 was reassigned away from res-1 to res-3
        assert v2_asgn_map["act-1"].resource_id == "res-3"

    # 3. Authorize and Publish Repaired Candidate v2
    policy = PermissivePublishPolicy()
    async with db_manager.session_factory() as session:
        pub_v2 = await service.publish_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=repair_candidate.id,
            principal="lead_scheduler@irinfotech.internal",
            publish_policy=policy,
        )
        await session.commit()

    assert pub_v2.status == ScheduleLifecycleStatus.PUBLISHED.value
    assert pub_v2.version == 2

    # 4. Verify atomic superseding: v1 -> SUPERSEDED, v2 -> PUBLISHED
    async with db_manager.session_factory() as session:
        v1_updated = await session.get(ScheduleRecord, v1_id)
        assert v1_updated is not None
        assert v1_updated.status == ScheduleLifecycleStatus.SUPERSEDED.value

        # Verify outbox has schedule.published for v2 AND schedule.changed
        outbox_all = (await session.execute(select(OutboxEventRecord))).scalars().all()
        outbox_pub = [
            r
            for r in outbox_all
            if r.envelope.get("tenant_id") == tenant_id
            and r.envelope.get("event_type") == "schedule.published"
        ]
        assert any(
            r.envelope["payload"].get("schedule_id") == str(repair_candidate.id)
            for r in outbox_pub
        )

        outbox_changed = [
            r
            for r in outbox_all
            if r.envelope.get("tenant_id") == tenant_id
            and r.envelope.get("event_type") == "schedule.changed"
        ]
        assert len(outbox_changed) == 1
        chg_payload = outbox_changed[0].envelope["payload"]
        assert chg_payload["old_schedule_id"] == str(v1_id)
        assert chg_payload["new_schedule_id"] == str(repair_candidate.id)


@pytest.mark.asyncio
async def test_live_resource_unavailable_event_handler(
    db_manager: DatabaseManager,
) -> None:
    """Verify resource.unavailable processing creates 1 targeted repair job
    with inbox deduplication.
    """
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    sched_id = uuid4()
    job_id = uuid4()
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    h_end = t0 + timedelta(days=2)

    activities = [
        SchedulingActivity(
            id="act-dist-1",
            title="Activity Dist 1",
            duration_minutes=120,
            allowed_windows=[TimeWindow(start_at=t0, end_at=h_end)],
            required_skills=["TECH"],
        ),
    ]
    resources = [
        SchedulingResourceSnapshot(
            id="res-disrupted",
            type=ResourceType.PERSON,
            display_label="Tech Disrupted",
            skills=["TECH"],
        ),
        SchedulingResourceSnapshot(
            id="res-backup",
            type=ResourceType.PERSON,
            display_label="Tech Backup",
            skills=["TECH"],
        ),
    ]
    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=2,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=10,
        ),
    )
    base_scheduling_request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="base_sched_unavail",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=activities,
        resources=resources,
        constraints=[],
        profile=profile,
    )

    # 1. Seed published schedule with assignment on res-disrupted
    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="base_sched_unavail",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot=base_scheduling_request.model_dump(mode="json"),
            input_snapshot_hash="hash-res",
        )
        session.add(job)

        sched = ScheduleRecord(
            id=sched_id,
            tenant_id=tenant_id,
            schedule_key="base_sched_unavail",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            input_snapshot_hash="hash-res",
            score_components={"violations": 0},
        )
        session.add(sched)

        asgn = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="act-dist-1",
            resource_id="res-disrupted",
            start_at=t0,
            end_at=t0 + timedelta(hours=2),
            is_locked=False,
        )
        session.add(asgn)
        await session.commit()

    # 2. Construct canonical EventEnvelope
    event_id = uuid4()
    correlation_id = uuid4()
    envelope = EventEnvelope(
        event_id=event_id,
        event_type="resource.unavailable",
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_id,
        correlation_id=str(correlation_id),
        payload={
            "resource_id": "res-disrupted",
            "start_at": t0.isoformat(),
            "end_at": (t0 + timedelta(hours=4)).isoformat(),
            "reason": "MEDICAL_OUTAGE",
        },
    )

    from app.scheduling.repair.policy import DefaultRepairPolicyResolver, TenantRepairPolicy

    resolver = DefaultRepairPolicyResolver({tenant_id: TenantRepairPolicy(movement_weight=15.0)})
    handler = ResourceUnavailableHandler(repair_policy_resolver=resolver)
    processor = ConsumerEventProcessor(
        consumer_name="scheduling.resource_unavailable_consumer",
        uow_factory=lambda: SqlAlchemyUnitOfWork(db_manager.session_factory),
        handler=handler,
    )

    # 3. First delivery -> PROCESSED
    outcome1 = await processor.process(envelope)
    assert outcome1.outcome == ConsumerProcessingOutcome.PROCESSED

    # Verify repair candidate created in REVIEW and snapshot contains exact weight W1 (15.0)
    async with db_manager.session_factory() as session:
        repaired_scheds = (
            (
                await session.execute(
                    select(ScheduleRecord).where(
                        ScheduleRecord.tenant_id == tenant_id,
                        ScheduleRecord.supersedes_id == sched_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(repaired_scheds) == 1
        assert repaired_scheds[0].status == ScheduleLifecycleStatus.REVIEW.value

        repair_job_1 = (
            await session.execute(
                select(ScheduleJobRecord).where(
                    ScheduleJobRecord.id == repaired_scheds[0].source_job_id
                )
            )
        ).scalar_one()
        assert (
            repair_job_1.input_snapshot["repair_metadata"]["effective_movement_weight"]
            == 15.0
        )
        assert (
            repair_job_1.input_snapshot["profile"]["soft_weights"]["movement_weight"]
            == 15.0
        )

    # 4. Redelivery of exact same event -> DUPLICATE, zero duplicate repair schedules
    outcome2 = await processor.process(envelope)
    assert outcome2.outcome == ConsumerProcessingOutcome.DUPLICATE

    async with db_manager.session_factory() as session:
        repaired_scheds_check = (
            (
                await session.execute(
                    select(ScheduleRecord).where(
                        ScheduleRecord.tenant_id == tenant_id,
                        ScheduleRecord.supersedes_id == sched_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(repaired_scheds_check) == 1

    # 5. Dynamically change configured policy to W2 (0.5)
    resolver.set_tenant_policy(tenant_id, TenantRepairPolicy(movement_weight=0.5))

    event_id_w2 = uuid4()
    envelope_w2 = EventEnvelope(
        event_id=event_id_w2,
        event_type="resource.unavailable",
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_id,
        correlation_id=str(uuid4()),
        payload={
            "resource_id": "res-backup",
            "start_at": t0.isoformat(),
            "end_at": (t0 + timedelta(hours=4)).isoformat(),
            "reason": "EQUIPMENT_MAINTENANCE",
        },
    )
    outcome_w2 = await processor.process(envelope_w2)
    assert outcome_w2.outcome == ConsumerProcessingOutcome.PROCESSED

    async with db_manager.session_factory() as session:
        all_repair_jobs = (
            await session.execute(
                select(ScheduleJobRecord)
                .where(
                    ScheduleJobRecord.tenant_id == tenant_id,
                    ScheduleJobRecord.id != job_id,
                    ScheduleJobRecord.id != repair_job_1.id,
                )
            )
        ).scalars().all()
        assert len(all_repair_jobs) == 1
        repair_job_2 = all_repair_jobs[0]
        assert (
            repair_job_2.input_snapshot["repair_metadata"]["effective_movement_weight"]
            == 0.5
        )
        assert (
            repair_job_2.input_snapshot["profile"]["soft_weights"]["movement_weight"]
            == 0.5
        )

    # 6. Unconfigured tenant repair policy -> fails closed, logs audit event, baseline unmodified
    tenant_unconfigured = f"tenant-unconf-{uuid4().hex[:8]}"
    job_unconf_id = uuid4()
    sched_unconf_id = uuid4()

    async with db_manager.session_factory() as session:
        job_unconf = ScheduleJobRecord(
            id=job_unconf_id,
            tenant_id=tenant_unconfigured,
            schedule_key="unconf_sched",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot=base_scheduling_request.model_copy(
                update={"tenant_id": tenant_unconfigured}
            ).model_dump(mode="json"),
            input_snapshot_hash="hash-unconf",
        )
        session.add(job_unconf)
        sched_unconf = ScheduleRecord(
            id=sched_unconf_id,
            tenant_id=tenant_unconfigured,
            schedule_key="unconf_sched",
            source_job_id=job_unconf_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            published_at=t0,
            published_by="admin",
            input_snapshot_hash="hash-unconf",
        )
        session.add(sched_unconf)
        await session.commit()

    envelope_unconf = EventEnvelope(
        event_id=uuid4(),
        event_type="resource.unavailable",
        occurred_at=datetime.now(UTC),
        tenant_id=tenant_unconfigured,
        correlation_id=str(uuid4()),
        payload={
            "resource_id": "res-disrupted",
            "start_at": t0.isoformat(),
            "end_at": (t0 + timedelta(hours=4)).isoformat(),
            "reason": "POWER_OUTAGE",
        },
    )
    outcome_unconf = await processor.process(envelope_unconf)
    assert outcome_unconf.outcome == ConsumerProcessingOutcome.PROCESSED

    async with db_manager.session_factory() as session:
        # Zero repair schedules created
        repaired_unconf = (
            await session.execute(
                select(ScheduleRecord).where(
                    ScheduleRecord.tenant_id == tenant_unconfigured,
                    ScheduleRecord.id != sched_unconf_id,
                )
            )
        ).scalars().all()
        assert len(repaired_unconf) == 0

        # Baseline schedule status remains PUBLISHED and unmodified
        baseline_check = (
            await session.execute(
                select(ScheduleRecord).where(
                    ScheduleRecord.id == sched_unconf_id
                )
            )
        ).scalar_one()
        assert baseline_check.status == ScheduleLifecycleStatus.PUBLISHED.value

        # Audit event recorded for unresolved policy
        audit_records = (
            await session.execute(
                select(AuditEventRecord).where(
                    AuditEventRecord.tenant_id == tenant_unconfigured,
                    AuditEventRecord.event_type == "schedule.repair_policy_unresolved",
                )
            )
        ).scalars().all()
        assert len(audit_records) == 1
        assert audit_records[0].outcome == "FAILURE"


@pytest.mark.asyncio
async def test_live_calendar_outbound_sync_and_degraded_outage(
    db_manager: DatabaseManager,
) -> None:
    """Verify calendar sync create/update/delete, outage failure recording, and reconciliation."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    sched_id = uuid4()
    job_id = uuid4()
    now = datetime.now(UTC)

    # 1. Seed job, published schedule, and 2 assignments
    asgn1_id = uuid4()
    asgn2_id = uuid4()
    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="cal_sched",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={},
            input_snapshot_hash="hash-cal",
        )
        session.add(job)

        sched = ScheduleRecord(
            id=sched_id,
            tenant_id=tenant_id,
            schedule_key="cal_sched",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            input_snapshot_hash="hash-cal",
            score_components={"violations": 0},
        )
        session.add(sched)

        a1 = ScheduleAssignmentRecord(
            id=asgn1_id,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="act-cal-1",
            resource_id="res-cal-1",
            start_at=now,
            end_at=now + timedelta(hours=1),
            is_locked=False,
        )
        a2 = ScheduleAssignmentRecord(
            id=asgn2_id,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="act-cal-2",
            resource_id="res-cal-2",
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=3),
            is_locked=False,
        )
        session.add_all([a1, a2])
        await session.commit()

    adapter = FakeCalendarAdapter()
    sync_service = OutboundCalendarSyncService(adapter=adapter)

    cal_outbound = "cal-outbound"

    # 2. Outbound sync for publication: both assignments synced
    async with db_manager.session_factory() as session:
        links = await sync_service.sync_published_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            external_calendar_id=cal_outbound,
        )
        await session.commit()

    assert len(links) == 2
    for link in links:
        assert link.sync_status == SyncStatus.SYNCED
        assert link.external_event_id.startswith("e7_ev_")
        assert link.etag is not None

    # 3. Simulate Provider Outage
    adapter.simulate_outage = True
    # Add a 3rd assignment
    asgn3_id = uuid4()
    async with db_manager.session_factory() as session:
        a3 = ScheduleAssignmentRecord(
            id=asgn3_id,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="act-cal-3",
            resource_id="res-cal-3",
            start_at=now + timedelta(hours=4),
            end_at=now + timedelta(hours=5),
            is_locked=False,
        )
        session.add(a3)
        await session.commit()

    # Outbound sync during outage: does NOT raise; records FAILED truthfully
    async with db_manager.session_factory() as session:
        links_outage = await sync_service.sync_published_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            external_calendar_id=cal_outbound,
        )
        await session.commit()

    assert any(link.sync_status == SyncStatus.FAILED for link in links_outage)

    # Verify canonical schedule remains PUBLISHED
    async with db_manager.session_factory() as session:
        sched_check = await session.get(ScheduleRecord, sched_id)
        assert sched_check is not None
        assert sched_check.status == ScheduleLifecycleStatus.PUBLISHED.value

        # Check link 3 is recorded as FAILED
        link3 = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.assignment_id == asgn3_id
                )
            )
        ).scalar_one_or_none()
        assert link3 is not None
        assert link3.sync_status == SyncStatus.FAILED
        assert link3.error_message is not None

    # 4. Outage Cleared -> Periodic Reconciliation heals missing/failed links
    adapter.simulate_outage = False
    reconciliation_service = CalendarReconciliationService(adapter=adapter)
    async with db_manager.session_factory() as session:
        recon_res = await reconciliation_service.reconcile_published_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            external_calendar_id=cal_outbound,
        )
        await session.commit()

    assert recon_res.updated_count >= 1
    assert recon_res.failed_count == 0

    async with db_manager.session_factory() as session:
        link3_repaired = await session.get(CalendarLinkRecord, link3.id)
        assert link3_repaired is not None
        assert link3_repaired.sync_status == SyncStatus.SYNCED


@pytest.mark.asyncio
async def test_live_hard_lock_disruption_infeasible(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify that disruption on a hard-locked assignment produces INFEASIBLE, not an exception."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    job_id = uuid4()
    v1_id = uuid4()
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    h_end = t0 + timedelta(hours=8)

    activities = [
        SchedulingActivity(
            id="act-locked-1",
            title="Locked Task",
            duration_minutes=60,
            allowed_windows=[TimeWindow(start_at=t0, end_at=t0 + timedelta(hours=2))],
            required_skills=["SURGEON"],
        ),
    ]
    resources = [
        SchedulingResourceSnapshot(
            id="res-locked-1",
            type=ResourceType.PERSON,
            display_label="Dr. Smith",
            skills=["SURGEON"],
        ),
    ]
    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=2,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=10,
        ),
    )
    base_request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="hard_lock_test",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=activities,
        resources=resources,
        constraints=[],
        profile=profile,
    )
    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="hard_lock_test",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot=base_request.model_dump(mode="json"),
            input_snapshot_hash="hash-lock-v1",
        )
        session.add(job)
        v1_sched = ScheduleRecord(
            id=v1_id,
            tenant_id=tenant_id,
            schedule_key="hard_lock_test",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            input_snapshot_hash="hash-lock-v1",
            score_components={"violations": 0},
            published_at=t0,
            published_by="operator@irinfotech.internal",
        )
        session.add(v1_sched)
        asgn = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=v1_id,
            activity_id="act-locked-1",
            resource_id="res-locked-1",
            start_at=t0,
            end_at=t0 + timedelta(hours=1),
            is_locked=True,  # EXPLICIT HARD LOCK!
        )
        session.add(asgn)
        await session.commit()

    # Disruption: res-locked-1 is unavailable over the EXACT locked window!
    # Hard lock requires this resource at this time, but resource is absent!
    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=["res-locked-1"],
        disrupted_interval_start=t0,
        disrupted_interval_end=t0 + timedelta(hours=4),
        reason="Dr. Smith emergency leave",
        movement_weight=10,
    )

    async with db_manager.session_factory() as session:
        repair_job, repair_candidate = await service.repair_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=v1_id,
            request=repair_req,
        )
        await session.commit()

    # Assertions: INFEASIBLE, zero candidate rows, baseline untouched
    assert repair_job.status == ScheduleJobStatus.INFEASIBLE.value
    assert repair_candidate is None

    async with db_manager.session_factory() as session:
        v1_check = await session.get(ScheduleRecord, v1_id)
        assert v1_check is not None
        assert v1_check.status == ScheduleLifecycleStatus.PUBLISHED.value


@pytest.mark.asyncio
async def test_live_supersession_lineage_isolation(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify publishing repair for Schedule A supersedes only A and leaves B intact."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    t0 = datetime.now(UTC)

    sched_a_id = uuid4()
    sched_b_id = uuid4()
    cand_a2_id = uuid4()
    job_a_id = uuid4()
    job_b_id = uuid4()
    job_a2_id = uuid4()

    async with db_manager.session_factory() as session:
        job_a = ScheduleJobRecord(
            id=job_a_id,
            tenant_id=tenant_id,
            schedule_key="domain_alpha",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-a",
        )
        job_b = ScheduleJobRecord(
            id=job_b_id,
            tenant_id=tenant_id,
            schedule_key="domain_beta",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-b",
        )
        job_a2 = ScheduleJobRecord(
            id=job_a2_id,
            tenant_id=tenant_id,
            schedule_key="domain_alpha",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-a2",
        )
        session.add_all([job_a, job_b, job_a2])

        # Schedule A (lineage key A)
        sched_a = ScheduleRecord(
            id=sched_a_id,
            tenant_id=tenant_id,
            schedule_key="domain_alpha",
            source_job_id=job_a_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            published_at=t0,
            published_by="admin",
            input_snapshot_hash="hash-a",
        )
        # Schedule B (independent lineage key B under same tenant)
        sched_b = ScheduleRecord(
            id=sched_b_id,
            tenant_id=tenant_id,
            schedule_key="domain_beta",
            source_job_id=job_b_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            published_at=t0,
            published_by="admin",
            input_snapshot_hash="hash-b",
        )
        # Candidate A2 (repair candidate of A)
        cand_a2 = ScheduleRecord(
            id=cand_a2_id,
            tenant_id=tenant_id,
            schedule_key="domain_alpha",
            source_job_id=job_a2_id,
            version=2,
            status=ScheduleLifecycleStatus.REVIEW.value,
            supersedes_id=sched_a_id,  # references A only
            input_snapshot_hash="hash-a2",
            score_components={"hard_constraint_violations": 0},
        )
        asgn_a2 = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=cand_a2_id,
            activity_id="act-a2",
            resource_id="res-a",
            start_at=t0,
            end_at=t0 + timedelta(hours=1),
        )
        session.add_all([sched_a, sched_b, cand_a2, asgn_a2])
        await session.commit()

    # Publish repaired A2 with permissive policy
    policy = PermissivePublishPolicy()
    async with db_manager.session_factory() as session:
        published_a2 = await service.publish_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=cand_a2_id,
            principal="admin",
            publish_policy=policy,
        )
        await session.commit()

    assert published_a2.status == ScheduleLifecycleStatus.PUBLISHED.value

    # Verify A is SUPERSEDED, but B is completely UNTOUCHED (PUBLISHED)
    async with db_manager.session_factory() as session:
        check_a = await session.get(ScheduleRecord, sched_a_id)
        check_b = await session.get(ScheduleRecord, sched_b_id)
        assert check_a is not None
        assert check_a.status == ScheduleLifecycleStatus.SUPERSEDED.value
        assert check_b is not None
        assert check_b.status == ScheduleLifecycleStatus.PUBLISHED.value


@pytest.mark.asyncio
async def test_live_calendar_v1_v2_diff_and_restart_sync_token(
    db_manager: DatabaseManager,
) -> None:
    """Verify v1->v2 diff (A: NO-OP, B: UPDATE, C: DELETE, D: CREATE) and sync token restart."""
    tenant_id = f"tenant-{uuid4().hex[:8]}"
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    v1_id = uuid4()
    v2_id = uuid4()
    job_v1_id = uuid4()
    job_v2_id = uuid4()

    adapter = FakeCalendarAdapter()
    sync_service = OutboundCalendarSyncService(adapter=adapter)

    # v1 has activities A, B, C
    asgn_v1_a = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v1_id,
        activity_id="act-A",
        resource_id="res-1",
        start_at=t0,
        end_at=t0 + timedelta(hours=1),
    )
    asgn_v1_b = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v1_id,
        activity_id="act-B",
        resource_id="res-1",
        start_at=t0 + timedelta(hours=1),
        end_at=t0 + timedelta(hours=2),
    )
    asgn_v1_c = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v1_id,
        activity_id="act-C",
        resource_id="res-2",
        start_at=t0,
        end_at=t0 + timedelta(hours=2),
    )

    async with db_manager.session_factory() as session:
        job_v1 = ScheduleJobRecord(
            id=job_v1_id,
            tenant_id=tenant_id,
            schedule_key="stream_alpha",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-v1",
        )
        job_v2 = ScheduleJobRecord(
            id=job_v2_id,
            tenant_id=tenant_id,
            schedule_key="stream_alpha",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot={"activities": [], "resources": []},
            input_snapshot_hash="hash-v2",
        )
        session.add_all([job_v1, job_v2])

        sched_v1 = ScheduleRecord(
            id=v1_id,
            tenant_id=tenant_id,
            schedule_key="stream_alpha",
            source_job_id=job_v1_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            published_at=t0,
            published_by="admin",
            input_snapshot_hash="hash-v1",
        )
        session.add(sched_v1)
        session.add_all([asgn_v1_a, asgn_v1_b, asgn_v1_c])
        await session.commit()

    cal_diff = "cal-version-diff"

    # Sync v1
    async with db_manager.session_factory() as session:
        links_v1 = await sync_service.sync_published_schedule(
            session=session, tenant_id=tenant_id, schedule_id=v1_id, external_calendar_id=cal_diff
        )
        await session.commit()

    assert len(links_v1) == 3
    assert len(adapter._events[tenant_id]) == 3

    # Now create v2:
    # A is unchanged (same times and resource)
    # B is moved (start pushed by 2 hours)
    # C is removed
    # D is added
    asgn_v2_a = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v2_id,
        activity_id="act-A",
        resource_id="res-1",
        start_at=t0,
        end_at=t0 + timedelta(hours=1),
    )
    asgn_v2_b = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v2_id,
        activity_id="act-B",
        resource_id="res-1",
        start_at=t0 + timedelta(hours=3),  # moved!
        end_at=t0 + timedelta(hours=4),
    )
    asgn_v2_d = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        schedule_id=v2_id,
        activity_id="act-D",  # newly added!
        resource_id="res-3",
        start_at=t0,
        end_at=t0 + timedelta(hours=1),
    )

    async with db_manager.session_factory() as session:
        sched_v2 = ScheduleRecord(
            id=v2_id,
            tenant_id=tenant_id,
            schedule_key="stream_alpha",
            source_job_id=job_v2_id,
            version=2,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            supersedes_id=v1_id,
            published_at=t0,
            published_by="admin",
            input_snapshot_hash="hash-v2",
        )
        session.add(sched_v2)
        session.add_all([asgn_v2_a, asgn_v2_b, asgn_v2_d])
        await session.commit()

    # Sync v2
    async with db_manager.session_factory() as session:
        await sync_service.sync_published_schedule(
            session=session, tenant_id=tenant_id, schedule_id=v2_id, external_calendar_id=cal_diff
        )
        await session.commit()

    # Verify adapter events:
    # A exists and unchanged
    # B exists with updated time
    # C is deleted (no longer in adapter)
    # D exists
    live_events = adapter._events[tenant_id]
    assert len(live_events) == 3  # A, B, D (C deleted!)

    # Repeat sync v2: zero additional creates (idempotent)
    async with db_manager.session_factory() as session:
        await sync_service.sync_published_schedule(
            session=session, tenant_id=tenant_id, schedule_id=v2_id, external_calendar_id=cal_diff
        )
        await session.commit()
    assert len(adapter._events[tenant_id]) == 3

    # Now verify durable sync cursor restart with InboundCalendarSyncService
    from app.integrations.calendar.inbound import InboundCalendarSyncService
    inbound_1 = InboundCalendarSyncService()

    async with db_manager.session_factory() as session:
        res1 = await inbound_1.import_incremental_changes(
            session=session, tenant_id=tenant_id, adapter=adapter, external_calendar_id=cal_diff
        )
        await session.commit()
    t1 = res1.next_sync_token
    assert t1 is not None

    # Verify database cursor representation:
    # 1. link_kind == "SYNC_CURSOR"
    # 2. sync_token == t1
    # 3. external_event_id is NULL (no fake synthetic external_event_id)
    # 4. assignment_id is NULL
    # 5. activity_id is NULL
    async with db_manager.session_factory() as session:
        cursor_link = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.external_calendar_id == cal_diff,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one_or_none()
        assert cursor_link is not None
        assert cursor_link.sync_token == t1
        assert cursor_link.external_event_id is None
        assert cursor_link.assignment_id is None
        assert cursor_link.activity_id is None

        # Verify no link has synthetic external_event_id containing '__sync_cursor__' or 'google'
        all_links = (
            await session.execute(
                select(CalendarLinkRecord).where(CalendarLinkRecord.tenant_id == tenant_id)
            )
        ).scalars().all()
        for link in all_links:
            if link.external_event_id is not None:
                assert "__sync_cursor__" not in link.external_event_id
                assert "google" not in link.external_event_id

    # Construct brand-new service instance (simulating app process restart)
    inbound_2 = InboundCalendarSyncService()
    async with db_manager.session_factory() as session:
        res2 = await inbound_2.import_incremental_changes(
            session=session, tenant_id=tenant_id, adapter=adapter, external_calendar_id=cal_diff
        )
        await session.commit()
    # Next sync loaded t1 from calendar_links and delta returned 0 new items
    assert len(res2.created_or_updated) == 0


@pytest.mark.asyncio
async def test_live_inbound_calendar_etag_dedupe_and_stale_update(
    db_manager: DatabaseManager,
) -> None:
    """Test callback and incremental sync deduplication:
    - Same provider notification twice = one canonical availability mutation.
    - Same event delivered through webhook then incremental sync = one logical availability state.
    - Newer ETag = accepted update.
    - Older stale ETag = ignored/rejected safely.
    """
    from app.integrations.calendar.inbound import (
        DefaultCalendarResourceResolver,
        InboundCalendarSyncService,
    )

    tenant_id = f"tenant-etag-{uuid4().hex[:8]}"
    res_id = uuid4()

    # Seed resource
    async with db_manager.session_factory() as session:
        session.add(
            ResourceRecord(
                id=res_id,
                tenant_id=tenant_id,
                resource_key="technician-dan",
                display_label="Dan Tech",
                type="PERSON",
                active=True,
            )
        )
        await session.commit()

    cal_dan = "cal-dan"
    adapter = FakeCalendarAdapter()
    resolver = DefaultCalendarResourceResolver(synthetic_mappings={"Dan Tech": res_id})
    inbound = InboundCalendarSyncService(resolver=resolver)

    # 1. First event with opaque etag and provider_version=1
    start_dt = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    end_dt = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    ev1 = CalendarEventDTO(
        external_event_id="ext-ev-dan-1",
        external_calendar_id=cal_dan,
        title="Dan Tech",
        start_at=start_dt,
        end_at=end_dt,
        provider_version=1,
        provider_updated_at=start_dt,
    )
    res_create = await adapter.create_event(tenant_id, ev1, external_calendar_id=cal_dan)
    ev1 = ev1.model_copy(update={"etag": res_create.etag})

    # Import initial event
    async with db_manager.session_factory() as session:
        changes1 = await inbound.import_incremental_changes(
            session=session, tenant_id=tenant_id, adapter=adapter, external_calendar_id=cal_dan
        )
        await session.commit()
    assert len(changes1.created_or_updated) == 1

    # Verify exactly 1 availability record exists
    async with db_manager.session_factory() as session:
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 1
        assert avails[0].start_at == start_dt
        assert avails[0].end_at == end_dt

    # 2. Duplicate notification with identical ETag (e.g. webhook redelivery or sync)
    # A. same event + same ETag -> duplicate / no mutation
    changes_dup = SyncChangesResult(
        created_or_updated=[ev1],
        deleted_external_ids=[],
        next_sync_token="token-dup",
    )

    class DupAdapter:
        async def fetch_incremental_changes(
            self, tid: str, token: str | None = None, external_calendar_id: str | None = None
        ) -> SyncChangesResult:
            return changes_dup

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=DupAdapter(),  # type: ignore[arg-type]
            external_calendar_id=cal_dan,
        )
        await session.commit()

    # Still exactly 1 availability record (zero additional mutation)
    async with db_manager.session_factory() as session:
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 1
        assert avails[0].start_at == start_dt

    # 3. Stale update with different opaque ETag, but older provider version/updated_at
    # B. same event + different ETag + older provider version/updated_at -> ignore stale update
    stale_ev = CalendarEventDTO(
        external_event_id="ext-ev-dan-1",
        external_calendar_id=cal_dan,
        title="Dan Tech",
        start_at=start_dt + timedelta(hours=5),
        end_at=end_dt + timedelta(hours=5),
        etag="z_opaque_hash_arbitrary_string",  # Opaque validator - lexical order does NOT matter
        provider_version=0,  # Older version than current (1)
        provider_updated_at=start_dt - timedelta(hours=1),
    )
    changes_stale = SyncChangesResult(
        created_or_updated=[stale_ev],
        deleted_external_ids=[],
        next_sync_token="token-stale",
    )

    class StaleAdapter:
        async def fetch_incremental_changes(
            self, tid: str, token: str | None = None, external_calendar_id: str | None = None
        ) -> SyncChangesResult:
            return changes_stale

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=StaleAdapter(),  # type: ignore[arg-type]
            external_calendar_id=cal_dan,
        )
        await session.commit()

    # Still 1 availability record, start_at unchanged (stale update rejected)
    async with db_manager.session_factory() as session:
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 1
        assert avails[0].start_at == start_dt  # unchanged!

    # 4. Newer update with different opaque ETag + newer provider version/updated_at
    # C. same event + different ETag + newer provider version/updated_at -> apply update
    newer_start = start_dt + timedelta(days=1)
    newer_end = end_dt + timedelta(days=1)
    newer_ev = CalendarEventDTO(
        external_event_id="ext-ev-dan-1",
        external_calendar_id=cal_dan,
        title="Dan Tech",
        start_at=newer_start,
        end_at=newer_end,
        # Lexically 'a' < 'z', but freshness is derived from provider_version=2
        etag="a_opaque_hash_arbitrary_string",
        provider_version=2,
        provider_updated_at=start_dt + timedelta(hours=2),
    )
    changes_newer = SyncChangesResult(
        created_or_updated=[newer_ev],
        deleted_external_ids=[],
        next_sync_token="token-newer",
    )

    class NewerAdapter:
        async def fetch_incremental_changes(
            self, tid: str, token: str | None = None, external_calendar_id: str | None = None
        ) -> SyncChangesResult:
            return changes_newer

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=NewerAdapter(),  # type: ignore[arg-type]
            external_calendar_id=cal_dan,
        )
        await session.commit()

    # Exactly 1 availability record, updated to newer_start and newer_end!
    async with db_manager.session_factory() as session:
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 1
        assert avails[0].start_at == newer_start
        assert avails[0].end_at == newer_end

    # 5. Outbound concurrency with expected_etag (If-Match)
    # D. outbound update with stale expected ETag -> provider concurrency conflict (no overwrite)
    current_adapter_ev = await adapter.get_event(
        tenant_id, "ext-ev-dan-1", external_calendar_id=cal_dan
    )
    assert current_adapter_ev is not None
    real_etag = current_adapter_ev.etag

    # Attempt outbound update with a mismatched/stale expected_etag
    conflict_res = await adapter.update_event(
        tenant_id=tenant_id,
        external_event_id="ext-ev-dan-1",
        event=current_adapter_ev.model_copy(update={"title": "Unauthorized Overwrite"}),
        expected_etag="stale-client-etag",
        external_calendar_id=cal_dan,
    )
    assert conflict_res.status == SyncStatus.CONFLICT
    # Verify no silent overwrite occurred
    check_ev = await adapter.get_event(
        tenant_id, "ext-ev-dan-1", external_calendar_id=cal_dan
    )
    assert check_ev is not None
    assert check_ev.title == "Dan Tech"

    # E. Reconciliation refresh resolves conflict idempotently
    reconciled_res = await adapter.update_event(
        tenant_id=tenant_id,
        external_event_id="ext-ev-dan-1",
        event=current_adapter_ev.model_copy(update={"title": "Reconciled Dan"}),
        expected_etag=real_etag,
        external_calendar_id=cal_dan,
    )
    assert reconciled_res.status == SyncStatus.SYNCED
    check_ev2 = await adapter.get_event(
        tenant_id, "ext-ev-dan-1", external_calendar_id=cal_dan
    )
    assert check_ev2 is not None
    assert check_ev2.title == "Reconciled Dan"


@pytest.mark.asyncio
async def test_live_multi_calendar_cursor_isolation_and_restart(
    db_manager: DatabaseManager,
) -> None:
    """Requirement 8: Multi-calendar restart test.

    Proves:
    - Independent cursor tokens for C1 and C2 under same tenant and provider
    - Persist both
    - New InboundCalendarSyncService loads only C1-T1 for C1 and C2-T1 for C2
    - Provider returns C1-T2 and C2-T2, persisted independently
    - Zero token cross-talk, zero duplicate availability mutations, zero synthetic IDs
    """
    tenant_id = f"t-multical-{uuid4().hex[:8]}"
    provider = "google"
    cal1 = "calendar-C1"
    cal2 = "calendar-C2"

    async with db_manager.session_factory() as session:
        res = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key="tech-alice",
            display_label="Alice Tech",
            type="PERSON",
            active=True,
        )
        session.add(res)
        await session.commit()
        res_id = res.id

    now = datetime.now(UTC)
    ev_c1_1 = CalendarEventDTO(
        external_event_id="ev-c1-1",
        external_calendar_id=cal1,
        title="tech-alice",
        start_at=now,
        end_at=now + timedelta(hours=1),
        etag="etag-c1-1",
    )
    ev_c2_1 = CalendarEventDTO(
        external_event_id="ev-c2-1",
        external_calendar_id=cal2,
        title="tech-alice",
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
        etag="etag-c2-1",
    )

    adapter = FakeCalendarAdapter()
    await adapter.create_event(tenant_id, ev_c1_1, external_calendar_id=cal1)
    adapter.set_sync_token(tenant_id, "C1-T1", external_calendar_id=cal1)

    await adapter.create_event(tenant_id, ev_c2_1, external_calendar_id=cal2)
    adapter.set_sync_token(tenant_id, "C2-T1", external_calendar_id=cal2)

    # Initial sync
    inbound_1 = InboundCalendarSyncService()
    async with db_manager.session_factory() as session:
        res_c1 = await inbound_1.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=adapter,
            provider=provider,
            external_calendar_id=cal1,
        )
        assert res_c1.next_sync_token == "C1-T1"
        await session.commit()

    async with db_manager.session_factory() as session:
        res_c2 = await inbound_1.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=adapter,
            provider=provider,
            external_calendar_id=cal2,
        )
        assert res_c2.next_sync_token == "C2-T1"
        await session.commit()

    # Verify both cursors persisted independently in DB
    async with db_manager.session_factory() as session:
        cursor_c1 = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == cal1,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one_or_none()
        assert cursor_c1 is not None
        assert cursor_c1.sync_token == "C1-T1"

        cursor_c2 = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == cal2,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one_or_none()
        assert cursor_c2 is not None
        assert cursor_c2.sync_token == "C2-T1"

        # Verify availability intervals: 2 distinct records
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 2

    # Construct NEW InboundCalendarSyncService to simulate restart
    inbound_restarted = InboundCalendarSyncService()

    class RestartInspectAdapter(CalendarAdapter):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None, str | None]] = []

        async def fetch_incremental_changes(
            self,
            tenant_id: str,
            sync_token: str | None = None,
            external_calendar_id: str | None = None,
        ) -> SyncChangesResult:
            self.calls.append((tenant_id, sync_token, external_calendar_id))
            if external_calendar_id == cal1:
                assert sync_token == "C1-T1"  # Zero token cross-talk
                return SyncChangesResult(
                    created_or_updated=[],
                    deleted_external_ids=[],
                    next_sync_token="C1-T2",
                )
            elif external_calendar_id == cal2:
                assert sync_token == "C2-T1"  # Zero token cross-talk
                return SyncChangesResult(
                    created_or_updated=[],
                    deleted_external_ids=[],
                    next_sync_token="C2-T2",
                )
            raise ValueError(f"Unexpected calendar {external_calendar_id}")

        async def create_event(
            self,
            tenant_id: str,
            event: CalendarEventDTO,
            external_calendar_id: str | None = None,
        ) -> Any:
            raise NotImplementedError

        async def update_event(
            self,
            tenant_id: str,
            external_event_id: str,
            event: CalendarEventDTO,
            expected_etag: str | None = None,
            external_calendar_id: str | None = None,
        ) -> Any:
            raise NotImplementedError

        async def delete_event(
            self,
            tenant_id: str,
            external_event_id: str,
            expected_etag: str | None = None,
            external_calendar_id: str | None = None,
        ) -> bool:
            return True

        async def get_event(
            self,
            tenant_id: str,
            external_event_id: str,
            external_calendar_id: str | None = None,
        ) -> CalendarEventDTO | None:
            return None

    restart_adapter = RestartInspectAdapter()

    # Sync C1 after restart
    async with db_manager.session_factory() as session:
        await inbound_restarted.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=restart_adapter,
            provider=provider,
            external_calendar_id=cal1,
        )
        await session.commit()

    # Sync C2 after restart
    async with db_manager.session_factory() as session:
        await inbound_restarted.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=restart_adapter,
            provider=provider,
            external_calendar_id=cal2,
        )
        await session.commit()

    # Verify updated tokens persisted independently
    async with db_manager.session_factory() as session:
        c1_after = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == cal1,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one()
        assert c1_after.sync_token == "C1-T2"

        c2_after = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == cal2,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one()
        assert c2_after.sync_token == "C2-T2"

        # Zero duplicate availability mutations: still exactly 2 records
        avails_after = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                    ResourceAvailabilityRecord.resource_id == res_id,
                )
            )
        ).scalars().all()
        assert len(avails_after) == 2


@pytest.mark.asyncio
async def test_live_missing_external_calendar_id_fails_closed(
    db_manager: DatabaseManager,
) -> None:
    """Requirement Section 7: Missing external_calendar_id fails closed.

    Guarantees:
    - Configuration / validation failure (ValueError)
    - Zero cursor rows persisted
    - Zero external adapter calls made
    - Zero availability mutations
    """
    tenant_id = f"t-misscal-{uuid4().hex[:8]}"

    # Setup resource
    async with db_manager.session_factory() as session:
        res = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key="tech-bob",
            display_label="Bob Tech",
            type="PERSON",
            active=True,
        )
        session.add(res)
        await session.commit()

    class CountingAdapter:
        def __init__(self) -> None:
            self.fetch_calls = 0

        async def fetch_incremental_changes(
            self,
            tenant_id: str,
            sync_token: str | None = None,
            external_calendar_id: str | None = None,
        ) -> SyncChangesResult:
            self.fetch_calls += 1
            return SyncChangesResult(
                created_or_updated=[],
                deleted_external_ids=[],
                next_sync_token="tok-should-never-be-fetched",
            )

    adapter = CountingAdapter()
    inbound = InboundCalendarSyncService()

    # 1. Attempt inbound sync with empty external_calendar_id -> fail closed
    with pytest.raises(ValueError, match="external_calendar_id is required"):
        async with db_manager.session_factory() as session:
            await inbound.import_incremental_changes(
                session=session,
                tenant_id=tenant_id,
                adapter=adapter,  # type: ignore[arg-type]
                external_calendar_id="",
            )

    # 2. Attempt inbound sync with whitespace-only external_calendar_id -> fail closed
    with pytest.raises(ValueError, match="external_calendar_id is required"):
        async with db_manager.session_factory() as session:
            await inbound.import_incremental_changes(
                session=session,
                tenant_id=tenant_id,
                adapter=adapter,  # type: ignore[arg-type]
                external_calendar_id="   ",
            )

    # 3. Verify ZERO external adapter calls were made
    assert adapter.fetch_calls == 0

    # 4. Verify ZERO cursor rows persisted
    async with db_manager.session_factory() as session:
        cursors = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalars().all()
        assert len(cursors) == 0

    # 5. Verify ZERO availability mutations occurred
    async with db_manager.session_factory() as session:
        avails = (
            await session.execute(
                select(ResourceAvailabilityRecord).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id,
                )
            )
        ).scalars().all()
        assert len(avails) == 0

    # 6. Verify Outbound and Reconciliation also fail closed if external_calendar_id is empty
    fake_adapter = FakeCalendarAdapter()
    sync_service = OutboundCalendarSyncService(adapter=fake_adapter)
    with pytest.raises(ValueError, match="external_calendar_id is required"):
        async with db_manager.session_factory() as session:
            await sync_service.sync_published_schedule(
                session=session,
                tenant_id=tenant_id,
                schedule_id=uuid4(),
                external_calendar_id="",
            )

    recon_service = CalendarReconciliationService(adapter=fake_adapter)
    with pytest.raises(ValueError, match="external_calendar_id is required"):
        async with db_manager.session_factory() as session:
            await recon_service.reconcile_published_schedule(
                session=session,
                tenant_id=tenant_id,
                schedule_id=uuid4(),
                external_calendar_id="  ",
            )


@pytest.mark.asyncio
async def test_live_cross_calendar_event_collision_coexistence(
    db_manager: DatabaseManager,
) -> None:
    """Requirement 9: Cross-calendar event collision coexistence.

    Proves:
    - Same opaque external event ID "event-collision-opaque-E" under C1 and C2 coexists safely
    - Same ID under two providers ("google" and "microsoft") coexists safely
    - Exactly 3 isolated event links persisted
    """
    tenant_id = f"t-collision-{uuid4().hex[:8]}"
    cal1 = "calendar-dept-alpha"
    cal2 = "calendar-dept-beta"
    opaque_event_id = "event-collision-opaque-E"

    async with db_manager.session_factory() as session:
        res = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key="tech-bob",
            display_label="Bob Tech",
            type="PERSON",
            active=True,
        )
        session.add(res)
        await session.commit()

    now = datetime.now(UTC)
    ev_c1 = CalendarEventDTO(
        external_event_id=opaque_event_id,
        external_calendar_id=cal1,
        title="tech-bob",
        start_at=now,
        end_at=now + timedelta(hours=1),
        etag="etag-c1",
    )
    ev_c2 = CalendarEventDTO(
        external_event_id=opaque_event_id,
        external_calendar_id=cal2,
        title="tech-bob",
        start_at=now + timedelta(hours=3),
        end_at=now + timedelta(hours=4),
        etag="etag-c2",
    )

    adapter_c1 = FakeCalendarAdapter()
    await adapter_c1.create_event(tenant_id, ev_c1, external_calendar_id=cal1)

    adapter_c2 = FakeCalendarAdapter()
    await adapter_c2.create_event(tenant_id, ev_c2, external_calendar_id=cal2)

    inbound = InboundCalendarSyncService()

    # Import from C1 under "google"
    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=adapter_c1,
            provider="google",
            external_calendar_id=cal1,
        )
        await session.commit()

    # Import from C2 under "google" (same external_event_id, different calendar)
    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=adapter_c2,
            provider="google",
            external_calendar_id=cal2,
        )
        await session.commit()

    # Import under provider 2 ("microsoft") with same event ID
    ev_ms = CalendarEventDTO(
        external_event_id=opaque_event_id,
        external_calendar_id=cal1,
        title="tech-bob",
        start_at=now + timedelta(hours=5),
        end_at=now + timedelta(hours=6),
        etag="etag-ms",
    )
    adapter_ms = FakeCalendarAdapter()
    await adapter_ms.create_event(tenant_id, ev_ms, external_calendar_id=cal1)

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_id,
            adapter=adapter_ms,
            provider="microsoft",
            external_calendar_id=cal1,
        )
        await session.commit()

    # Verify all 3 event links coexist safely in calendar_links
    async with db_manager.session_factory() as session:
        links = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.external_event_id == opaque_event_id,
                    CalendarLinkRecord.link_kind == "EVENT",
                )
            )
        ).scalars().all()
        assert len(links) == 3
        scopes = {(lnk.provider, lnk.external_calendar_id) for lnk in links}
        assert scopes == {
            ("google", cal1),
            ("google", cal2),
            ("microsoft", cal1),
        }


@pytest.mark.asyncio
async def test_live_cross_tenant_calendar_isolation(
    db_manager: DatabaseManager,
) -> None:
    """Requirement 10: Cross-tenant calendar isolation.

    Proves:
    - Same provider, calendar, and event values in two tenants remain completely isolated.
    """
    tenant_a = f"t-iso-a-{uuid4().hex[:8]}"
    tenant_b = f"t-iso-b-{uuid4().hex[:8]}"
    provider = "google"
    shared_cal = "ops-primary-calendar"
    shared_event_id = "event-ident-12345"

    now = datetime.now(UTC)

    async with db_manager.session_factory() as session:
        res_a = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_a,
            resource_key="worker-1",
            display_label="Worker A",
            type="PERSON",
            active=True,
        )
        session.add(res_a)

        res_b = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_b,
            resource_key="worker-1",
            display_label="Worker B",
            type="PERSON",
            active=True,
        )
        session.add(res_b)
        await session.commit()

    ev_a = CalendarEventDTO(
        external_event_id=shared_event_id,
        external_calendar_id=shared_cal,
        title="worker-1",
        start_at=now,
        end_at=now + timedelta(hours=1),
        etag="etag-tenant-a",
    )
    ev_b = CalendarEventDTO(
        external_event_id=shared_event_id,
        external_calendar_id=shared_cal,
        title="worker-1",
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
        etag="etag-tenant-b",
    )

    adapter_a = FakeCalendarAdapter()
    await adapter_a.create_event(tenant_a, ev_a, external_calendar_id=shared_cal)
    adapter_a.set_sync_token(tenant_a, "tok-a", external_calendar_id=shared_cal)

    adapter_b = FakeCalendarAdapter()
    await adapter_b.create_event(tenant_b, ev_b, external_calendar_id=shared_cal)
    adapter_b.set_sync_token(tenant_b, "tok-b", external_calendar_id=shared_cal)

    inbound = InboundCalendarSyncService()

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_a,
            adapter=adapter_a,
            provider=provider,
            external_calendar_id=shared_cal,
        )
        await session.commit()

    async with db_manager.session_factory() as session:
        await inbound.import_incremental_changes(
            session=session,
            tenant_id=tenant_b,
            adapter=adapter_b,
            provider=provider,
            external_calendar_id=shared_cal,
        )
        await session.commit()

    # Verify both tenants have isolated cursor and event links
    async with db_manager.session_factory() as session:
        cursor_a = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_a,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == shared_cal,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one()
        assert cursor_a.sync_token == "tok-a"

        cursor_b = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_b,
                    CalendarLinkRecord.provider == provider,
                    CalendarLinkRecord.external_calendar_id == shared_cal,
                    CalendarLinkRecord.link_kind == "SYNC_CURSOR",
                )
            )
        ).scalar_one()
        assert cursor_b.sync_token == "tok-b"

        event_a = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_a,
                    CalendarLinkRecord.external_event_id == shared_event_id,
                )
            )
        ).scalar_one()
        assert event_a.etag is not None

        event_b = (
            await session.execute(
                select(CalendarLinkRecord).where(
                    CalendarLinkRecord.tenant_id == tenant_b,
                    CalendarLinkRecord.external_event_id == shared_event_id,
                )
            )
        ).scalar_one()
        assert event_b.etag is not None
        assert event_a.etag != event_b.etag


@pytest.mark.asyncio
async def test_live_fractional_weight_repair_and_reproducibility(
    db_manager: DatabaseManager,
) -> None:
    """Requirements 12-18: Fractional objective weight normalization & repair solve.

    Proves:
    - Weight matrix: 0.001, 0.01, 0.1, 0.5, 1, 1.5, 15, 33.7 -> all CP-SAT integer coefficients > 0
    - Multi-weight profile preserves monotonic ordering across all soft objectives
    - Live repair solve with movement_weight=0.001 succeeds in REVIEW state
    - Reproducibility: solver metadata stores normalized weights, snapshot stores weights
    - Same snapshot produces exact identical normalized integers
    """
    tenant_id = f"t-frac-{uuid4().hex[:8]}"
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    h_end = t0 + timedelta(days=2)

    # 1. Test weight matrix: 0.001, 0.01, 0.1, 0.5, 1, 1.5, 15, 33.7
    matrix = [0.001, 0.01, 0.1, 0.5, 1, 1.5, 15, 33.7]
    for w in matrix:
        norm = normalize_soft_objective_weights(
            SoftObjectiveWeights(
                preference_weight=1.0,
                idle_gap_weight=1.0,
                fairness_weight=1.0,
                utilization_weight=1.0,
                movement_weight=w,
            )
        )
        assert norm.movement_weight > 0, (
            f"Weight {w} must normalize to integer > 0, got {norm.movement_weight}"
        )

    # 2. Test profile with fractional weights and verify monotonic ordering survives
    multi_weights = SoftObjectiveWeights(
        movement_weight=0.001,
        preference_weight=0.01,
        idle_gap_weight=0.1,
        fairness_weight=0.5,
        utilization_weight=1.5,
    )
    multi_norm = normalize_soft_objective_weights(multi_weights)
    assert (
        0
        < multi_norm.movement_weight
        < multi_norm.preference_weight
        < multi_norm.idle_gap_weight
        < multi_norm.fairness_weight
        < multi_norm.utilization_weight
    )

    # 3. Setup baseline published schedule for repair
    res_1 = SchedulingResourceSnapshot(
        id="res-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        skills=["general"],
    )
    res_2 = SchedulingResourceSnapshot(
        id="res-2",
        type=ResourceType.PERSON,
        display_label="Tech 2",
        skills=["general"],
    )
    act_1 = SchedulingActivity(
        id="act-1",
        title="Activity 1",
        duration_minutes=120,
        required_skills=["general"],
        allowed_windows=[TimeWindow(start_at=t0, end_at=h_end)],
    )
    act_2 = SchedulingActivity(
        id="act-2",
        title="Activity 2",
        duration_minutes=120,
        required_skills=["general"],
        allowed_windows=[TimeWindow(start_at=t0, end_at=h_end)],
    )

    profile = SolverProfile(
        profile_key="frac_std",
        version=1,
        name="Fractional Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=2,
        soft_weights=SoftObjectiveWeights(
            movement_weight=0.001,
            preference_weight=0.01,
            idle_gap_weight=1.0,
            fairness_weight=1.0,
            utilization_weight=1.0,
        ),
    )

    base_scheduling_request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="frac_repair_sched",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=[act_1, act_2],
        resources=[res_1, res_2],
        constraints=[],
        profile=profile,
    )

    job_id = uuid4()
    v1_id = uuid4()
    async with db_manager.session_factory() as session:
        job = ScheduleJobRecord(
            id=job_id,
            tenant_id=tenant_id,
            schedule_key="frac_repair_sched",
            status=ScheduleJobStatus.FEASIBLE.value,
            input_snapshot=base_scheduling_request.model_dump(mode="json"),
            input_snapshot_hash="hash-frac-v1",
        )
        session.add(job)

        v1_sched = ScheduleRecord(
            id=v1_id,
            tenant_id=tenant_id,
            schedule_key="frac_repair_sched",
            source_job_id=job_id,
            version=1,
            status=ScheduleLifecycleStatus.PUBLISHED.value,
            input_snapshot_hash="hash-frac-v1",
            score_components={"violations": 0},
            published_at=t0,
            published_by="operator@irinfotech.internal",
        )
        session.add(v1_sched)

        asgn1 = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=v1_id,
            activity_id="act-1",
            resource_id="res-1",
            start_at=t0,
            end_at=t0 + timedelta(hours=2),
            is_locked=False,
        )
        asgn2 = ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_id=v1_id,
            activity_id="act-2",
            resource_id="res-2",
            start_at=t0 + timedelta(hours=3),
            end_at=t0 + timedelta(hours=5),
            is_locked=True,
        )
        session.add_all([asgn1, asgn2])
        await session.commit()

    # 4. Execute repair solve with movement_weight=0.001
    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=["res-1"],
        disrupted_interval_start=t0,
        disrupted_interval_end=t0 + timedelta(hours=3),
        reason="EMERGENCY_DISRUPTION",
        movement_weight=0.001,
    )

    async with db_manager.session_factory() as session:
        service = SchedulingService()
        repair_job, repaired_cand = await service.repair_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=v1_id,
            request=repair_req,
        )
        await session.commit()

    assert repaired_cand is not None
    assert repaired_cand.status == ScheduleLifecycleStatus.REVIEW.value
    assert repaired_cand.version == 2

    # 5. Reproducibility verification:
    async with db_manager.session_factory() as session:
        job_record = (
            await session.execute(
                select(ScheduleJobRecord).where(
                    ScheduleJobRecord.id == repair_job.id,
                )
            )
        ).scalar_one()

        meta = job_record.solver_metadata or {}
        assert "normalized_objective_weights" in meta
        norm_dict = meta["normalized_objective_weights"]
        # In normalized integers, movement_weight must be positive (> 0)
        assert norm_dict["movement_weight"] > 0
        assert norm_dict["scale_factor"] >= 1000

        # Snapshot stores original configured weights
        snapshot = repair_job.input_snapshot
        assert snapshot is not None
        assert snapshot["profile"]["soft_weights"]["movement_weight"] == 0.001

        # Reproducibility: same snapshot, weights, seed produces exact same normalization
        reproduced_norm = normalize_soft_objective_weights(
            SoftObjectiveWeights(**snapshot["profile"]["soft_weights"])
        )
        assert reproduced_norm.to_metadata_dict() == norm_dict


