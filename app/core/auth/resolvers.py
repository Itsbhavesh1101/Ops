"""Provider-neutral identity resolver interfaces and protocols.

Defines asynchronous contracts for mapping cryptographically verified external identities
(OIDC tokens or service credentials) into canonical internal AuthenticatedPrincipal records.
"""

from typing import Any, Protocol, runtime_checkable

import structlog

from app.core.auth.models import (
    AuthenticatedPrincipal,
    VerifiedOidcIdentity,
    VerifiedServiceCredentialIdentity,
)

logger = structlog.get_logger(__name__)


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


CANONICAL_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "planner": frozenset({
        "goal:create",
        "plan:create",
        "plan:validate",
        "plan:submit",
        "plan:edit",
        "task:read",
    }),
    "approver": frozenset({
        "plan:approve",
        "schedule:publish",
        "workflow:approve",
        "task:read",
    }),
    "chairs": frozenset({
        "workflow:approve",
        "task:read",
    }),
    "academic_dean": frozenset({
        "workflow:approve",
        "task:read",
    }),
    "scheduler": frozenset({
        "schedule:solve",
        "schedule:publish",
        "schedule:repair",
        "task:execute",
        "task:read",
    }),
    "operator": frozenset({
        "task:execute",
        "task:read",
        "schedule:repair",
    }),
    "tenant_admin": frozenset({"*"}),
    "admin": frozenset({"*"}),
    "auditor": frozenset({
        "audit:read",
        "analytics:read",
        "plan:read",
        "schedule:read",
    }),
    "viewer": frozenset({
        "plan:read",
        "schedule:read",
    }),
}


