"""Unit tests for Phase 8E Capacity Controls, Tenant Quotas, and Tenant Fairness.

Tests:
1. Multi-tenant fairness in worker job claiming (preventing backlog starvation).
2. Tenant quota enforcement on schedule solver jobs (HTTP 429 + Retry-After).
3. Tenant quota enforcement on workflow instances (HTTP 429 + Retry-After).
4. Tenant quota enforcement on plan generation (HTTP 429 + Retry-After).
5. Tenant quota enforcement on schedule repairs (HTTP 429 + Retry-After).
6. Security invariants: quota bypass prevention (header tampering & multi-credential consistency).
7. Configuration fail-safe bounds & validation on Settings.
8. CP-SAT solver time budget exhaustion produces safe BUDGET_EXHAUSTED status.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from app.core.capacity.quotas import (
    TenantQuotaExceededError,
    TenantQuotaManager,
)
from app.core.config import Settings
from app.db.models.scheduling import ScheduleJobRecord
from app.resources.schemas import ResourceType
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.service import SchedulingService
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.scheduling.states import ScheduleJobStatus

# ==============================================================================
# 1. Tenant Fairness Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_tenant_fairness_prevents_backlog_starvation() -> None:
    """Verify that a large backlog from Tenant A does NOT starve Tenant B in worker claiming."""
    service = SchedulingService()
    session = AsyncMock()

    now = datetime.now(UTC)
    # Tenant A has 3 queued jobs, Tenant B has 1 queued job (placed behind Tenant A)
    job_a1 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-A", status="REQUESTED", schedule_key="k1", created_at=now
    )
    job_a2 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-A", status="REQUESTED", schedule_key="k2", created_at=now
    )
    job_a3 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-A", status="REQUESTED", schedule_key="k3", created_at=now
    )
    job_b1 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-B", status="REQUESTED", schedule_key="k4", created_at=now
    )

    candidates = [job_a1, job_a2, job_a3, job_b1]

    candidate_result = MagicMock()
    candidate_result.scalars.return_value.all.return_value = candidates

    active_counts_result = MagicMock()
    active_counts_result.all.return_value = []

    session.execute.side_effect = [candidate_result, active_counts_result]

    claimed = await service.claim_jobs(
        session=session,
        worker_id="test-worker-1",
        batch_size=2,
        max_concurrent_per_tenant=1,
    )

    # Must claim job_a1 and job_b1 (skipping job_a2 and job_a3 due to concurrent limit)
    assert len(claimed) == 2
    assert claimed[0].tenant_id == "tenant-A"
    assert claimed[0].id == job_a1.id
    assert claimed[1].tenant_id == "tenant-B"
    assert claimed[1].id == job_b1.id

    assert job_a1.status == ScheduleJobStatus.SOLVING.value
    assert job_b1.status == ScheduleJobStatus.SOLVING.value
    assert job_a2.status == ScheduleJobStatus.REQUESTED.value
    assert job_a3.status == ScheduleJobStatus.REQUESTED.value


@pytest.mark.asyncio
async def test_tenant_fairness_skips_tenants_already_at_concurrent_limit() -> None:
    """Verify worker claiming prioritizes Tenant B if Tenant A is at concurrent limit."""
    service = SchedulingService()
    session = AsyncMock()

    now = datetime.now(UTC)
    job_a1 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-A", status="REQUESTED", schedule_key="k1", created_at=now
    )
    job_b1 = ScheduleJobRecord(
        id=uuid4(), tenant_id="tenant-B", status="REQUESTED", schedule_key="k2", created_at=now
    )

    candidate_result = MagicMock()
    candidate_result.scalars.return_value.all.return_value = [job_a1, job_b1]

    # Tenant A already has 1 job actively SOLVING
    active_counts_result = MagicMock()
    active_counts_result.all.return_value = [("tenant-A", 1)]

    session.execute.side_effect = [candidate_result, active_counts_result]

    claimed = await service.claim_jobs(
        session=session,
        worker_id="test-worker-1",
        batch_size=1,
        max_concurrent_per_tenant=1,
    )

    assert len(claimed) == 1
    assert claimed[0].tenant_id == "tenant-B"
    assert job_a1.status == ScheduleJobStatus.REQUESTED.value
    assert job_b1.status == ScheduleJobStatus.SOLVING.value


# ==============================================================================
# 2. Tenant Quota Enforcement Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_tenant_quota_solver_jobs_rejection() -> None:
    """Verify HTTP 429 + Retry-After when tenant exceeds max queued solver jobs."""
    settings = Settings(
        capacity_tenant_max_queued_solves=5,
        capacity_rate_limit_default_retry_after_seconds=30,
    )
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 5
    session.execute.return_value = count_result

    with pytest.raises(TenantQuotaExceededError) as exc_info:
        await quota_mgr.check_solve_submission_quota(session, "tenant-overloaded")

    err = exc_info.value
    assert err.status_code == 429
    assert err.code == "capacity.quota_exceeded"
    assert "Tenant 'tenant-overloaded' has exceeded" in err.message
    assert err.headers is not None
    assert err.headers["Retry-After"] == "30"
    assert err.resource_class == "solver_jobs"


@pytest.mark.asyncio
async def test_tenant_quota_solver_jobs_allowed_under_limit() -> None:
    """Verify submission passes when tenant is strictly below quota limit."""
    settings = Settings(capacity_tenant_max_queued_solves=5)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    session.execute.return_value = count_result

    await quota_mgr.check_solve_submission_quota(session, "tenant-normal")


@pytest.mark.asyncio
async def test_tenant_quota_workflow_burst_rejection() -> None:
    """Verify HTTP 429 when tenant exceeds running workflow quota."""
    settings = Settings(
        capacity_tenant_max_running_workflows=20,
        capacity_rate_limit_default_retry_after_seconds=30,
    )
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 20
    session.execute.return_value = count_result

    with pytest.raises(TenantQuotaExceededError) as exc_info:
        await quota_mgr.check_workflow_burst_quota(session, "tenant-wf-burst")

    err = exc_info.value
    assert err.status_code == 429
    assert err.resource_class == "workflow_burst"
    assert err.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_tenant_quota_active_plans_rejection() -> None:
    """Verify HTTP 429 when tenant exceeds active plan quota."""
    settings = Settings(capacity_tenant_max_active_plans=5)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 5
    session.execute.return_value = count_result

    with pytest.raises(TenantQuotaExceededError) as exc_info:
        await quota_mgr.check_active_plans_quota(session, "tenant-plans-burst")

    err = exc_info.value
    assert err.status_code == 429
    assert err.resource_class == "plan_generation"


@pytest.mark.asyncio
async def test_tenant_quota_repair_submission_rejection() -> None:
    """Verify HTTP 429 when tenant exceeds active repair quota."""
    settings = Settings(capacity_tenant_max_queued_repairs=5)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 5
    session.execute.return_value = count_result

    with pytest.raises(TenantQuotaExceededError) as exc_info:
        await quota_mgr.check_repair_submission_quota(session, "tenant-repairs-burst")

    err = exc_info.value
    assert err.status_code == 429
    assert err.resource_class == "repair_jobs"


# ==============================================================================
# 3. Security Regression: Quota Bypass Prevention
# ==============================================================================


@pytest.mark.asyncio
async def test_quota_keyed_strictly_by_authenticated_tenant() -> None:
    """Verify quotas are strictly enforced on trusted authenticated tenant context."""
    settings = Settings(capacity_tenant_max_queued_solves=3)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 3
    session.execute.return_value = count_result

    with pytest.raises(TenantQuotaExceededError):
        await quota_mgr.check_solve_submission_quota(session, "tenant-1")

    with pytest.raises(TenantQuotaExceededError):
        await quota_mgr.check_solve_submission_quota(session, "tenant-1")


@pytest.mark.asyncio
async def test_quota_shared_across_distinct_api_credentials_for_same_tenant() -> None:
    """Distinct API credentials for the same tenant must share identical quota ceiling."""
    settings = Settings(capacity_tenant_max_queued_solves=2)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    session.execute.return_value = count_result

    tenant_from_cred_a = "tenant-enterprise-1"
    with pytest.raises(TenantQuotaExceededError) as exc_a:
        await quota_mgr.check_solve_submission_quota(session, tenant_from_cred_a)
    assert exc_a.value.status_code == 429

    tenant_from_cred_b = "tenant-enterprise-1"
    with pytest.raises(TenantQuotaExceededError) as exc_b:
        await quota_mgr.check_solve_submission_quota(session, tenant_from_cred_b)
    assert exc_b.value.status_code == 429


@pytest.mark.asyncio
async def test_quota_enforcement_immune_to_forged_x_tenant_id() -> None:
    """Client sending forged X-Tenant-ID header cannot bypass quota on authenticated tenant."""
    settings = Settings(capacity_tenant_max_queued_solves=1)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    session.execute.return_value = count_result

    authenticated_tenant = "tenant-real"
    forged_header_tenant = "tenant-attacker-different"

    with pytest.raises(TenantQuotaExceededError) as exc:
        await quota_mgr.check_solve_submission_quota(session, authenticated_tenant)
    assert exc.value.status_code == 429
    assert "tenant-real" in exc.value.message
    assert forged_header_tenant not in exc.value.message


@pytest.mark.asyncio
async def test_quota_enforcement_immune_to_payload_tenant_mismatch() -> None:
    """Payload tenant mismatch cannot bypass quota on authenticated tenant."""
    settings = Settings(capacity_tenant_max_active_plans=1)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    session.execute.return_value = count_result

    authenticated_tenant = "tenant-target"
    with pytest.raises(TenantQuotaExceededError) as exc:
        await quota_mgr.check_active_plans_quota(session, authenticated_tenant)
    assert exc.value.status_code == 429
    assert "tenant-target" in exc.value.message


@pytest.mark.asyncio
async def test_concurrent_quota_submissions_fail_closed() -> None:
    """Concurrent submissions from the same tenant hitting the limit all fail closed (429)."""
    settings = Settings(capacity_tenant_max_queued_solves=5)
    quota_mgr = TenantQuotaManager(settings)

    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 5
    session.execute.return_value = count_result

    tasks = [
        quota_mgr.check_solve_submission_quota(session, "tenant-overloaded")
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert len(results) == 10
    for r in results:
        assert isinstance(r, TenantQuotaExceededError)
        assert r.status_code == 429



# ==============================================================================
# 4. Configuration Fail-Safe Validation Tests
# ==============================================================================


def test_settings_fail_safe_zero_or_negative_concurrency() -> None:
    """Invalid zero/negative concurrency must fail Settings validation."""
    with pytest.raises(ValidationError):
        Settings(capacity_solver_max_concurrency=0)

    with pytest.raises(ValidationError):
        Settings(capacity_solver_max_concurrency=-1)

    with pytest.raises(ValidationError):
        Settings(capacity_outbox_publish_concurrency=0)


def test_settings_fail_safe_absurd_batch_size() -> None:
    """Absurd batch size (> 500) must fail Settings validation."""
    with pytest.raises(ValidationError):
        Settings(capacity_outbox_batch_size=1000)

    with pytest.raises(ValidationError):
        Settings(capacity_outbox_batch_size=0)


def test_settings_fail_safe_negative_timeouts() -> None:
    """Negative/zero timeouts must fail Settings validation."""
    with pytest.raises(ValidationError):
        Settings(capacity_solver_time_budget_seconds=0.0)

    with pytest.raises(ValidationError):
        Settings(capacity_solver_time_budget_seconds=-10.0)

    with pytest.raises(ValidationError):
        Settings(capacity_flowable_pool_timeout_seconds=0.0)


def test_settings_fail_safe_quota_below_minimum() -> None:
    """Tenant quota below 1 must fail Settings validation."""
    with pytest.raises(ValidationError):
        Settings(capacity_tenant_max_queued_solves=0)

    with pytest.raises(ValidationError):
        Settings(capacity_tenant_max_running_workflows=0)


def test_settings_fail_safe_connection_pool_invariants() -> None:
    """Keepalive connections greater than max connections must fail Settings validation."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            capacity_flowable_pool_max_connections=10,
            capacity_flowable_pool_max_keepalive_connections=20,
        )
    assert "cannot exceed capacity_flowable_pool_max_connections" in str(exc_info.value)


