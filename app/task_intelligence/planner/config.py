"""Planner configuration and validation for Task Intelligence (Phase 2B).

Enforces:
1. External configuration invariant: provider names, model identifiers,
   base URLs, timeouts, retry budgets, and repair budgets must be externally configured.
2. Zero hardcoded production provider, model, or operational policy defaults.
3. Safe application startup: importing app.main or running API/worker does not require
   AI planner configuration.
4. Clear failure upon attempting to instantiate an unconfigured production planner.
"""

from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator


class PlannerConfigurationError(ValueError):
    """Raised when an AI planner is instantiated without required external configuration."""

    pass


class PlannerSettings(BaseModel):
    """Explicit, validated configuration for Task Intelligence AI planner.

    All runtime parameters (provider, models, timeouts, retry budgets, repair budgets)
    must be explicitly supplied from environment variables, configuration files,
    or test factories.
    """

    provider: str = Field(
        ...,
        min_length=1,
        description="Provider-neutral abstraction identifier (e.g. 'openai_compatible', 'test')",
    )
    primary_model: str = Field(
        ...,
        min_length=1,
        description="Primary model identifier for planning (e.g. benchmark candidate model name)",
    )
    fallback_model: str | None = Field(
        default=None,
        description="Optional fallback model identifier if primary fails",
    )
    timeout_seconds: float = Field(
        ...,
        gt=0.0,
        description="Explicit bounded timeout for LLM inference requests in seconds",
    )
    max_retries: int = Field(
        ...,
        ge=0,
        description="Explicit maximum bounded transport/provider retries",
    )
    max_repair_attempts: int = Field(
        ...,
        ge=0,
        description="Explicit maximum bounded targeted repair iterations against validation issues",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Secret API key for authorized model provider",
    )
    base_url: str | None = Field(
        default=None,
        description="Base URL for provider endpoint",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not cleaned:
            raise PlannerConfigurationError("Planner provider must not be blank.")
        return cleaned

    @classmethod
    def from_app_settings(cls, settings: Any) -> "PlannerSettings":
        """Build PlannerSettings from application Settings or environment.

        Fails with clear diagnostic error if required fields are unset.
        """
        provider = getattr(settings, "llm_provider", None)
        primary_model = getattr(settings, "llm_primary_model", None)
        timeout = getattr(settings, "llm_timeout_seconds", None)
        retries = getattr(settings, "llm_max_retries", None)
        repairs = getattr(settings, "llm_max_repair_attempts", None)

        missing = []
        if not provider:
            missing.append("llm_provider")
        if not primary_model:
            missing.append("llm_primary_model")
        if timeout is None:
            missing.append("llm_timeout_seconds")
        if retries is None:
            missing.append("llm_max_retries")
        if repairs is None:
            missing.append("llm_max_repair_attempts")

        if (
            missing
            or timeout is None
            or retries is None
            or repairs is None
            or provider is None
            or primary_model is None
        ):
            raise PlannerConfigurationError(
                f"Cannot construct AI planner without explicit configuration. "
                f"Missing required planner settings: {', '.join(missing)}. "
                f"Provider names, model IDs, timeouts, and retry/repair budgets "
                f"must be externally configured via environment or configuration files."
            )

        return cls(
            provider=str(provider),
            primary_model=str(primary_model),
            fallback_model=getattr(settings, "llm_fallback_model", None),
            timeout_seconds=float(timeout),
            max_retries=int(retries),
            max_repair_attempts=int(repairs),
            api_key=getattr(settings, "llm_api_key", None),
            base_url=getattr(settings, "llm_base_url", None),
        )
