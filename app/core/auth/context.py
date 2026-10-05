"""Request-scoped security context using ContextVar.

Maintains an asynchronous, thread-safe security context isolating authenticated
principals and tenant scope per request/task.
"""

from contextvars import ContextVar, Token

from app.core.auth.models import AuthenticatedPrincipal, TenantContext

# Dedicated security ContextVar completely separated from correlation_id_ctx
_current_principal_ctx: ContextVar[AuthenticatedPrincipal | None] = ContextVar(
    "current_principal", default=None
)


def set_current_principal(
    principal: AuthenticatedPrincipal,
) -> Token[AuthenticatedPrincipal | None]:
    """Bind an authenticated principal to the active request context."""
    return _current_principal_ctx.set(principal)


def get_current_principal() -> AuthenticatedPrincipal | None:
    """Retrieve the active authenticated principal from context, or None if unauthenticated."""
    return _current_principal_ctx.get()


def reset_current_principal(token: Token[AuthenticatedPrincipal | None]) -> None:
    """Reset the current principal context using the token obtained from set_current_principal."""
    _current_principal_ctx.reset(token)


def get_current_tenant_id() -> str | None:
    """Retrieve the canonical tenant_id from the active authenticated principal, or None."""
    principal = get_current_principal()
    return principal.tenant_id if principal else None


def get_current_tenant_context() -> TenantContext | None:
    """Retrieve the canonical TenantContext from the active authenticated principal, or None."""
    principal = get_current_principal()
    return principal.tenant_context if principal else None
