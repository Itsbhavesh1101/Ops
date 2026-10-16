"""Configuration settings for Google Calendar REST v3 provider integration.

Part of Phase 7C Real Calendar Provider Adapter.
"""

from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GoogleCalendarSettings(BaseSettings):
    """Configuration for Google Calendar integration."""

    model_config = SettingsConfigDict(
        env_prefix="E7_GOOGLE_CALENDAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False, description="Whether live Google Calendar integration is active"
    )
    auth_mode: Literal["adc", "service_account_key"] = Field(
        default="adc",
        description=(
            "Google Calendar auth mode: 'adc' (keyless ADC) or 'service_account_key'"
        ),
    )
    client_email: str = Field(
        default="", description="OAuth2 service account or client email (legacy private-key mode)"
    )
    private_key: SecretStr = Field(
        default=SecretStr(""),
        description="Service account private key in PEM format (legacy private-key mode)",
    )
    calendar_id: str = Field(
        default="", description="Default Google Calendar ID (e.g. secondary or institutional ID)"
    )
    webhook_callback_url: str = Field(
        default="",
        description="Public HTTPS callback URL for Google push notifications",
    )
    webhook_token: SecretStr = Field(
        default=SecretStr(""), description="Shared secret token for verifying push notifications"
    )
    channel_ttl_seconds: int = Field(
        default=604800, description="Watch channel TTL in seconds (default 7 days)"
    )
    reconciliation_interval_seconds: float = Field(
        default=3600.0, description="Periodic reconciliation interval in seconds"
    )
    renewal_interval_seconds: float = Field(
        default=300.0, description="Watch renewal check interval in seconds"
    )
    timeout_seconds: float = Field(
        default=10.0, description="HTTP request timeout in seconds"
    )

    @field_validator("auth_mode", mode="before")
    @classmethod
    def normalize_auth_mode(cls, v: Any) -> str:
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("adc", "service_account_key"):
                return val
            raise ValueError(
                f"Unsupported E7_GOOGLE_CALENDAR_AUTH_MODE: '{v}'. "
                "Supported modes are 'adc' and 'service_account_key'."
            )
        return v

    @model_validator(mode="after")
    def validate_fail_closed_configuration(self) -> "GoogleCalendarSettings":
        """Fail closed if live Google Calendar is enabled but credentials missing."""
        if self.enabled:
            if not self.calendar_id.strip():
                raise ValueError("E7_GOOGLE_CALENDAR_ENABLED is true, but calendar_id is empty.")

            if self.auth_mode == "service_account_key":
                if not self.client_email.strip():
                    raise ValueError(
                        "E7_GOOGLE_CALENDAR_AUTH_MODE is 'service_account_key', "
                        "but client_email is empty."
                    )
                if not self.private_key.get_secret_value().strip():
                    raise ValueError(
                        "E7_GOOGLE_CALENDAR_AUTH_MODE is 'service_account_key', "
                        "but private_key is empty."
                    )
            elif self.auth_mode == "adc":
                # In ADC mode, client_email and private_key are NOT required.
                # Resolution occurs via google.auth.default() at runtime.
                pass
        return self


__all__ = ["GoogleCalendarSettings"]