class DefaultIdentityResolver:
    """Canonical in-memory identity resolver bridging credentials to AuthenticatedPrincipals."""

    def __init__(
        self,
        oidc_mappings: dict[str, AuthenticatedPrincipal] | None = None,
        service_mappings: dict[str, AuthenticatedPrincipal] | None = None,
        default_tenant_id: str | None = None,
    ) -> None:
        self._oidc_mappings = dict(oidc_mappings or {})
        self._service_mappings = dict(service_mappings or {})
        self._default_tenant_id = default_tenant_id

    def register_oidc_mapping(
        self, subject_or_email: str, principal: AuthenticatedPrincipal
    ) -> None:
        """Register deterministic principal mapping for an OIDC subject or email."""
        key = subject_or_email.strip()
        existing = self._oidc_mappings.get(key)
        if existing is not None:
            if (
                existing.principal_id != principal.principal_id
                or existing.tenant_id != principal.tenant_id
                or existing.roles != principal.roles
                or (
                    existing.external_subject
                    and principal.external_subject
                    and existing.external_subject != principal.external_subject
                )
            ):
                raise ValueError(
                    f"Conflicting OIDC mapping detected for '{key}': "
                    f"existing principal '{existing.principal_id}' (tenant: {existing.tenant_id}) "
                    f"conflicts with incoming principal '{principal.principal_id}' "
                    f"(tenant: {principal.tenant_id})"
                )
        self._oidc_mappings[key] = principal

    def register_service_mapping(
        self, credential_id: str, principal: AuthenticatedPrincipal
    ) -> None:
        """Register deterministic principal mapping for a service credential ID."""
        self._service_mappings[credential_id] = principal

    async def resolve_oidc_identity(
        self, identity: VerifiedOidcIdentity
    ) -> AuthenticatedPrincipal | None:
        """Resolve verified OIDC identity to an internal principal."""
        from app.core.auth.models import AuthenticationMethod, PrincipalType

        # 1. Primary lookup: IMMUTABLE SUBJECT CLAIM takes highest precedence
        # Google Workspace / Cloud Identity stable identifier is the OIDC 'sub' claim.
        # This prevents identity reassignment or lockout when user email changes.
        if identity.subject in self._oidc_mappings:
            principal = self._oidc_mappings[identity.subject]
            # Issuer verification: ensure issuer matches if explicitly recorded on principal
            if principal.issuer and identity.issuer != principal.issuer:
                logger.warning(
                    "oidc_mapping_issuer_mismatch",
                    expected_issuer=principal.issuer,
                    actual_issuer=identity.issuer,
                    subject=identity.subject,
                )
                return None
            return principal

        # 2. Secondary/Bootstrap lookup by email (only if subject is not yet registered)
        if identity.email and identity.email in self._oidc_mappings:
            principal = self._oidc_mappings[identity.email]
            # Conflicting identity protection: if principal already recorded a different
            # external subject, reject (preventing identity hijacking via email recycling)
            if (
                principal.external_subject
                and principal.external_subject != identity.subject
            ):
                logger.warning(
                    "oidc_conflicting_identity_rejected",
                    mapped_subject=principal.external_subject,
                    incoming_subject=identity.subject,
                    email=identity.email,
                    reason="email_claimed_by_different_subject",
                )
                return None

            # Issuer verification
            if principal.issuer and identity.issuer != principal.issuer:
                logger.warning(
                    "oidc_mapping_issuer_mismatch",
                    expected_issuer=principal.issuer,
                    actual_issuer=identity.issuer,
                    email=identity.email,
                )
                return None

            return principal

        # 3. External Google Workspace / Cloud Identity tokens MUST NOT automatically derive
        # tenant authority or roles through email username heuristics or external claims alone.
        # Unmapped Google identities strictly fail closed to preserve internal governance.
        if identity.issuer in ("https://accounts.google.com", "accounts.google.com"):
            logger.warning(
                "google_identity_unmapped_fail_closed",
                subject=identity.subject,
                email=identity.email,
                reason="external_google_identity_not_mapped_to_internal_principal",
            )
            return None

        # 3. In demo environments without default tenant configured, fail closed!
        if not self._default_tenant_id:
            logger.warning(
                "oidc_tenant_mapping_failed_closed",
                subject=identity.subject,
                email=identity.email,
                reason="no_default_tenant_configured_and_no_explicit_mapping",
            )
            return None

        # 4. Local demonstration heuristic: derive roles from verified identity email or subject
        candidate_roles: set[str] = set()
        username = (
            (identity.email.split("@")[0] if identity.email else "")
            or identity.subject
        ).lower()

        if "planner" in username:
            candidate_roles.add("planner")
        elif "approver" in username:
            candidate_roles.update(["approver", "chairs", "academic_dean"])
        elif "scheduler" in username:
            candidate_roles.update(["scheduler", "operator"])
        elif "admin" in username or "tenant_admin" in username:
            candidate_roles.add("tenant_admin")
        elif "auditor" in username:
            candidate_roles.add("auditor")

        # Normalize known canonical roles
        canonical_roles = set()
        for r in candidate_roles:
            r_clean = r.lower().strip()
            if r_clean in CANONICAL_ROLE_PERMISSIONS:
                canonical_roles.add(r_clean)
            elif r_clean in ("admin", "tenant-admin"):
                canonical_roles.add("tenant_admin")

        if not canonical_roles:
            return None

        # Derive canonical permissions
        canonical_permissions: set[str] = set()
        for role in canonical_roles:
            canonical_permissions.update(CANONICAL_ROLE_PERMISSIONS.get(role, set()))

        principal_id = identity.email or identity.subject
        return AuthenticatedPrincipal(
            principal_id=principal_id,
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=self._default_tenant_id,
            roles=frozenset(canonical_roles),
            permissions=frozenset(canonical_permissions),
            issuer=identity.issuer,
            external_subject=identity.subject,
        )

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
        default_tenant_id: str | None = None,
    ) -> None:
        super().__init__(
            oidc_mappings=oidc_mappings,
            service_mappings=service_mappings,
            default_tenant_id=default_tenant_id,
        )
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
