"""Configuration settings and runner policy for RabbitMQ consumer transport.

Part of Phase 1 Foundation Task 1G.4C2.
Provides:
- RabbitMqConsumerSettings: settings-driven topology configuration with E7_ prefix,
  SecretStr credential protection, and fail-closed required deployment attributes.
- RabbitMqConsumerRunnerPolicy: immutable operational policy defining concurrency bounds
  and graceful shutdown grace periods.
"""

import json
import re
from datetime import timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_topic_segment(segment: str, key: str) -> None:
    """Validate a single segment of a RabbitMQ topic routing key."""
    if not segment:
        raise ValueError(f"Binding key '{key}' contains empty dot segments.")
    if segment in ("*", "#"):
        return
    if "*" in segment or "#" in segment:
        raise ValueError(
            f"Binding key '{key}' has malformed wildcard segment '{segment}'. "
            f"Wildcards '*' and '#' must constitute entire topic segments."
        )
    if not re.fullmatch(r"^[a-zA-Z0-9_\-]+$", segment):
        raise ValueError(
            f"Binding key segment '{segment}' in '{key}' contains invalid characters. "
            f"Only alphanumeric characters, hyphens, and underscores are permitted."
        )


def _validate_binding_key(key: str) -> str:
    """Validate a single RabbitMQ topic binding key string."""
    stripped = key.strip()
    if not stripped:
        raise ValueError("Binding key must not be empty or whitespace.")
    if stripped != key:
        raise ValueError(f"Binding key '{key}' contains leading or trailing whitespace.")
    if " " in stripped:
        raise ValueError(f"Binding key '{key}' contains internal whitespace.")
    if stripped.startswith(".") or stripped.endswith(".") or ".." in stripped:
        raise ValueError(f"Binding key '{key}' contains empty dot segments.")

    segments = stripped.split(".")
    for seg in segments:
        _validate_topic_segment(seg, key)

    return stripped


class RabbitMqConsumerSettings(BaseSettings):
    """RabbitMQ consumer transport and topology settings.

    Fails closed: requires explicit deployment configuration from environment
    variables prefixed with E7_ or constructor arguments. Zero production-code
    fallback to localhost, guest credentials, or default queue/exchange names.
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
        description="Name of the durable topic exchange for domain events (required)",
    )
    rabbitmq_consumer_queue_name: str = Field(
        ...,
        max_length=255,
        description="Name of the durable consumer queue (required)",
    )
    rabbitmq_consumer_binding_keys: tuple[str, ...] = Field(
        ...,
        description="Configured topic binding keys (required, non-empty, no duplicates)",
    )
    rabbitmq_dead_letter_exchange_name: str = Field(
        ...,
        max_length=255,
        description="Name of the durable dead-letter exchange (required)",
    )
    rabbitmq_dead_letter_queue_name: str = Field(
        ...,
        max_length=255,
        description="Name of the durable dead-letter queue (required)",
    )
    rabbitmq_dead_letter_routing_key: str = Field(
        ...,
        max_length=255,
        description="Routing key binding the DLQ to the DLX (required)",
    )
    rabbitmq_consumer_max_concurrency: int = Field(
        ...,
        gt=0,
        description="Maximum concurrent message processing callbacks (required, >= 1)",
    )
    rabbitmq_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=300.0,
        description="Connection timeout in seconds for establishing RabbitMQ transport",
    )

    @field_validator("rabbitmq_url")
    @classmethod
    def validate_rabbitmq_url(cls, v: SecretStr) -> SecretStr:
        """Validate RabbitMQ URL protocol scheme."""
        secret_value = v.get_secret_value()
        if not (secret_value.startswith("amqp://") or secret_value.startswith("amqps://")):
            raise ValueError("rabbitmq_url must start with 'amqp://' or 'amqps://'")
        return v

    @field_validator(
        "rabbitmq_exchange_name",
        "rabbitmq_consumer_queue_name",
        "rabbitmq_dead_letter_exchange_name",
        "rabbitmq_dead_letter_queue_name",
        "rabbitmq_dead_letter_routing_key",
    )
    @classmethod
    def validate_non_empty_name(cls, v: str, info: Any) -> str:
        """Validate that topology names are non-empty, stripped, and bounded."""
        stripped = v.strip()
        if not stripped:
            raise ValueError(f"{info.field_name} must not be empty or whitespace.")
        if len(stripped) > 255:
            raise ValueError(f"{info.field_name} must not exceed 255 characters.")
        return stripped

    @field_validator("rabbitmq_consumer_binding_keys", mode="before")
    @classmethod
    def parse_and_validate_binding_keys(cls, v: Any) -> tuple[str, ...]:
        """Parse and strictly validate topic binding keys."""
        raw_keys: list[str] = []
        if isinstance(v, str):
            stripped_str = v.strip()
            if stripped_str.startswith("[") and stripped_str.endswith("]"):
                try:
                    parsed = json.loads(stripped_str)
                    if isinstance(parsed, list):
                        raw_keys = [str(k) for k in parsed]
                    else:
                        raise ValueError("JSON string must represent a list of strings")
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed JSON in binding keys: {exc}") from exc
            else:
                raw_keys = [k.strip() for k in stripped_str.split(",") if k.strip()]
        elif isinstance(v, (list, tuple, set)):
            raw_keys = [str(k) for k in v]
        else:
            raise ValueError("rabbitmq_consumer_binding_keys must be a list, tuple, or JSON string")

        if not raw_keys:
            raise ValueError("rabbitmq_consumer_binding_keys must not be empty.")

        validated: list[str] = []
        seen: set[str] = set()
        for k in raw_keys:
            valid_key = _validate_binding_key(k)
            if valid_key in seen:
                raise ValueError(
                    f"rabbitmq_consumer_binding_keys contains duplicate key: '{valid_key}'"
                )
            seen.add(valid_key)
            validated.append(valid_key)

        return tuple(validated)


class RabbitMqConsumerRunnerPolicy(BaseModel):
    """Immutable operational policy for the RabbitMQ consumer runner.

    Defines worker concurrency bounds and graceful shutdown timeouts.
    Contains strictly explicit configuration without hidden production defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrency: int = Field(
        ...,
        gt=0,
        description="Maximum concurrent message processing callbacks (>= 1).",
    )
    shutdown_grace_period: timedelta = Field(
        ...,
        description="Grace period duration for in-flight callbacks during shutdown.",
    )

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Validate operational bounds across consumer runner policy settings."""
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {self.max_concurrency}")
        if self.shutdown_grace_period <= timedelta(0):
            raise ValueError(
                f"shutdown_grace_period must be strictly positive, got {self.shutdown_grace_period}"
            )
        return self

    @classmethod
    def from_settings(
        cls,
        settings: RabbitMqConsumerSettings,
        *,
        shutdown_grace_period: timedelta,
    ) -> "RabbitMqConsumerRunnerPolicy":
        """Construct runner policy using authoritative max_concurrency from settings."""
        return cls(
            max_concurrency=settings.rabbitmq_consumer_max_concurrency,
            shutdown_grace_period=shutdown_grace_period,
        )
