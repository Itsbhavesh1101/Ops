"""Unit tests for Phase 4A Scheduling HTTP API contracts and router factory."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.idempotency import register_idempotency_exception_handler
from app.api.v1.scheduling import (
    SCHEDULING_API_PRODUCTION_MOUNT,
    create_scheduling_router,
)
from app.core.auth.models import TenantContext
from app.db.models.scheduling import ScheduleJobRecord
from app.main import app as main_app
from app.resources.schemas import AvailabilityIntervalRead, IntervalType, ResourceType
from app.scheduling.constraints.models import NoOverlapConstraint
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import SchedulingActivity, SchedulingRequest, SchedulingResourceSnapshot


def test_production_mount_deferred() -> None:
    """Verify production mounting constant and that routes are not mounted in main_app."""
    assert SCHEDULING_API_PRODUCTION_MOUNT == "DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY"

    routes = [route.path for route in main_app.routes]  # type: ignore
    assert "/api/v1/schedule-jobs" not in routes
    assert "/api/v1/resources/availability" not in routes


@pytest.fixture
def dummy_request() -> SchedulingRequest:
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    profile = SolverProfile(
        profile_key="test-prof",
        version=1,
        name="Test",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Technician 1",
        capacity=1,
    )
    act = SchedulingActivity(
        id="act-1",
        title="Activity 1",
        duration_minutes=60,
        required_resource_ids=["tech-1"],
    )
    return SchedulingRequest(
        tenant_id="tenant-123",
        schedule_key="maint",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act],
        resources=[res],
        constraints=[
            NoOverlapConstraint(id="c1", name="No Overlap Tech 1", resource_ids=["tech-1"])
        ],
        profile=profile,
    )


def test_scheduling_router_endpoints(dummy_request: SchedulingRequest) -> None:
    """Verify router factory creates endpoints with tenant boundary and error handling."""
    mock_tenant = TenantContext(tenant_id="tenant-123")
    mock_session = AsyncMock()

    mock_service = MagicMock()
    mock_job_id = uuid4()
    mock_job = ScheduleJobRecord(
        id=mock_job_id,
        tenant_id="tenant-123",
        schedule_key="maint",
        status="REQUESTED",
        input_snapshot=dummy_request.model_dump(mode="json"),
        input_snapshot_hash="hash-123",
        solver_metadata={},
        diagnostics=[],
        created_at=datetime.now(UTC),
    )
    mock_service.submit_job = AsyncMock(return_value=mock_job)
    mock_service.get_job = AsyncMock(return_value=mock_job)

    mock_resource_service = MagicMock()
    mock_resource_service.get_intervals = AsyncMock(
        return_value=[
            AvailabilityIntervalRead(
                tenant_id="tenant-123",
                resource_id=uuid4(),
                start_at=datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
                end_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
                interval_type=IntervalType.UNAVAILABLE,
                reason="Maintenance",
                created_at=datetime.now(UTC),
            )
        ]
    )

    test_app = FastAPI()
    register_api_exception_handlers(test_app)
    register_idempotency_exception_handler(test_app)

    router = create_scheduling_router(
        tenant_dependency=lambda: mock_tenant,
        session_dependency=lambda: mock_session,
        scheduling_service_dependency=lambda: mock_service,
        resource_service_dependency=lambda: mock_resource_service,
    )
    test_app.include_router(router, prefix="/api/v1")

    client = TestClient(test_app)

    # 1. POST /api/v1/schedule-jobs (client request payload without tenant_id)
    req_dict = dummy_request.model_dump(mode="json")
    req_dict.pop("tenant_id", None)
    post_payload = {
        "schedule_key": "maint",
        "request": req_dict,
    }
    resp = client.post("/api/v1/schedule-jobs", json=post_payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["job_id"] == str(mock_job_id)
    assert data["status"] == "REQUESTED"
    assert data["schedule_key"] == "maint"

    # 2. GET /api/v1/schedule-jobs/{id}
    resp_get = client.get(f"/api/v1/schedule-jobs/{mock_job_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["job_id"] == str(mock_job_id)

    # 3. GET /api/v1/schedule-jobs/{unknown_id}
    mock_service.get_job = AsyncMock(return_value=None)
    unknown_id = uuid4()
    resp_404 = client.get(f"/api/v1/schedule-jobs/{unknown_id}")
    assert resp_404.status_code == 404

    # 4. GET /api/v1/resources/availability
    resp_avail = client.get("/api/v1/resources/availability")
    assert resp_avail.status_code == 200
    intervals = resp_avail.json()
    assert len(intervals) == 1
    assert intervals[0]["interval_type"] == "UNAVAILABLE"
    assert intervals[0]["reason"] == "Maintenance"


def test_scheduling_router_rejects_client_tenant_id(dummy_request: SchedulingRequest) -> None:
    """Verify that clients supplying tenant_id in request body are rejected with 422."""
    mock_tenant = TenantContext(tenant_id="trusted-tenant")
    mock_session = AsyncMock()
    mock_service = MagicMock()

    test_app = FastAPI()
    register_api_exception_handlers(test_app)

    router = create_scheduling_router(
        tenant_dependency=lambda: mock_tenant,
        session_dependency=lambda: mock_session,
        scheduling_service_dependency=lambda: mock_service,
    )
    test_app.include_router(router, prefix="/api/v1")
    client = TestClient(test_app)

    # Supply forbidden tenant_id in request body
    bad_req_dict = dummy_request.model_dump(mode="json")
    bad_req_dict["tenant_id"] = "attacker-tenant"

    resp = client.post(
        "/api/v1/schedule-jobs",
        json={"schedule_key": "test", "request": bad_req_dict},
    )
    assert resp.status_code == 422


def test_scheduling_router_publish_and_repair_endpoints() -> None:
    """Verify publish and repair HTTP API endpoints with tenant isolation."""
    mock_tenant = TenantContext(tenant_id="tenant-123")
    mock_session = AsyncMock()
    mock_execute_res = MagicMock()
    mock_execute_res.scalar_one_or_none.return_value = "mock_key_digest"
    mock_session.execute = AsyncMock(return_value=mock_execute_res)
    mock_service = MagicMock()

    test_app = FastAPI()
    register_api_exception_handlers(test_app)
    register_idempotency_exception_handler(test_app)

    router = create_scheduling_router(
        tenant_dependency=lambda: mock_tenant,
        session_dependency=lambda: mock_session,
        scheduling_service_dependency=lambda: mock_service,
    )
    test_app.include_router(router, prefix="/api/v1")
    client = TestClient(test_app)

    # 1. Publish endpoint
    job_id = uuid4()
    sched_id = uuid4()
    published_sched = MagicMock()
    published_sched.id = sched_id
    published_sched.version = 1
    published_sched.status = "PUBLISHED"
    published_sched.published_at = datetime.now(UTC)
    published_sched.published_by = "operator@irinfotech.internal"
    published_sched.supersedes_id = None
    published_sched.assignments = []

    mock_service.publish_job = AsyncMock(return_value=published_sched)

    resp_pub = client.post(
        f"/api/v1/schedule-jobs/{job_id}/publish",
        json={"principal": "operator@irinfotech.internal"},
        headers={"Idempotency-Key": "idemp-pub-test"},
    )
    assert resp_pub.status_code == 200
    pub_data = resp_pub.json()
    assert pub_data["schedule_id"] == str(sched_id)
    assert pub_data["status"] == "PUBLISHED"
    assert pub_data["published_by"] == "operator@irinfotech.internal"

    # 2. Repair endpoint
    repair_candidate = MagicMock()
    repair_candidate.id = uuid4()
    repair_candidate.version = 2
    repair_candidate.status = "REVIEW"
    repair_candidate.supersedes_id = sched_id
    repair_candidate.assignments = []
    repair_candidate.score_components = {"movement_penalty": 0}

    mock_repair_job = MagicMock()
    mock_repair_job.id = uuid4()
    mock_repair_job.status = "FEASIBLE"

    mock_service.repair_schedule = AsyncMock(return_value=(mock_repair_job, repair_candidate))

    repair_payload = {
        "disrupted_resource_ids": ["tech-1"],
        "disrupted_interval_start": "2026-10-05T08:00:00Z",
        "disrupted_interval_end": "2026-10-05T10:00:00Z",
        "reason": "SICK_LEAVE",
        "movement_weight": 10,
    }
    resp_repair = client.post(
        f"/api/v1/schedules/{sched_id}/repair",
        json=repair_payload,
        headers={"Idempotency-Key": "idemp-repair-test"},
    )
    assert resp_repair.status_code == 200
    repair_data = resp_repair.json()
    assert repair_data["job_id"] == str(mock_repair_job.id)
    assert repair_data["baseline_schedule_id"] == str(sched_id)
    assert repair_data["candidate_schedule_id"] == str(repair_candidate.id)
    assert repair_data["candidate_version"] == 2
    assert repair_data["status"] == "FEASIBLE"

    # 3. Repair endpoint rejects client tenant_id
    bad_repair_payload = dict(repair_payload)
    bad_repair_payload["tenant_id"] = "client-injected-tenant"
    resp_bad = client.post(
        f"/api/v1/schedules/{sched_id}/repair",
        json=bad_repair_payload,
        headers={"Idempotency-Key": "idemp-bad-repair"},
    )
    assert resp_bad.status_code == 422
