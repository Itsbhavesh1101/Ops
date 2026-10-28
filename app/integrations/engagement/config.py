"""Configuration settings for E-8 Notification & Engagement Subsystem boundary.

Part of Phase 7D E-8 Production Notification Boundary.
"""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotificationSettings(BaseSettings):
    """Configuration for outbound E-8 notifications."""

    model_config = SettingsConfigDict(
        env_prefix="E7_NOTIFICATION_",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False, description="Whether outbound notification dispatch to E-8 is active"
    )
    transport: str = Field(
        default="broker",
        description="Outbound transport type: 'broker' (RabbitMQ) or 'http' (REST)",
    )
    endpoint_url: str = Field(
        default="",
        description=(
            "Target HTTP URL for E-8 notification gateway when transport is 'http' "
            "(e.g. https://<e8-host>/v1/notifications/commands)"
        ),
    )
    api_key: SecretStr = Field(
        default=SecretStr(""), description="API key / bearer token for E-8 gateway authorization"
    )
    exchange: str = Field(
        default="e8.notifications",
        description="RabbitMQ exchange name for AMQP notification transport",
    )
    timeout_seconds: float = Field(
        default=5.0, description="HTTP request or broker publish timeout in seconds"
    )
    rabbitmq_url: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Dedicated RabbitMQ broker connection URL for E-8 AMQP transport. "
            "Must be explicitly configured via E7_NOTIFICATION_RABBITMQ_URL. "
            "Zero silent fallback to E-7 internal domain-event broker (RABBITMQ_URL)."
        ),
    )
    mandatory_routing: bool = Field(
        default=True,
        description="Whether AMQP publish requires mandatory routing verification in broker",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum dispatch retry attempts for recoverable errors",
    )
    retry_backoff_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial backoff delay in seconds for retry attempts",
    )
    allow_provisional_recipients: bool = Field(
        default=False,
        description=(
            "Whether provisional cohort/role recipient references (e.g. urn:e7:cohort:faculty) "
            "are permitted without institutional resolution under DEC-E8-02. "
            "Defaults to False (fail-closed) in production."
        ),
    )
    pipeline_mode: str = Field(
        default="outbox",
        description=(
            "Notification pipeline execution mode: 'outbox' (durable two-stage notification "
            "outbox, Option C) or 'direct' (direct inline dispatch for test doubles)."
        ),
    )
    poll_interval_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Polling interval in seconds for the notification outbox delivery worker",
    )
    batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of notification commands claimed per poll batch",
    )
    lease_duration_seconds: float = Field(
        default=30.0,
        ge=5.0,
        description="Worker lease timeout in seconds for claimed notification commands",
    )
    enforce_lease_expiration: bool = Field(
        default=True,
        description=(
            "Whether command completion/failure requires an active (unexpired) lease. "
            "When True (default), an expired lease invalidates completion rights."
        ),
    )

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        """Validate and normalize outbound transport."""
        v_clean = v.strip().lower()
        if v_clean in ("broker", "amqp"):
            return "broker"
        if v_clean == "http":
            return "http"
        raise ValueError(
            f"Invalid E-7 notification transport: '{v}'. Must be 'broker' (or 'amqp') or 'http'."
        )

    @field_validator("pipeline_mode")
    @classmethod
    def validate_pipeline_mode(cls, v: str) -> str:
        """Validate and normalize pipeline execution mode."""
        v_clean = v.strip().lower()
        if v_clean in ("outbox", "direct"):
            return v_clean
        raise ValueError(
            f"Invalid E-7 notification pipeline_mode: '{v}'. Must be 'outbox' or 'direct'."
        )


__all__ = ["NotificationSettings"]

