"""Configuration settings for Flowable workflow runtime adapter (Phase 3A).

Enforces:
- Explicit configuration with zero silent defaults for production URLs, timeouts, or retries.
- Secret-masking of credentials using Pydantic SecretStr.
- Fail-clearly configuration error when required settings are missing.
"""

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FLOWABLE_SERVICE_AUTH_PRODUCTION: str = "DEFERRED_PENDING_DEPLOYMENT_SECRET_CONFIGURATION"


class FlowableConfigurationError(ValueError):
    """Raised when Flowable runtime adapter configuration is missing or invalid."""


class FlowableSettings(BaseSettings):
    """Runtime configuration for connecting to Flowable REST API.

    Zero silent production defaults. All endpoint, timeout/retry parameters,
    and authentication credentials must be explicitly provided or configured
    via environment variables. Exactly one auth mode (Basic or Bearer) must be set.
    """

    model_config = SettingsConfigDict(
        env_prefix="FLOWABLE_",
        extra="ignore",
    )

    base_url: str = Field(
        ...,
        description="Explicit base URL for Flowable REST API endpoint (no silent default)",
    )
    timeout_seconds: float = Field(
        ...,
        gt=0.0,
        le=60.0,
        description="Explicit bounded HTTP client timeout in seconds (no silent default)",
    )
    max_retries: int = Field(
        ...,
        ge=0,
        le=5,
        description=(
            "Explicit retry budget for idempotent transport GET requests (no silent default)"
        ),
    )
    username: str | None = Field(
        default=None,
        description="Optional Basic auth username",
    )
    password: SecretStr | None = Field(
        default=None,
        description="Optional Basic auth password masked as SecretStr",
    )
    token: SecretStr | None = Field(
        default=None,
        description="Optional Bearer token masked as SecretStr",
    )

    @property
    def auth_mode(self) -> str:
        """Return the active authentication strategy ('basic' or 'bearer')."""
        if self.token is not None:
            return "bearer"
        return "basic"

    @model_validator(mode="after")
    def validate_auth_strategy(self) -> "FlowableSettings":
        """Enforce exactly one valid authentication strategy (Basic or Bearer)."""
        has_basic_username = bool(self.username)
        has_basic_password = self.password is not None
        has_token = self.token is not None

        has_basic = has_basic_username or has_basic_password

        if has_basic and has_token:
            raise ValueError(
                "Ambiguous Flowable authentication configuration: cannot specify both "
                "Basic auth (username/password) and Bearer auth (token)."
            )

        if has_basic:
            if not (has_basic_username and has_basic_password):
                raise ValueError(
                    "Flowable Basic auth requires both username and password to be specified."
                )
            return self

        if has_token:
            return self

        raise ValueError(
            "Flowable authentication is required: must specify either Basic auth "
            "(username and password) or Bearer auth (token)."
        )

    @classmethod
    def from_env(cls) -> "FlowableSettings":
        try:
            return cls()  # type: ignore[call-arg]
        except ValidationError as exc:
            raise FlowableConfigurationError(
                f"Missing or invalid required Flowable runtime configuration: {exc}"
            ) from exc
