"""Application runtime configuration module for the IR Infotech E-7 Operations Engine.

Provides strongly typed configuration via Pydantic Settings with safe technical defaults,
environment prefix handling (E7_), and cached settings retrieval.
"""

import json
import re
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentType = Literal["local", "development", "test", "staging", "production"]
LogLevelType = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormatType = Literal["json", "console"]

VALID_ENVIRONMENTS = {"local", "development", "test", "staging", "production"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_LOG_FORMATS = {"json", "console"}


class Settings(BaseSettings):
    """Core technical application settings.

    Contains only technical runtime and platform configuration.
    Domain policies, database credentials, external provider secrets,
    and solver parameters belong to subsequent roadmap tasks.
    """

    model_config = SettingsConfigDict(
        env_prefix="E7_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(
        default="IR Infotech E-7 Operations Engine",
        description="Public service and platform identifier",
    )
    environment: EnvironmentType = Field(
        default="local",
        description="Bounded runtime environment classification",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode diagnostics",
    )
    log_level: LogLevelType = Field(
        default="INFO",
        description="Logging threshold level",
    )
    log_format: LogFormatType = Field(
        default="json",
        description="Structured logging output format (json for production, console for local)",
    )
    correlation_header_name: str = Field(
        default="X-Correlation-ID",
        description="HTTP header key for request correlation tracking",
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=list,
        description="List of allowed CORS origins for browser ingress",
    )

    # Google OIDC technical verifier configuration (Task 1C.2)
    google_oidc_discovery_url: str = Field(
        default="https://accounts.google.com/.well-known/openid-configuration",
        description="OIDC discovery URL for Google Identity provider",
    )
    google_oidc_client_ids: list[str] = Field(
        default_factory=list,
        description="Approved Google OAuth client IDs (audiences)",
    )
    google_oidc_allowed_hosted_domains: list[str] = Field(
        default_factory=list,
        description="Allowed Google Workspace hosted domains (hd claim allowlist)",
    )
    google_oidc_http_timeout_seconds: float = Field(
        default=10.0,
        description="HTTP timeout for discovery and JWKS metadata retrieval",
    )
    google_oidc_clock_skew_seconds: int = Field(
        default=60,
        description="Allowed clock skew in seconds for token verification leeway",
    )
    google_oidc_cache_fallback_seconds: int = Field(
        default=3600,
        description="Fallback cache TTL in seconds for discovery and JWKS metadata",
    )
    google_oidc_cache_max_seconds: int = Field(
        default=86400,
        description="Maximum cache TTL in seconds for discovery and JWKS metadata",
    )

    # Service API Key technical verifier configuration (Task 1C.3)
    service_api_key_header_name: str = Field(
        default="X-E7-Service-Key",
        description="HTTP header name carrying service API key credential",
    )

    # PostgreSQL persistence technical configuration (Task 1D)
    database_url: SecretStr | None = Field(
        default=None,
        description="PostgreSQL async connection string (postgresql+asyncpg://...)",
    )
    database_pool_size: int = Field(
        default=5,
        description="SQLAlchemy connection pool size",
    )
    database_max_overflow: int = Field(
        default=10,
        description="SQLAlchemy max overflow connections beyond pool_size",
    )
    database_pool_timeout_seconds: float = Field(
        default=30.0,
        description="SQLAlchemy pool timeout waiting for connection in seconds",
    )
    database_pool_recycle_seconds: int = Field(
        default=1800,
        description="SQLAlchemy pool recycle connection lifetime in seconds (-1 to disable)",
    )
    database_connect_timeout_seconds: float = Field(
        default=10.0,
        description="asyncpg connection establishment timeout in seconds",
    )
    database_echo: bool = Field(
        default=False,
        description="Enable SQLAlchemy SQL query echo logging (strictly False in production)",
    )

    # AI / LLM Task Intelligence configuration (Phase 2B)
    # Zero hardcoded production defaults: provider, model, timeout, retries,
    # and repairs must be externally configured to instantiate an AI planner.
    llm_provider: str | None = Field(
        default=None,
        description="Provider-neutral abstraction identifier (e.g. openai_compatible, test)",
    )
    llm_primary_model: str | None = Field(
        default=None,
        description="Primary model identifier for Task Intelligence planning (benchmark candidate)",
    )
    llm_fallback_model: str | None = Field(
        default=None,
        description="Configured fallback model identifier if primary fails",
    )
    llm_timeout_seconds: float | None = Field(
        default=None,
        description="Bounded timeout for LLM inference requests in seconds",
    )
    llm_max_retries: int | None = Field(
        default=None,
        description="Maximum bounded transport/provider retries for retryable failures",
    )
    llm_max_repair_attempts: int | None = Field(
        default=None,
        description=(
            "Maximum bounded targeted repair iterations against deterministic validation issues"
        ),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        description="Secret API key for authorized model provider",
    )
    llm_base_url: str | None = Field(
        default=None,
        description="Base URL for provider endpoint",
    )

    @field_validator("environment", mode="before")
    @classmethod
    def validate_and_normalize_environment(cls, value: Any) -> str:
        """Normalize environment string to lowercase and validate against allowed set."""
        if not isinstance(value, str):
            raise ValueError(f"Invalid environment type: {type(value).__name__}; must be string")
        normalized = value.strip().lower()
        if normalized not in VALID_ENVIRONMENTS:
            raise ValueError(
                f"Invalid environment '{value}'. Allowed environments: {sorted(VALID_ENVIRONMENTS)}"
            )
        return normalized

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_and_normalize_log_level(cls, value: Any) -> str:
        """Normalize log level string to uppercase and validate against recognized levels."""
        if not isinstance(value, str):
            raise ValueError(f"Invalid log_level type: {type(value).__name__}; must be string")
        normalized = value.strip().upper()
        if normalized not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log_level '{value}'. Allowed log levels: {sorted(VALID_LOG_LEVELS)}"
            )
        return normalized

    @field_validator("log_format", mode="before")
    @classmethod
    def validate_and_normalize_log_format(cls, value: Any) -> str:
        """Normalize log format string to lowercase and validate."""
        if not isinstance(value, str):
            raise ValueError(f"Invalid log_format type: {type(value).__name__}; must be string")
        normalized = value.strip().lower()
        if normalized not in VALID_LOG_FORMATS:
            raise ValueError(
                f"Invalid log_format '{value}'. Allowed formats: {sorted(VALID_LOG_FORMATS)}"
            )
        return normalized

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: Any) -> list[str]:
        """Parse CORS origins from list, JSON string, or comma-delimited string."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(o).strip() for o in parsed if str(o).strip()]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    @field_validator("service_api_key_header_name", mode="before")
    @classmethod
    def validate_service_api_key_header_name(cls, value: Any) -> str:
        """Validate header name syntax, rejecting blank, whitespace, CR/LF, or control chars."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("service_api_key_header_name must be a non-empty string")
        normalized = value.strip()
        if not re.match(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$", normalized):
            raise ValueError(
                f"Invalid service_api_key_header_name '{value}'; must be a valid HTTP "
                "header token without spaces, control characters, or delimiters"
            )
        if len(normalized) > 128:
            raise ValueError("service_api_key_header_name exceeds maximum length of 128 characters")
        return normalized

    @field_validator("google_oidc_discovery_url", mode="before")
    @classmethod
    def validate_google_oidc_discovery_url(cls, value: Any) -> str:
        """Validate that Google OIDC discovery URL is pinned to Google's canonical endpoint."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("google_oidc_discovery_url must be a non-empty string")
        normalized = value.strip()
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "accounts.google.com"
            or parsed.path != "/.well-known/openid-configuration"
        ):
            raise ValueError(
                "google_oidc_discovery_url must point to Google's canonical discovery endpoint: "
                "https://accounts.google.com/.well-known/openid-configuration"
            )
        return normalized

    @field_validator("google_oidc_client_ids", mode="before")
    @classmethod
    def parse_google_oidc_client_ids(cls, value: Any) -> list[str]:
        """Parse and normalize client IDs, rejecting blank values."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set, frozenset)):
            res: list[str] = []
            for item in value:
                s = str(item).strip()
                if not s:
                    raise ValueError("google_oidc_client_ids cannot contain blank entries")
                res.append(s)
            return res
        if isinstance(value, str):
            val = value.strip()
            if not val:
                return []
            if val.startswith("[") and val.endswith("]"):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return cls.parse_google_oidc_client_ids(parsed)
                except json.JSONDecodeError:
                    pass
            parts = [item.strip() for item in val.split(",") if item.strip()]
            return parts
        return []

    @field_validator("google_oidc_allowed_hosted_domains", mode="before")
    @classmethod
    def parse_google_oidc_allowed_hosted_domains(cls, value: Any) -> list[str]:
        """Parse and normalize hosted domains to lowercase, rejecting blank values."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set, frozenset)):
            res: list[str] = []
            for item in value:
                s = str(item).strip().lower()
                if not s:
                    raise ValueError(
                        "google_oidc_allowed_hosted_domains cannot contain blank entries"
                    )
                res.append(s)
            return res
        if isinstance(value, str):
            val = value.strip()
            if not val:
                return []
            if val.startswith("[") and val.endswith("]"):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return cls.parse_google_oidc_allowed_hosted_domains(parsed)
                except json.JSONDecodeError:
                    pass
            parts = [item.strip().lower() for item in val.split(",") if item.strip()]
            return parts
        return []

    @field_validator("google_oidc_clock_skew_seconds", mode="before")
    @classmethod
    def validate_clock_skew(cls, value: Any) -> int:
        """Validate clock skew is non-negative and bounded (0 to 300 seconds)."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("google_oidc_clock_skew_seconds must be an integer") from exc
        if val < 0 or val > 300:
            raise ValueError("google_oidc_clock_skew_seconds must be between 0 and 300 seconds")
        return val

    @field_validator("google_oidc_http_timeout_seconds", mode="before")
    @classmethod
    def validate_http_timeout(cls, value: Any) -> float:
        """Validate HTTP timeout is positive and bounded (0.1 to 60.0 seconds)."""
        try:
            val = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("google_oidc_http_timeout_seconds must be a float") from exc
        if val <= 0 or val > 60.0:
            raise ValueError(
                "google_oidc_http_timeout_seconds must be between 0.1 and 60.0 seconds"
            )
        return val

    @field_validator("google_oidc_cache_fallback_seconds", mode="before")
    @classmethod
    def validate_cache_fallback(cls, value: Any) -> int:
        """Validate cache fallback TTL is positive and bounded (1 to 604800 seconds)."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("google_oidc_cache_fallback_seconds must be an integer") from exc
        if val <= 0 or val > 604800:
            raise ValueError(
                "google_oidc_cache_fallback_seconds must be between 1 and 604800 seconds"
            )
        return val

    @field_validator("google_oidc_cache_max_seconds", mode="before")
    @classmethod
    def validate_cache_max(cls, value: Any) -> int:
        """Validate cache max TTL is positive and bounded (1 to 2592000 seconds)."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("google_oidc_cache_max_seconds must be an integer") from exc
        if val <= 0 or val > 2592000:
            raise ValueError("google_oidc_cache_max_seconds must be between 1 and 2592000 seconds")
        return val

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: Any) -> SecretStr | None:
        """Parse and normalize database URL.

        Treats empty string, placeholders, and syntax examples as None.
        """
        if value is None:
            return None
        if isinstance(value, SecretStr):
            raw = value.get_secret_value().strip()
        elif isinstance(value, str):
            raw = value.strip()
        else:
            raise ValueError(
                f"Invalid database_url type: {type(value).__name__}; must be string or SecretStr"
            )
        if not raw or raw.startswith("<") or "placeholder" in raw.lower():
            return None
        return SecretStr(raw)

    @field_validator("database_pool_size", mode="before")
    @classmethod
    def validate_database_pool_size(cls, value: Any) -> int:
        """Validate connection pool size is an integer between 1 and 100."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("database_pool_size must be an integer") from exc
        if val < 1 or val > 100:
            raise ValueError("database_pool_size must be between 1 and 100")
        return val

    @field_validator("database_max_overflow", mode="before")
    @classmethod
    def validate_database_max_overflow(cls, value: Any) -> int:
        """Validate max overflow is an integer between 0 and 100."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("database_max_overflow must be an integer") from exc
        if val < 0 or val > 100:
            raise ValueError("database_max_overflow must be between 0 and 100")
        return val

    @field_validator("database_pool_timeout_seconds", mode="before")
    @classmethod
    def validate_database_pool_timeout(cls, value: Any) -> float:
        """Validate pool timeout is a positive float between 0.1 and 300.0 seconds."""
        try:
            val = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("database_pool_timeout_seconds must be a float") from exc
        if val <= 0.0 or val > 300.0:
            raise ValueError("database_pool_timeout_seconds must be between 0.1 and 300.0 seconds")
        return val

    @field_validator("database_pool_recycle_seconds", mode="before")
    @classmethod
    def validate_database_pool_recycle(cls, value: Any) -> int:
        """Validate pool recycle is -1 (disabled) or between 1 and 86400 seconds."""
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("database_pool_recycle_seconds must be an integer") from exc
        if val != -1 and (val < 1 or val > 86400):
            raise ValueError(
                "database_pool_recycle_seconds must be -1 (disabled) or between 1 and 86400 seconds"
            )
        return val

    @field_validator("database_connect_timeout_seconds", mode="before")
    @classmethod
    def validate_database_connect_timeout(cls, value: Any) -> float:
        """Validate connect timeout is a positive float between 0.1 and 300.0 seconds."""
        try:
            val = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("database_connect_timeout_seconds must be a float") from exc
        if val <= 0.0 or val > 300.0:
            raise ValueError(
                "database_connect_timeout_seconds must be between 0.1 and 300.0 seconds"
            )
        return val

    @field_validator("llm_timeout_seconds", mode="before")
    @classmethod
    def validate_llm_timeout(cls, value: Any) -> float | None:
        """Validate LLM timeout is between 0.1 and 300.0 seconds if configured."""
        if value is None:
            return None
        try:
            val = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("llm_timeout_seconds must be a float") from exc
        if val <= 0.0 or val > 300.0:
            raise ValueError("llm_timeout_seconds must be between 0.1 and 300.0 seconds")
        return val

    @field_validator("llm_max_retries", mode="before")
    @classmethod
    def validate_llm_max_retries(cls, value: Any) -> int | None:
        """Validate LLM max retries is between 0 and 10 if configured."""
        if value is None:
            return None
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("llm_max_retries must be an integer") from exc
        if val < 0 or val > 10:
            raise ValueError("llm_max_retries must be between 0 and 10")
        return val

    @field_validator("llm_max_repair_attempts", mode="before")
    @classmethod
    def validate_llm_max_repair_attempts(cls, value: Any) -> int | None:
        """Validate LLM max repair attempts is between 0 and 5 if configured."""
        if value is None:
            return None
        try:
            val = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("llm_max_repair_attempts must be an integer") from exc
        if val < 0 or val > 5:
            raise ValueError("llm_max_repair_attempts must be between 0 and 5")
        return val

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def validate_llm_api_key(cls, value: Any) -> SecretStr | None:
        """Parse LLM API key, treating empty strings and placeholders as None."""
        if value is None:
            return None
        if isinstance(value, SecretStr):
            raw = value.get_secret_value().strip()
        elif isinstance(value, str):
            raw = value.strip()
        else:
            raise ValueError(
                f"Invalid llm_api_key type: {type(value).__name__}; must be string or SecretStr"
            )
        if not raw or raw.startswith("<") or "placeholder" in raw.lower():
            return None
        return SecretStr(raw)

    @model_validator(mode="after")
    def validate_cache_bounds(self) -> "Settings":
        """Ensure maximum cache TTL is greater than or equal to fallback cache TTL."""
        if self.google_oidc_cache_max_seconds < self.google_oidc_cache_fallback_seconds:
            max_sec = self.google_oidc_cache_max_seconds
            fb_sec = self.google_oidc_cache_fallback_seconds
            raise ValueError(
                f"google_oidc_cache_max_seconds ({max_sec}) "
                f"must be >= google_oidc_cache_fallback_seconds ({fb_sec})"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance.

    Guarantees deterministic single-instance retrieval without disk/env re-parsing per request.
    """
    return Settings()
