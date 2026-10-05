"""Provider-neutral identity resolver interfaces and protocols.

Defines asynchronous contracts for mapping cryptographically verified external identities
(OIDC tokens or service credentials) into canonical internal AuthenticatedPrincipal records.
"""

from typing import Protocol, runtime_checkable

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
