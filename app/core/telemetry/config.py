"""OpenTelemetry configuration module for the IR Infotech E-7 Operations Engine.

Defines strongly-typed telemetry settings via Pydantic Settings under the E7_
environment variable prefix. Telemetry is disabled by default for zero-overhead
local development and testing.
"""

from typing import Any
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetrySettings(BaseSettings):
    """Runtime configuration for OpenTelemetry SDK, exporters, and sampling.

    Environment variables use the standard application prefix `E7_`.
    Example: `E7_OTEL_ENABLED=true` maps to `otel_enabled`.
    """

    model_config = SettingsConfigDict(
        env_prefix="E7_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    otel_enabled: bool = Field(
        default=False,
        description="Master switch for OpenTelemetry tracing and metrics runtime.",
    )
    otel_service_name: str | None = Field(
        default=None,
        description="Logical service name for telemetry resource (required if enabled).",
    )
    otel_service_namespace: str = Field(
        default="e7",
        description="Logical service namespace for telemetry resource.",
    )
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        description="Base OTLP/HTTP collector endpoint, e.g. http://otel-collector:4318.",
    )
    otel_trace_sample_ratio: float = Field(
        default=1.0,
        description="Trace sampling ratio between 0.0 and 1.0 (default 1.0).",
    )
    otel_export_timeout_seconds: float = Field(
        default=10.0,
        description="OTLP export timeout in seconds (must be strictly positive).",
    )
    otel_metric_export_interval_seconds: float = Field(
        default=60.0,
        description="OTLP metric export interval in seconds (must be strictly positive).",
    )

    @field_validator("otel_service_name")
    @classmethod
    def validate_service_name(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("otel_service_name cannot be an empty or whitespace-only string")
        return v.strip() if v is not None else None

    @field_validator("otel_service_namespace")
    @classmethod
    def validate_service_namespace(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("otel_service_namespace cannot be empty or whitespace-only")
        return cleaned

    @field_validator("otel_trace_sample_ratio")
    @classmethod
    def validate_sample_ratio(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"Trace sample ratio must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("otel_export_timeout_seconds", "otel_metric_export_interval_seconds")
    @classmethod
    def validate_positive_seconds(cls, v: float, info: Any) -> float:
        if v <= 0.0:
            raise ValueError(f"{info.field_name} must be strictly positive (> 0.0), got {v}")
        return v

    @field_validator("otel_exporter_otlp_endpoint")
    @classmethod
    def validate_endpoint(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("OTLP exporter endpoint must use http or https scheme")
        if not parsed.netloc:
            raise ValueError("OTLP exporter endpoint must include a valid host")
        if parsed.username or parsed.password:
            raise ValueError("OTLP exporter endpoint must not contain user credentials")
        normalized = cleaned.rstrip("/")
        if normalized.endswith("/v1/traces") or normalized.endswith("/v1/metrics"):
            raise ValueError(
                "OTLP exporter endpoint must be the base collector URL without "
                "signal paths (/v1/traces or /v1/metrics)"
            )
        return normalized

    @model_validator(mode="after")
    def validate_enabled_requirements(self) -> "TelemetrySettings":
        if self.otel_enabled:
            if not self.otel_service_name:
                raise ValueError("otel_service_name is required when otel_enabled is True")
            if not self.otel_exporter_otlp_endpoint:
                raise ValueError(
                    "otel_exporter_otlp_endpoint is required when otel_enabled is True"
                )
        return self

    @property
    def traces_endpoint(self) -> str:
        """Construct the signal-specific OTLP/HTTP traces endpoint."""
        if not self.otel_exporter_otlp_endpoint:
            raise ValueError(
                "Cannot derive traces_endpoint without otel_exporter_otlp_endpoint configured"
            )
        return f"{self.otel_exporter_otlp_endpoint}/v1/traces"

    @property
    def metrics_endpoint(self) -> str:
        """Construct the signal-specific OTLP/HTTP metrics endpoint."""
        if not self.otel_exporter_otlp_endpoint:
            raise ValueError(
                "Cannot derive metrics_endpoint without otel_exporter_otlp_endpoint configured"
            )
        return f"{self.otel_exporter_otlp_endpoint}/v1/metrics"


def resolve_telemetry_settings(
    default_service_name: str,
    override_settings: TelemetrySettings | None = None,
    **kwargs: Any,
) -> TelemetrySettings:
    """Resolve TelemetrySettings for a service entrypoint with canonical service identity.

    Precedence rule:
    1. Explicit override_settings passed directly takes highest precedence.
    2. Operator environment/file configuration (E7_OTEL_SERVICE_NAME) takes precedence if specified.
    3. If E7_OTEL_SERVICE_NAME is unset, the entrypoint's default_service_name
       ('e7-api' or 'e7-worker') is applied as the canonical service name.

    Validation guarantees:
    - If telemetry is enabled and the operator configured an invalid service name (e.g. whitespace),
      validation fails immediately.
    - If telemetry is enabled and endpoint is missing or invalid, validation fails immediately.
    - If telemetry is disabled, the entrypoint default service name is attached for consistency.
    """
    if override_settings is not None:
        return override_settings

    try:
        settings = TelemetrySettings(**kwargs)
        if settings.otel_service_name:
            if default_service_name == "e7-worker" and settings.otel_service_name == "e7-api":
                kwargs_override = {k: v for k, v in kwargs.items() if k != "otel_service_name"}
                return TelemetrySettings(otel_service_name=default_service_name, **kwargs_override)
            return settings
        return TelemetrySettings(otel_service_name=default_service_name, **kwargs)
    except ValidationError as err:
        # Check if the validation error was due to missing otel_service_name when enabled
        has_missing_service_name = any(
            "otel_service_name is required" in str(e.get("msg", "")) for e in err.errors()
        )
        has_invalid_service_name = any(e.get("loc") == ("otel_service_name",) for e in err.errors())
        if has_missing_service_name and not has_invalid_service_name:
            return TelemetrySettings(otel_service_name=default_service_name, **kwargs)
        raise
