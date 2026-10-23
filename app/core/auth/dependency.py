"""HTTP request authentication and canonical tenant context dependencies.

Provides:
- authenticate_request: FastAPI dependency verifying OIDC bearer tokens or Service API keys.
- require_trusted_tenant_context: FastAPI dependency extracting TenantContext strictly
  from authenticated principal.
- require_trusted_tenant_id: FastAPI dependency extracting tenant_id string strictly
  from authenticated principal.
- HttpAuthenticator: configurable runtime authenticator bridging verifiers and resolvers.
"""


import json
from typing import Any

import structlog
from fastapi import Depends, Request

from app.api.errors import ApiError
from app.core.auth.context import set_current_principal
from app.core.auth.google_oidc import (
    GoogleOidcVerificationError,
    GoogleOidcVerifier,
)
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
)
from app.core.auth.oidc import (
    OidcVerificationError,
    OidcVerifier,
)
from app.core.auth.resolvers import (
    CANONICAL_ROLE_PERMISSIONS,
    CompositeIdentityResolver,
    DefaultIdentityResolver,
    ExternalIdentityResolver,
    PostgresServiceIdentityResolver,
    ServiceIdentityResolver,
)
from app.core.auth.service_api_key import (
    CompositeServiceApiKeyCredentialStore,
    PostgresServiceApiKeyCredentialStore,
    ServiceApiKeyCredentialStore,
    ServiceApiKeyVerificationError,
    ServiceApiKeyVerifier,
)
from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class HttpAuthenticator:
    """Configurable HTTP authenticator bridging technical verifiers to internal principals."""

    def __init__(
        self,
        settings: Settings | None = None,
        oidc_verifier: Any | None = None,
        service_key_verifier: ServiceApiKeyVerifier | None = None,
        external_identity_resolver: ExternalIdentityResolver | None = None,
        service_identity_resolver: ServiceIdentityResolver | None = None,
        credential_store: ServiceApiKeyCredentialStore | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if oidc_verifier is not None:
            self._oidc_verifier = oidc_verifier
        elif self._settings.oidc_issuer_url or self._settings.oidc_provider_name.lower() in (
            "keycloak",
            "generic",
        ):
            if self._settings.environment in ("production", "staging"):
                env = self._settings.environment
                prov = self._settings.oidc_provider_name
                raise ValueError(
                    f"Production/staging environment ({env}) prohibits demonstration "
                    f"identity provider '{prov}'. "
                    "Google Workspace / Cloud Identity OIDC is strictly required."
                )
            self._oidc_verifier = OidcVerifier.from_settings(self._settings)
        else:
            self._oidc_verifier = GoogleOidcVerifier.from_settings(self._settings)

        self._credential_store: ServiceApiKeyCredentialStore
        self._resolver: DefaultIdentityResolver
        default_tenant = getattr(self._settings, "oidc_default_tenant_id", None)

        # 1. Resolve credential store and identity resolvers
        if credential_store is None:
            postgres_store: PostgresServiceApiKeyCredentialStore | None = None
            postgres_resolver: PostgresServiceIdentityResolver | None = None
            try:
                from app.db.engine import DatabaseManager

                db_manager = DatabaseManager(settings=self._settings)
                postgres_store = PostgresServiceApiKeyCredentialStore(
                    session_factory=db_manager.session_factory
                )
                postgres_resolver = PostgresServiceIdentityResolver(
                    session_factory=db_manager.session_factory
                )
            except Exception:
                pass

            self._credential_store = CompositeServiceApiKeyCredentialStore(
                postgres_store=postgres_store
            )
            self._resolver = CompositeIdentityResolver(
                postgres_resolver=postgres_resolver,
                default_tenant_id=default_tenant,
            )
        else:
            self._credential_store = credential_store
            self._resolver = DefaultIdentityResolver(default_tenant_id=default_tenant)

        # 2. Register server-controlled OIDC user mappings from configuration if provided
        user_mappings_json = getattr(self._settings, "oidc_user_mappings_json", None)
        if user_mappings_json:
            self._load_oidc_user_mappings(user_mappings_json)

        self._service_key_verifier = service_key_verifier or ServiceApiKeyVerifier(
            credential_store=self._credential_store
        )
        self._external_identity_resolver = external_identity_resolver or self._resolver
        self._service_identity_resolver = service_identity_resolver or self._resolver

    def _load_oidc_user_mappings(self, raw_json: str) -> None:
        """Parse server-controlled JSON user roster and register authorized principals."""
        try:
            data = json.loads(raw_json)
        except Exception as exc:
            logger.error("failed_to_parse_oidc_user_mappings_json", error=str(exc))
            return

        if not isinstance(data, dict):
            return

        for identity_key, spec in data.items():
            if not isinstance(spec, dict) or not spec.get("enabled", True):
                continue  # Suspended or disabled users fail closed

            tenant_id = spec.get("tenant_id")
            if not tenant_id or not isinstance(tenant_id, str):
                continue

            raw_roles = spec.get("roles", ["viewer"])
            roles = frozenset(str(r).strip() for r in raw_roles if str(r).strip())

            perms: set[str] = set()
            for r in roles:
                perms.update(CANONICAL_ROLE_PERMISSIONS.get(r, frozenset()))
            if "permissions" in spec and isinstance(spec["permissions"], (list, set, tuple)):
                perms.update(str(p).strip() for p in spec["permissions"] if str(p).strip())

            # Determine subject and email
            subject = spec.get("subject")
            email = spec.get("email")
            clean_key = identity_key.strip()

            if not subject and "@" not in clean_key:
                subject = clean_key
            if not email and "@" in clean_key:
                email = clean_key

            principal_id = spec.get("principal_id") or email or subject or clean_key

            principal = AuthenticatedPrincipal(
                principal_id=principal_id,
                principal_type=PrincipalType.HUMAN,
                authentication_method=AuthenticationMethod.OIDC,
                tenant_id=tenant_id.strip(),
                roles=roles,
                permissions=frozenset(perms),
                issuer=spec.get("issuer"),
                external_subject=subject,
            )
            # Register under subject as primary external identity key
            if subject:
                self._resolver.register_oidc_mapping(subject.strip(), principal)
            # Register under email as descriptive/bootstrap lookup key
            if email and email.strip() != (subject or "").strip():
                self._resolver.register_oidc_mapping(email.strip(), principal)
            elif not subject:
                self._resolver.register_oidc_mapping(clean_key, principal)

    @property
    def identity_resolver(self) -> DefaultIdentityResolver:
        """Access the default identity resolver for registering mappings."""
        if isinstance(self._resolver, DefaultIdentityResolver):
            return self._resolver
        raise TypeError("Configured resolver is not an instance of DefaultIdentityResolver")

    @property
    def credential_store(self) -> ServiceApiKeyCredentialStore:
        """Access the service credential store for registering credentials."""
        return self._credential_store

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal:
        """Authenticate an incoming HTTP request using OIDC or Service API Key.

        Enforces:
        - Mutual exclusivity: requests providing BOTH Authorization and Service Key
          fail closed (400).
        - Credentials missing: requests with NEITHER credential fail closed (401).
        - Malformed Authorization header: fails closed (401).
        - Verified credentials with no mapped principal fail closed (401).
        - Successful verification binds principal to request ContextVar and structlog.
        """
        auth_header = request.headers.get("authorization")
        service_key_header_name = self._settings.service_api_key_header_name.lower()
        service_key_header = request.headers.get(service_key_header_name)

        # 1. Reject conflicting credentials
        if auth_header and service_key_header:
            raise ApiError(
                status_code=400,
                code="auth.conflicting_credentials",
                message=(
                    "Conflicting authentication credentials provided: "
                    "request must include either 'Authorization' or "
                    f"'{self._settings.service_api_key_header_name}', not both."
                ),
            )

        # 2. Reject missing credentials
        if not auth_header and not service_key_header:
            raise ApiError(
                status_code=401,
                code="auth.credentials_missing",
                message="Authentication required: missing Authorization or Service Key header.",
            )

        principal: AuthenticatedPrincipal | None = None

        # 3. Handle OIDC Bearer Token
        if auth_header:
            parts = auth_header.strip().split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
                raise ApiError(
                    status_code=401,
                    code="auth.invalid_authorization_header",
                    message="Malformed Authorization header: expected 'Bearer <token>'.",
                )
            raw_token = parts[1].strip()
            try:
                verified_oidc = await self._oidc_verifier.verify_id_token(raw_token)
            except (OidcVerificationError, GoogleOidcVerificationError) as exc:
                logger.warning("oidc_verification_failed", error=str(exc))
                raise ApiError(
                    status_code=401,
                    code="auth.token_invalid",
                    message=f"OIDC token verification failed: {exc}",
                ) from exc

            principal = await self._external_identity_resolver.resolve_oidc_identity(verified_oidc)
            if principal is None:
                raise ApiError(
                    status_code=401,
                    code="auth.principal_unresolved",
                    message="Verified OIDC identity is not mapped to an active E-7 principal.",
                )

        # 4. Handle Service API Key
        elif service_key_header:
            raw_key = service_key_header.strip()
            if not raw_key:
                raise ApiError(
                    status_code=401,
                    code="auth.invalid_service_key",
                    message="Service API key must not be blank.",
                )
            try:
                verified_service = await self._service_key_verifier.verify_service_key(raw_key)
            except ServiceApiKeyVerificationError as exc:
                logger.warning("service_key_verification_failed", error=str(exc))
                raise ApiError(
                    status_code=401,
                    code="auth.service_key_invalid",
                    message=f"Service API key verification failed: {exc}",
                ) from exc

            principal = await self._service_identity_resolver.resolve_service_identity(
                verified_service
            )
            if principal is None:
                raise ApiError(
                    status_code=401,
                    code="auth.principal_unresolved",
                    message="Verified service credential is not mapped to an active E-7 principal.",
                )

        if principal is None:
            raise ApiError(
                status_code=401,
                code="auth.unauthorized",
                message="Authentication failed.",
            )

        # 5. Bind authenticated principal to context
        set_current_principal(principal)
        structlog.contextvars.bind_contextvars(
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
        )

        return principal

    __call__ = authenticate


# Process-level singleton authenticator
_default_authenticator: HttpAuthenticator | None = None


def get_default_authenticator() -> HttpAuthenticator:
    """Return process-level singleton HttpAuthenticator."""
    global _default_authenticator
    if _default_authenticator is None:
        _default_authenticator = HttpAuthenticator()
    return _default_authenticator


async def authenticate_request(
    request: Request,
    authenticator: HttpAuthenticator = Depends(get_default_authenticator),
) -> AuthenticatedPrincipal:
    """FastAPI dependency authenticating incoming HTTP requests."""
    return await authenticator.authenticate(request)


async def require_trusted_tenant_context(
    principal: AuthenticatedPrincipal = Depends(authenticate_request),
) -> TenantContext:
    """FastAPI dependency deriving TenantContext strictly from authenticated principal."""
    return principal.tenant_context


async def require_trusted_tenant_id(
    principal: AuthenticatedPrincipal = Depends(authenticate_request),
) -> str:
    """FastAPI dependency deriving canonical tenant_id strictly from authenticated principal."""
    return principal.tenant_id


__all__ = [
    "HttpAuthenticator",
    "authenticate_request",
    "get_default_authenticator",
    "require_trusted_tenant_context",
    "require_trusted_tenant_id",
]
