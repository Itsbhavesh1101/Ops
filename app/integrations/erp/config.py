"""Configuration for MySQL ERP/SIS adapter.

Part of Phase 7B Real ERP/SIS Adapter.
"""

import json

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MySqlErpSettings(BaseSettings):
    """Configuration for MySQL iredu ERP adapter."""

    model_config = SettingsConfigDict(
        env_prefix="E7_MYSQL_ERP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(default="", description="MySQL server hostname")
    port: int = Field(default=3306, description="MySQL server port")
    user: str = Field(default="", description="MySQL read-only username")
    password: SecretStr = Field(default=SecretStr(""), description="MySQL read-only password")
    database: str = Field(default="iredu", description="Upstream ERP database name")
    single_tenant_id: str = Field(
        default="",
        description=(
            "Canonical E-7 tenant_id bound to this dedicated single-tenant database instance"
        ),
    )
    enabled: bool = Field(default=False, description="Whether live MySQL ERP extraction is enabled")
    timeout_seconds: float = Field(
        default=5.0, description="Query and connection timeout in seconds"
    )
    staleness_threshold_seconds: float = Field(
        default=3600.0, description="Duration in seconds before cached master data is marked stale"
    )
    approved_tenant_scopes_json: str = Field(
        default="{}",
        description="JSON dict mapping E-7 tenant_id to approved upstream organization scopes",
    )

    @property
    def approved_tenant_scopes(self) -> dict[str, str]:
        """Parsed dictionary of approved tenant-to-organization scopes."""
        try:
            parsed = json.loads(self.approved_tenant_scopes_json)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
            return {}
        except Exception:
            return {}

    @model_validator(mode="after")
    def validate_fail_closed_credentials(self) -> "MySqlErpSettings":
        """Fail closed if live MySQL ERP extraction is enabled but credentials are missing."""
        if self.enabled:
            if not self.host.strip():
                raise ValueError("E7_MYSQL_ERP_ENABLED is true, but E7_MYSQL_ERP_HOST is empty.")
            if not self.user.strip():
                raise ValueError("E7_MYSQL_ERP_ENABLED is true, but E7_MYSQL_ERP_USER is empty.")
        return self


__all__ = ["MySqlErpSettings"]
