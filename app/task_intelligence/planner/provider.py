"""Provider-neutral model factory for Task Intelligence (Phase 2B).

Decouples PydanticAI model instantiation from specific vendor SDKs,
preserving provider neutrality and allowing test models, benchmark models,
and configured fallbacks without vendor lock-in.

Zero hardcoded production defaults: provider, model, and endpoint must be
externally configured.
"""

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import Settings, get_settings
from app.task_intelligence.planner.config import (
    PlannerSettings,
)


class ProviderConfigurationError(Exception):
    """Raised when an AI provider or model is improperly configured."""

    pass


def is_sarvam_access_available(settings: PlannerSettings | Settings | None = None) -> bool:
    """Check whether authorized Sarvam/LLM API credentials exist in environment.

    Safe discovery function: never logs or returns the secret credential.
    """
    if isinstance(settings, PlannerSettings):
        return bool(settings.api_key and settings.api_key.get_secret_value().strip())
    s = settings or get_settings()
    if s.llm_api_key and s.llm_api_key.get_secret_value().strip():
        return True
    return False


def get_llm_model(
    model_name: str | None = None,
    fallback: bool = False,
    settings: PlannerSettings | Settings | None = None,
    custom_model: Model | None = None,
) -> Model:
    """Instantiate a provider-neutral PydanticAI Model.

    Args:
        model_name: Optional explicit model identifier override.
        fallback: If True, resolves the configured fallback model.
        settings: Planner or application settings containing explicit LLM configuration.
        custom_model: Optional test or pre-configured model instance.

    Returns:
        pydantic_ai.models.Model: Configured model instance.

    Raises:
        ProviderConfigurationError: If requested model/provider configuration is invalid
            or fallback is requested but unconfigured.
        PlannerConfigurationError: If required settings are missing.
    """
    if custom_model is not None:
        return custom_model

    if isinstance(settings, PlannerSettings):
        cfg = settings
    elif isinstance(settings, Settings):
        cfg = PlannerSettings.from_app_settings(settings)
    elif settings is None:
        cfg = PlannerSettings.from_app_settings(get_settings())
    else:
        raise ProviderConfigurationError(f"Unsupported settings type: {type(settings).__name__}")

    # Determine target model name
    if fallback:
        if not cfg.fallback_model and not model_name:
            raise ProviderConfigurationError(
                "Fallback model was requested but no fallback model is configured."
            )
        resolved_name = model_name or cfg.fallback_model
    else:
        resolved_name = model_name or cfg.primary_model

    if not resolved_name:
        raise ProviderConfigurationError(
            "Primary model is not configured. Configure primary_model via environment or settings."
        )

    provider_type = cfg.provider.lower().strip()

    if provider_type == "test":
        return TestModel()

    if provider_type == "openai_compatible":
        api_key_str: str | None = cfg.api_key.get_secret_value() if cfg.api_key else None
        if not api_key_str:
            raise ProviderConfigurationError(
                "Missing LLM API key for 'openai_compatible' provider. "
                "Provide E7_LLM_API_KEY in environment or configure provider as 'test'."
            )
        try:
            provider = OpenAIProvider(
                base_url=cfg.base_url,
                api_key=api_key_str,
            )
            return OpenAIChatModel(
                model_name=resolved_name,
                provider=provider,
            )
        except Exception as exc:
            raise ProviderConfigurationError(
                f"Failed to initialize model '{resolved_name}': {exc}"
            ) from exc

    raise ProviderConfigurationError(
        f"Unsupported or unrecognized LLM provider type: '{cfg.provider}'. "
        "Supported providers are 'openai_compatible' and 'test'."
    )