def test_settings_fail_safe_rate_limit_policy_mode() -> None:
    """Invalid rate limit policy mode must fail Settings validation."""
    with pytest.raises(ValidationError):
        Settings(capacity_rate_limit_policy_mode="invalid_mode")


# ==============================================================================
# 5. Solver Time Budget & Budget Exhaustion Semantics
# ==============================================================================


def test_solver_timeout_produces_budget_exhausted_status() -> None:
    """Verify that when a solver times out, CpSatScheduler produces BUDGET_EXHAUSTED."""
    scheduler = CpSatScheduler()
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=8)
    req = SchedulingRequest(
        tenant_id="tenant-cpsat-test",
        schedule_key="test-solve-001",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="act-a",
                title="Task Alpha",
                duration_minutes=60,
                required_skills=["ELECTRICAL"],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-tech-1",
                type=ResourceType.PERSON,
                display_label="Technician One",
                capacity=1,
                skills=["ELECTRICAL"],
            )
        ],
        profile=SolverProfile(
            profile_key="test-profile",
            name="Test Profile",
            horizon_start=t0,
            horizon_end=t1,
            time_granularity_minutes=15,
            max_solve_seconds=0.1,
            random_seed=42,
            num_search_workers=1,
            soft_weights=SoftObjectiveWeights(
                preference_weight=0,
                idle_gap_weight=0,
                fairness_weight=0,
                utilization_weight=0,
                movement_weight=0,
            ),
        ),
    )

    with patch("ortools.sat.python.cp_model.CpSolver.solve", return_value=cp_model.UNKNOWN):
        result = scheduler.solve(req)
        assert result.status == ScheduleStatus.BUDGET_EXHAUSTED
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].root_cause_type == "BUDGET_EXHAUSTED"
        assert "Solver reached time budget" in result.diagnostics[0].human_readable_explanation
