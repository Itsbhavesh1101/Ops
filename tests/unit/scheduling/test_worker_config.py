"""Unit tests verifying fully explicit SchedulerSettings worker configuration.

Enforces:
- Zero production defaults for operational worker values:
  poll_interval_seconds, claim_batch_size, solve_timeout_seconds,
  lease_duration_seconds, shutdown_grace_seconds.
- Production instantiation without explicit parameters or E7_SCHEDULER_* fails clearly.
- Validated range checks for worker configuration.
"""

import pytest
from pydantic import ValidationError

from app.scheduling.config import SchedulerSettings


def test_scheduler_settings_requires_explicit_operational_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SchedulerSettings fails validation if operational parameters are omitted."""
    # Ensure no ambient E7_SCHEDULER_* environment variables exist
    for key in (
        "E7_SCHEDULER_POLL_INTERVAL_SECONDS",
        "E7_SCHEDULER_CLAIM_BATCH_SIZE",
        "E7_SCHEDULER_SOLVE_TIMEOUT_SECONDS",
        "E7_SCHEDULER_LEASE_DURATION_SECONDS",
        "E7_SCHEDULER_SHUTDOWN_GRACE_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError) as exc_info:
        SchedulerSettings()  # type: ignore[call-arg]

    errors = exc_info.value.errors()
    missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}

    expected_missing = {
        "poll_interval_seconds",
        "claim_batch_size",
        "solve_timeout_seconds",
        "lease_duration_seconds",
        "shutdown_grace_seconds",
    }
    assert expected_missing.issubset(
        missing_fields
    ), f"Expected missing fields {expected_missing}, got {missing_fields}"


def test_scheduler_settings_explicit_construction() -> None:
    """Verify explicit parameters instantiate successfully with zero defaults."""
    settings = SchedulerSettings(
        poll_interval_seconds=2.0,
        claim_batch_size=10,
        solve_timeout_seconds=45.0,
        lease_duration_seconds=120.0,
        shutdown_grace_seconds=10.0,
        worker_id="test-worker-1",
    )
    assert settings.poll_interval_seconds == 2.0
    assert settings.claim_batch_size == 10
    assert settings.solve_timeout_seconds == 45.0
    assert settings.lease_duration_seconds == 120.0
    assert settings.shutdown_grace_seconds == 10.0
    assert settings.worker_id == "test-worker-1"


def test_scheduler_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify SchedulerSettings loads properly from E7_SCHEDULER_* environment."""
    monkeypatch.setenv("E7_SCHEDULER_POLL_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("E7_SCHEDULER_CLAIM_BATCH_SIZE", "8")
    monkeypatch.setenv("E7_SCHEDULER_SOLVE_TIMEOUT_SECONDS", "25.0")
    monkeypatch.setenv("E7_SCHEDULER_LEASE_DURATION_SECONDS", "90.0")
    monkeypatch.setenv("E7_SCHEDULER_SHUTDOWN_GRACE_SECONDS", "3.0")
    monkeypatch.setenv("E7_SCHEDULER_WORKER_ID", "env-worker-42")

    settings = SchedulerSettings()
    assert settings.poll_interval_seconds == 0.5
    assert settings.claim_batch_size == 8
    assert settings.solve_timeout_seconds == 25.0
    assert settings.lease_duration_seconds == 90.0
    assert settings.shutdown_grace_seconds == 3.0
    assert settings.worker_id == "env-worker-42"


def test_scheduler_settings_range_validation() -> None:
    """Verify range constraints on operational worker parameters."""
    with pytest.raises(ValidationError):
        SchedulerSettings(
            poll_interval_seconds=0.01,  # ge=0.05
            claim_batch_size=5,
            solve_timeout_seconds=30.0,
            lease_duration_seconds=60.0,
            shutdown_grace_seconds=5.0,
        )

    with pytest.raises(ValidationError):
        SchedulerSettings(
            poll_interval_seconds=1.0,
            claim_batch_size=0,  # ge=1
            solve_timeout_seconds=30.0,
            lease_duration_seconds=60.0,
            shutdown_grace_seconds=5.0,
        )

    with pytest.raises(ValidationError):
        SchedulerSettings(
            poll_interval_seconds=1.0,
            claim_batch_size=5,
            solve_timeout_seconds=0.5,  # ge=1.0
            lease_duration_seconds=60.0,
            shutdown_grace_seconds=5.0,
        )
