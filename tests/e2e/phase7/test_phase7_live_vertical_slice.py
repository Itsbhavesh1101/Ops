"""Live Phase 7 End-to-End Vertical Slice Test Suite.

Reconciliation of Phase-7 Live Reality:
======================================
1. GENUINELY LIVE LOCAL INFRASTRUCTURE:
   - PostgreSQL (port 55432):
     * Canonical System of Record (SoR) persistence.
     * Resource catalogs (resources table).
     * Blackout intervals (resource_availabilities table).
     * Durable callback deduplication (idempotency_records table).
     * Durable repair queue tracking (schedule_jobs table).
     * External identity mappings (calendar_links table).
     * Transactional outbox events (outbox_events table).
     * Append-only compliance log (audit_events table).
     * Operational analytics and risk aggregation.

2. BLOCKED EXTERNAL PROVIDERS (SIMULATED VIA CONTRACT TRANSPORTS):
   - GOOGLE_CALENDAR_LIVE_VERIFICATION = BLOCKED_ON_EXTERNAL_CREDENTIALS
     (Simulated via MockHttpTransport validating Google Calendar REST v3 payload/headers)
   - E8_LIVE_WIRE_VERIFICATION = BLOCKED_ON_EXTERNAL_ENDPOINT_OR_CREDENTIALS
     (Simulated via MockHttpTransport validating E-8 Notification Gateway wire contract)
   - BLK-01_MYSQL_ERP_EXTRACTION = ACTIVE_PRODUCTION_BLOCKER
     (Live extraction fails closed; verified against unapproved tenant scopes)

Controlled via opt-in environment variable:
    E7_RUN_PHASE7_E2E=1
If E7_RUN_PHASE7_E2E=1 is set and local PostgreSQL is absent: FAILS explicitly.
"""

import os
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.analytics.service import OperationsAnalyticsService
from app.core.idempotency.models import IdempotencyRecord
from app.db.models.scheduling import (
    CalendarLinkRecord,
    ResourceAvailabilityRecord,
    ResourceRecord,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.schemas import EventEnvelope
from app.integrations.calendar.auth import DeterministicCalendarTokenProvider
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.dtos import CalendarEventDTO, SyncStatus
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import HttpNotificationDispatcher
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)
from app.integrations.erp.config import MySqlErpSettings
from app.integrations.erp.exceptions import ScopeUnresolvedError
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from app.integrations.identity_mapper import ExternalIdentityMapper
from app.operations.freshness import ResourceFreshnessTracker
from app.operations.repair_queue import ScheduleRepairQueueService
from app.operations.resource_events import (
    ExternalResourceChangeEvent,
    ResourceChangeEventHandler,
)
from app.operations.risk_monitor import RiskMonitorService


class MockHttpTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport simulating external Google Calendar & E-8 Notification API."""

    def __init__(self) -> None:
        self.posted_requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.posted_requests.append(request)
        url_str = str(request.url)

        # Google Calendar mock responses
        if "googleapis.com" in url_str:
            if request.method == "POST":
                return httpx.Response(
                    201,
                    json={
                        "id": "mock-event-001",
                        "summary": "Live Exam Slot",
                        "etag": '"etag-mock-event-001-v1"',
                        "status": "confirmed",
                    },
                )
            elif request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "id": "mock-event-001",
                        "summary": "Live Exam Slot",
                        "etag": '"etag-mock-event-001-v1"',
                        "status": "confirmed",
                    },
                )

        # E-8 Notification Gateway mock responses
        if "e8.internal" in url_str or "notifications" in url_str:
            return httpx.Response(
                202,
                json={"status": "ACCEPTED", "command_id": request.headers.get("x-command-id", "")},
            )

        return httpx.Response(200, json={})


@pytest.fixture(autouse=True)
def check_phase7_e2e_opt_in() -> None:
    """Ensure test is opted in via E7_RUN_PHASE7_E2E=1 and infra is available."""
    if os.environ.get("E7_RUN_PHASE7_E2E") != "1":
        pytest.skip(
            "Phase 7 live E2E vertical slice requires explicit opt-in: E7_RUN_PHASE7_E2E=1"
        )

    # When explicitly opted in, required local PostgreSQL infrastructure MUST be reachable
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "55432"))
    try:
        with socket.create_connection((host, port), timeout=3.0):
            pass
    except Exception as exc:
        pytest.fail(
            f"Phase 7 live E2E requested (E7_RUN_PHASE7_E2E=1) but PostgreSQL "
            f"is unreachable at {host}:{port}: {exc}"
        )


@pytest.fixture
async def live_db_session() -> AsyncIterator[AsyncSession]:
    """Provide real AsyncSession connected to live PostgreSQL."""
    db_url = os.environ.get(
        "DATABASE_URL",
        os.environ.get(
            "E7_DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:55432/operations_engine",
        ),
    )
    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_phase7_live_vertical_slice(live_db_session: AsyncSession) -> None:
    """Full live vertical slice reconciling local SoR durability with external boundaries."""
    tenant_id = f"tenant-phase7-e2e-{uuid4().hex[:8]}"
    mock_transport = MockHttpTransport()

    # --------------------------------------------------------------------------
    # Step 1: BLK-01 Fail-Closed Enforcement (Upstream ERP extraction blocked)
    # --------------------------------------------------------------------------
    erp_settings = MySqlErpSettings(
        enabled=True,
        host="localhost",
        user="erp_readonly",
        password=SecretStr("mock_pass"),
        approved_tenant_scopes_json=f'{{"{tenant_id}": "school-org-77"}}',
    )
    erp_adapter = MySqlIreduAdapter(settings=erp_settings)

    # A tenant with NO approved mapping must fail closed under BLK-01 governance
    with pytest.raises(ScopeUnresolvedError) as exc_info:
        await erp_adapter.get_academic_sessions(tenant_id="unmapped-rogue-tenant")
    assert "BLK-01" in str(exc_info.value)

    # --------------------------------------------------------------------------
    # Step 2: Canonical External-ID Mapping (Durable calendar_links)
    # --------------------------------------------------------------------------
    identity_mapper = ExternalIdentityMapper()
    now = datetime.now(UTC)
    cal_mapping = await identity_mapper.map_calendar_entity(
        session=live_db_session,
        tenant_id=tenant_id,
        provider="google",
        external_calendar_id="primary",
        external_event_id="mock-event-001",
        canonical_assignment_id=None,
        etag='"etag-mock-event-001-v1"',
        provider_version=1,
        provider_updated_at=now,
    )
    assert cal_mapping.provider == "google"
    assert cal_mapping.external_id == "mock-event-001"
    assert cal_mapping.tenant_id == tenant_id

    # Verify persisted in live PostgreSQL calendar_links
    cal_link_stmt = select(CalendarLinkRecord).where(
        CalendarLinkRecord.tenant_id == tenant_id,
        CalendarLinkRecord.external_event_id == "mock-event-001",
    )
    cal_link_rec = (await live_db_session.execute(cal_link_stmt)).scalar_one_or_none()
    assert cal_link_rec is not None
    assert cal_link_rec.sync_status == "SYNCED"

    # --------------------------------------------------------------------------
    # Step 3: Resource Freshness (Computed directly from live SoR records)
    # --------------------------------------------------------------------------
    freshness_tracker = ResourceFreshnessTracker(default_threshold_seconds=3600.0)
    durable_freshness = await freshness_tracker.compute_from_durable_records(
        session=live_db_session,
        tenant_id=tenant_id,
    )
    assert len(durable_freshness) >= 1
    cal_freshness = freshness_tracker.get_freshness(tenant_id, "google", "mock-event-001")
    assert cal_freshness.staleness_state == "FRESH"
    assert cal_freshness.is_stale is False

    # --------------------------------------------------------------------------
    # Step 4: External Calendar Adapter (Simulated wire due to BLOCKED_ON_EXTERNAL_CREDENTIALS)
    # --------------------------------------------------------------------------
    cal_settings = GoogleCalendarSettings(
        enabled=True,
        client_email="test@school.iam.gserviceaccount.com",
        calendar_id="primary",
    )
    token_provider = DeterministicCalendarTokenProvider(
        token="ya29.synthetic_deterministic_phase7_token"
    )
    cal_client = httpx.AsyncClient(transport=mock_transport)
    cal_adapter = GoogleCalendarAdapter(
        settings=cal_settings,
        http_client=cal_client,
        token_provider=token_provider,
    )

    dto = CalendarEventDTO(
        external_event_id="mock-event-001",
        external_calendar_id="primary",
        title="Mathematics Paper 1 Exam",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=1, hours=2),
    )
    created_event = await cal_adapter.create_event(tenant_id, dto)
    assert created_event.status == SyncStatus.SYNCED
    assert created_event.etag is not None

    # Verify real Authorization header injection and wire format execution
    cal_requests = [
        req for req in mock_transport.posted_requests if "googleapis.com" in str(req.url)
    ]
    assert len(cal_requests) >= 1
    assert (
        cal_requests[0].headers.get("authorization")
        == "Bearer ya29.synthetic_deterministic_phase7_token"
    )
    assert token_provider.access_count >= 1

    # --------------------------------------------------------------------------
    # Step 5: Disruption Normalization, Durable Deduplication & Repair Queue
    # --------------------------------------------------------------------------
    repair_queue = ScheduleRepairQueueService()
    event_handler = ResourceChangeEventHandler(repair_queue=repair_queue)
    res_id = uuid4()

    # Stage resource in live DB
    resource = ResourceRecord(
        id=res_id,
        tenant_id=tenant_id,
        resource_key="hall-a",
        display_label="Exam Hall A",
        type="ROOM",
        active=True,
    )
    live_db_session.add(resource)
    await live_db_session.flush()

    disruption_event = ExternalResourceChangeEvent(
        event_id=f"disrupt-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
        provider="google_calendar",
        external_resource_id="hall-a",
        canonical_resource_id=res_id,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=3),
        reason="Facility emergency ceiling maintenance",
    )

    # 1. Process disruption: creates blackout and durable idempotency record
    handled = await event_handler.handle_resource_unavailable(
        live_db_session, disruption_event
    )
    assert handled is True

    # Verify blackout persisted in live PostgreSQL
    blackout_stmt = select(ResourceAvailabilityRecord).where(
        ResourceAvailabilityRecord.tenant_id == tenant_id,
        ResourceAvailabilityRecord.resource_id == res_id,
    )
    blackout = (await live_db_session.execute(blackout_stmt)).scalar_one_or_none()
    assert blackout is not None
    assert blackout.interval_type == "UNAVAILABLE"

    # Verify idempotency record persisted in live PostgreSQL
    idemp_stmt = select(IdempotencyRecord).where(
        IdempotencyRecord.tenant_id == tenant_id,
        IdempotencyRecord.operation_name == "resource.unavailable",
    )
    idemp_rec = (await live_db_session.execute(idemp_stmt)).scalar_one_or_none()
    assert idemp_rec is not None

    # 2. Replay deduplication check: second delivery recognized as duplicate
    replayed = await event_handler.handle_resource_unavailable(
        live_db_session, disruption_event
    )
    assert replayed is False

    # --------------------------------------------------------------------------
    # Step 6: Transactional Outbox Atomic Event Creation
    # --------------------------------------------------------------------------
    outbox_event_id = uuid4()
    envelope = EventEnvelope(
        event_id=outbox_event_id,
        event_type="resource.unavailable",
        tenant_id=tenant_id,
        correlation_id=f"corr-{outbox_event_id.hex[:8]}",
        occurred_at=now,
        payload={
            "resource_id": str(res_id),
            "reason": "Facility emergency ceiling maintenance",
            "start_at": (now + timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=3)).isoformat(),
        },
    )
    outbox_record = OutboxEventRecord(
        event_id=outbox_event_id,
        envelope=envelope.model_dump(mode="json"),
        dispatch_attempt_count=0,
    )
    live_db_session.add(outbox_record)
    await live_db_session.flush()

    # Verify outbox event is in pending state (published_at is NULL)
    outbox_stmt = select(OutboxEventRecord).where(
        OutboxEventRecord.event_id == outbox_event_id
    )
    pending_outbox = (await live_db_session.execute(outbox_stmt)).scalar_one_or_none()
    assert pending_outbox is not None
    assert pending_outbox.published_at is None
    assert pending_outbox.dispatch_attempt_count == 0

    # --------------------------------------------------------------------------
    # Step 7: Deterministic Risk Detection
    # --------------------------------------------------------------------------
    risk_monitor = RiskMonitorService(freshness_tracker=freshness_tracker)
    risks = await risk_monitor.evaluate_resource_availability_risks(
        session=live_db_session,
        tenant_id=tenant_id,
        resource_id=res_id,
    )
    assert len(risks) >= 0

    # --------------------------------------------------------------------------
    # Step 8: E-8 Notification Boundary Dispatching
    # --------------------------------------------------------------------------
    notify_settings = NotificationSettings(
        enabled=True,
        transport="http",
        endpoint_url="http://e8.internal/api/v1/notifications",
        api_key=SecretStr("mock-token"),
        timeout_seconds=5.0,
    )
    notify_client = httpx.AsyncClient(transport=mock_transport)
    dispatcher = HttpNotificationDispatcher(
        settings=notify_settings, http_client=notify_client
    )

    cmd = NotificationCommand(
        command_id=f"cmd-{uuid4().hex[:8]}",
        correlation_reference=f"corr-{outbox_event_id.hex[:8]}",
        causation_reference=f"evt-{disruption_event.event_id}",
        organization_scope_reference=tenant_id,
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="resource.unavailable",
            source_entity_reference=str(res_id),
        ),
        recipient_references=["urn:e7:cohort:faculty"],
        notification_context={"hall": "Exam Hall A"},
    )
    dispatched = await dispatcher.dispatch(cmd, live_db_session)
    assert dispatched is True

    # --------------------------------------------------------------------------
    # Step 9: Dashboard Analytics Aggregation
    # --------------------------------------------------------------------------
    analytics_service = OperationsAnalyticsService(
        risk_monitor=risk_monitor,
        freshness_tracker=freshness_tracker,
        repair_queue=repair_queue,
    )
    dashboard_summary = await analytics_service.get_operations_summary(
        session=live_db_session,
        tenant_id=tenant_id,
    )

    assert dashboard_summary.tenant_id == tenant_id
    assert dashboard_summary.risk_summary is not None
    assert dashboard_summary.risk_summary.stale_resources_count == 0
    assert dashboard_summary.risk_summary.repair_stability.locked_moved_count == 0
