"""Configuration settings for outbox dispatcher runner and dispatch policy.

Part of Phase 1 Foundation Task 1J.3B.
Provides:
- OutboxWorkerSettings: settings-driven operational policy configuration with E7_ prefix,
  strict numeric bounds, and fail-closed validation.
- Construction helpers for frozen OutboxRunnerPolicy and OutboxDispatchPolicy.
"""

import math
from datetime import timedelta
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.events.outbox.dispatcher import OutboxDispatchPolicy
from app.events.outbox.runner import OutboxRunnerPolicy


class OutboxWorkerSettings(BaseSettings):
    """Outbox dispatcher runner and dispatch operational settings.

    Loads configuration from environment variables prefixed with E7_
    or explicit constructor arguments. Contains strictly explicit technical
    bounds and safe development defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="E7_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    outbox_poll_interval_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Polling cadence in seconds when the outbox table is idle.",
    )
    outbox_max_concurrency: int = Field(
        default=1,
        ge=1,
        description="Maximum concurrent outbox dispatch workers.",
    )
    outbox_shutdown_grace_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Maximum grace period in seconds for in-flight dispatches during shutdown.",
    )
    outbox_lease_duration_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Duration in seconds an event claim lease remains valid.",
    )
    outbox_publish_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout in seconds for publishing an event to the broker transport.",
    )
    outbox_retry_base_delay_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Base delay in seconds for exponential backoff retry calculation.",
    )
    outbox_retry_max_delay_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Maximum capped delay in seconds for exponential backoff retries.",
    )
    outbox_retry_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        description="Multiplicative factor for exponential backoff retry progression.",
    )
    outbox_batch_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Batch size for outbox claim and settlement.",
    )
    outbox_publish_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum concurrent in-flight AMQP publishes during batch dispatch.",
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Validate relational invariants required by frozen policies."""
        if self.outbox_publish_timeout_seconds >= self.outbox_lease_duration_seconds:
            raise ValueError(
                f"outbox_publish_timeout_seconds ({self.outbox_publish_timeout_seconds}) "
                f"must be strictly less than outbox_lease_duration_seconds "
                f"({self.outbox_lease_duration_seconds})"
            )
        if self.outbox_retry_max_delay_seconds < self.outbox_retry_base_delay_seconds:
            raise ValueError(
                f"outbox_retry_max_delay_seconds ({self.outbox_retry_max_delay_seconds}) "
                f"must be greater than or equal to outbox_retry_base_delay_seconds "
                f"({self.outbox_retry_base_delay_seconds})"
            )
        if not math.isfinite(self.outbox_retry_multiplier):
            raise ValueError("outbox_retry_multiplier must be a finite number >= 1.0")
        return self

    def create_runner_policy(self) -> OutboxRunnerPolicy:
        """Construct a validated frozen OutboxRunnerPolicy from current settings."""
        return OutboxRunnerPolicy(
            poll_interval=timedelta(seconds=self.outbox_poll_interval_seconds),
            max_concurrency=self.outbox_max_concurrency,
            shutdown_grace_period=timedelta(seconds=self.outbox_shutdown_grace_seconds),
        )

    def create_dispatch_policy(self) -> OutboxDispatchPolicy:
        """Construct a validated frozen OutboxDispatchPolicy from current settings."""
        return OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=self.outbox_lease_duration_seconds),
            publish_timeout=timedelta(seconds=self.outbox_publish_timeout_seconds),
            retry_base_delay=timedelta(seconds=self.outbox_retry_base_delay_seconds),
            retry_max_delay=timedelta(seconds=self.outbox_retry_max_delay_seconds),
            retry_multiplier=self.outbox_retry_multiplier,
            batch_size=self.outbox_batch_size,
            publish_concurrency=self.outbox_publish_concurrency,
        )
