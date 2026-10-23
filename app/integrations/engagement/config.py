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


__all__ = ["NotificationSettings"]

