"""Provider-neutral identity resolver interfaces and protocols.

Defines asynchronous contracts for mapping cryptographically verified external identities
(OIDC tokens or service credentials) into canonical internal AuthenticatedPrincipal records.
"""

from typing import Any, Protocol, runtime_checkable

from app.core.auth.models import (
    AuthenticatedPrincipal,
    VerifiedOidcIdentity,
    VerifiedServiceCredentialIdentity,
)


@runtime_checkable
class ExternalIdentityResolver(Protocol):
    """Protocol for resolving verified external OIDC identities to canonical E-7 principals.

    Implementations in later tasks may query PostgreSQL or an authoritative identity registry
    to locate or provision the canonical user and resolve tenant membership, roles, and permissions.
    """

    async def resolve_oidc_identity(
        self, identity: VerifiedOidcIdentity
    ) -> AuthenticatedPrincipal | None:
        """Resolve a verified OIDC identity to an AuthenticatedPrincipal, or None."""
        ...


@runtime_checkable
class ServiceIdentityResolver(Protocol):
    """Protocol for resolving verified service credentials to canonical E-7 service principals.

    Implementations in later tasks will query approved service identity configurations or
    PostgreSQL without exposing raw API keys.
    """

    async def resolve_service_identity(
        self, identity: VerifiedServiceCredentialIdentity
    ) -> AuthenticatedPrincipal | None:
        """Resolve verified service credential to an AuthenticatedPrincipal, or None."""
        ...


class DefaultIdentityResolver:
    """Canonical in-memory identity resolver bridging credentials to AuthenticatedPrincipals."""

    def __init__(
        self,
        oidc_mappings: dict[str, AuthenticatedPrincipal] | None = None,
        service_mappings: dict[str, AuthenticatedPrincipal] | None = None,
    ) -> None:
        self._oidc_mappings = dict(oidc_mappings or {})
        self._service_mappings = dict(service_mappings or {})

    def register_oidc_mapping(
        self, subject_or_email: str, principal: AuthenticatedPrincipal
    ) -> None:
        """Register deterministic principal mapping for an OIDC subject or email."""
        self._oidc_mappings[subject_or_email] = principal

    def register_service_mapping(
        self, credential_id: str, principal: AuthenticatedPrincipal
    ) -> None:
        """Register deterministic principal mapping for a service credential ID."""
        self._service_mappings[credential_id] = principal

    async def resolve_oidc_identity(
        self, identity: VerifiedOidcIdentity
    ) -> AuthenticatedPrincipal | None:
        """Resolve verified OIDC identity to an internal principal."""
        if identity.email and identity.email in self._oidc_mappings:
            return self._oidc_mappings[identity.email]
        if identity.subject in self._oidc_mappings:
            return self._oidc_mappings[identity.subject]
        return None

    async def resolve_service_identity(
        self, identity: VerifiedServiceCredentialIdentity
    ) -> AuthenticatedPrincipal | None:
        """Resolve verified service credential to an internal principal."""
        if identity.credential_id in self._service_mappings:
            return self._service_mappings[identity.credential_id]
        if identity.service_subject in self._service_mappings:
            return self._service_mappings[identity.service_subject]
        return None


class PostgresServiceIdentityResolver(ServiceIdentityResolver):
    """PostgreSQL-backed service identity resolver (Phase 8D REC-001).

    Resolves verified service credential identities into canonical AuthenticatedPrincipal
    instances by querying the persistent `service_api_credentials` table.
    Enforces strict tenant isolation by binding the principal strictly to the record's tenant_id.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def resolve_service_identity(
        self, identity: VerifiedServiceCredentialIdentity
    ) -> AuthenticatedPrincipal | None:
        from sqlalchemy import select

        from app.db.models.service_credential import ServiceApiKeyCredentialRecordModel

        async with self._session_factory() as session:
            stmt = select(ServiceApiKeyCredentialRecordModel).where(
                ServiceApiKeyCredentialRecordModel.credential_id == identity.credential_id
            )
            result = await session.execute(stmt)
            model = result.scalars().first()
            if model is None or not model.enabled:
                return None
            return model.to_authenticated_principal()


class CompositeIdentityResolver(DefaultIdentityResolver):
    """Composite identity resolver checking in-memory mappings first, then PostgreSQL.

    Preserves fast in-memory registration for test suites while enabling durable,
    persistent service identity resolution across process restarts in production.
    """

    def __init__(
        self,
        postgres_resolver: PostgresServiceIdentityResolver | None = None,
        oidc_mappings: dict[str, AuthenticatedPrincipal] | None = None,
        service_mappings: dict[str, AuthenticatedPrincipal] | None = None,
    ) -> None:
        super().__init__(oidc_mappings=oidc_mappings, service_mappings=service_mappings)
        self._postgres_resolver = postgres_resolver

    async def resolve_service_identity(
        self, identity: VerifiedServiceCredentialIdentity
    ) -> AuthenticatedPrincipal | None:
        mem_principal = await super().resolve_service_identity(identity)
        if mem_principal is not None:
            return mem_principal
        if self._postgres_resolver is not None:
            return await self._postgres_resolver.resolve_service_identity(identity)
        return None


__all__ = [
    "CompositeIdentityResolver",
    "DefaultIdentityResolver",
    "ExternalIdentityResolver",
    "PostgresServiceIdentityResolver",
    "ServiceIdentityResolver",
]
