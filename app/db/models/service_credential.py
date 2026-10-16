"""SQLAlchemy 2.x mapped model for persistent Service API Key credentials (Phase 8D).

Implements canonical System of Record persistence for service API key credentials:
- service_api_credentials (durable, tenant-scoped, cryptographic digest only)

Invariants:
- All credentials are strictly tenant-scoped via tenant_id.
- Application-generated canonical UUIDv4 primary keys.
- credential_id is globally unique and formatted (1..64 chars).
- Plaintext secrets are NEVER stored. Only one-way SHA-256 digests (64 hex chars) are persisted.
- Roles and permissions are stored for persistent RBAC resolution across process restarts.
- Lifecycle states: enabled/disabled, optional not_before/expires_at, revocation audit metadata.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.auth.service_api_key import ServiceApiKeyCredentialRecord
from app.db.base import Base


class ServiceApiKeyCredentialRecordModel(Base):
    """Canonical persistent service credential entity mapped to PostgreSQL
    service_api_credentials.
    """

    __tablename__ = "service_api_credentials"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            name="uq_service_api_credentials_credential_id",
        ),
        CheckConstraint(
            "length(trim(credential_id)) > 0",
            name="ck_service_cred_id_not_blank",
        ),
        CheckConstraint(
            "length(trim(tenant_id)) > 0",
            name="ck_service_cred_tenant_not_blank",
        ),
        CheckConstraint(
            "length(secret_digest) = 64",
            name="ck_service_cred_secret_digest_len",
        ),
        Index("ix_service_api_credentials_tenant_id", "tenant_id"),
        Index("ix_service_api_credentials_credential_id", "credential_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    credential_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    service_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    secret_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    roles: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(sa.JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    permissions: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(sa.JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    issuer: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default="e7-service-key",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def to_credential_record(self) -> ServiceApiKeyCredentialRecord:
        """Convert database record to immutable ServiceApiKeyCredentialRecord."""
        return ServiceApiKeyCredentialRecord(
            credential_id=self.credential_id,
            service_subject=self.service_subject,
            secret_digest=self.secret_digest,
            enabled=self.enabled,
            not_before=self.not_before,
            expires_at=self.expires_at,
            issuer=self.issuer or "e7-service-key",
        )

    def to_authenticated_principal(self) -> AuthenticatedPrincipal:
        """Convert database record to canonical internal AuthenticatedPrincipal."""
        return AuthenticatedPrincipal(
            principal_id=f"svc:{self.tenant_id}:{self.credential_id}",
            principal_type=PrincipalType.SERVICE,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            tenant_id=self.tenant_id,
            roles=frozenset(self.roles or []),
            permissions=frozenset(self.permissions or []),
            issuer=self.issuer or "e7-service-key",
            external_subject=self.service_subject,
        )


__all__ = [
    "ServiceApiKeyCredentialRecordModel",
]
