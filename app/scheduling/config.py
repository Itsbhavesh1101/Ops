"""Configuration settings for the asynchronous scheduling worker.

Reads from environment with prefix E7_SCHEDULER_ using Pydantic Settings.
Configures:
- poll_interval_seconds
- claim_batch_size
- solve_timeout_seconds
- shutdown_grace_seconds
- worker_id
"""

from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    """Runtime configuration for scheduling worker."""

    model_config = SettingsConfigDict(
        env_prefix="E7_SCHEDULER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    poll_interval_seconds: float = Field(
        ...,
        ge=0.05,
        le=60.0,
        description="Worker poll loop idle interval (explicit required)",
    )
    claim_batch_size: int = Field(
        ...,
        ge=1,
        le=50,
        description="Maximum number of REQUESTED jobs to claim in batch (explicit required)",
    )
    solve_timeout_seconds: float = Field(
        ...,
        ge=1.0,
        le=600.0,
        description="Maximum execution timeout before solve is cancelled (explicit required)",
    )
    lease_duration_seconds: float = Field(
        ...,
        ge=5.0,
        le=3600.0,
        description="Lease duration in seconds before job is eligible for recovery",
    )
    shutdown_grace_seconds: float = Field(
        ...,
        ge=0.5,
        le=60.0,
        description="Grace period in seconds to finish active solve during shutdown",
    )
    worker_id: str = Field(
        default_factory=lambda: f"scheduler-worker-{uuid4().hex[:8]}",
        description="Unique identifier for the worker claiming jobs.",
    )
