"""Configuration settings for RabbitMQ publisher transport.

Part of Phase 1 Foundation Task 1G.3A / 1G.3A.1.
Provides settings-driven configuration using Pydantic Settings with E7_ prefix,
SecretStr credential protection, and fail-closed required deployment attributes.
"""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RabbitMqPublisherSettings(BaseSettings):
    """RabbitMQ publisher transport settings.

    Fails closed: requires explicit deployment configuration (rabbitmq_url and
    rabbitmq_exchange_name) from environment variables prefixed with E7_ or constructor.
    Zero production-code fallback to localhost or guest credentials.
    """

    model_config = SettingsConfigDict(
        env_prefix="E7_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rabbitmq_url: SecretStr = Field(
        ...,
        description="RabbitMQ connection URL (required, protected secret, never logged)",
    )
    rabbitmq_exchange_name: str = Field(
        ...,
        max_length=255,
        description="Name of the durable topic exchange for E-7 domain events (required)",
    )
    rabbitmq_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=300.0,
        description="Connection timeout in seconds for establishing RabbitMQ transport",
    )

    @field_validator("rabbitmq_exchange_name")
    @classmethod
    def validate_exchange_name(cls, v: str) -> str:
        """Validate that exchange name is non-empty, stripped, and bounded."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("rabbitmq_exchange_name must not be empty or whitespace.")
        if len(stripped) > 255:
            raise ValueError("rabbitmq_exchange_name must not exceed 255 characters.")
        return stripped

    @field_validator("rabbitmq_url")
    @classmethod
    def validate_rabbitmq_url(cls, v: SecretStr) -> SecretStr:
        """Validate RabbitMQ URL protocol scheme."""
        secret_value = v.get_secret_value()
        if not (secret_value.startswith("amqp://") or secret_value.startswith("amqps://")):
            raise ValueError("rabbitmq_url must start with 'amqp://' or 'amqps://'")
        return v
