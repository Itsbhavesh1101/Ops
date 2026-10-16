"""Configuration settings for E-8 Notification & Engagement Subsystem boundary.

Part of Phase 7D E-8 Production Notification Boundary.
"""

from pydantic import Field, SecretStr
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
        description="Target HTTP URL for E-8 notification gateway when transport is 'http'",
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


__all__ = ["NotificationSettings"]
