"""Canonical NotificationCommand models based on Phase-0 E-8 contract.

Adheres strictly to docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md and
docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.json.
Enforces transport-neutral, privacy-preserving notification commands.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TriggerKind(StrEnum):
    """Classification of trigger event / condition."""

    SOURCE_VERIFIED_EVENT = "SOURCE_VERIFIED_EVENT"
    SOURCE_BACKED_BUSINESS_CONDITION = "SOURCE_BACKED_BUSINESS_CONDITION"


class NotificationTrigger(BaseModel):
    """Provenance metadata detailing operational cause of notification command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_kind: TriggerKind
    source_event_name: str | None = None
    business_condition: str | None = None
    source_entity_reference: str | None = None


class NotificationCommand(BaseModel):
    """Canonical provider-neutral NotificationCommand contract for E-8 engagement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: str = Field(default="1.0.0")
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_reference: str
    causation_reference: str | None = None
    organization_scope_reference: str  # tenant_id
    trigger: NotificationTrigger
    recipient_references: list[str]
    notification_context: dict[str, Any]
    requested_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


__all__ = [
    "NotificationCommand",
    "NotificationTrigger",
    "TriggerKind",
]
