"""Deterministic Role-Based Access Control (RBAC) and FastAPI authorization dependencies.

Provides reusable dependency hooks for requiring authenticated principals, roles,
and permissions with clean 401 Unauthorized vs 403 Forbidden semantics.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status

from app.core.auth.context import get_current_principal
from app.core.auth.models import AuthenticatedPrincipal


async def require_authenticated_principal() -> AuthenticatedPrincipal:
    """FastAPI dependency requiring an authenticated principal in request context.

    Raises HTTP 401 Unauthorized if no principal is present in the active context.
    """
    principal = get_current_principal()
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return principal


# Reusable alias for FastAPI dependency injection
get_current_authenticated_principal = require_authenticated_principal


def require_roles(
    *roles: str,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Dependency factory requiring the principal to possess all specified roles.

    Raises HTTP 401 if unauthenticated, or HTTP 403 if roles are insufficient.
    """

    async def _role_verifier(
        principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if not principal.has_all_roles(*roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role authorization",
            )
        return principal

    return _role_verifier


def require_any_role(
    *roles: str,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Dependency factory requiring the principal to possess at least one of the specified roles."""

    async def _any_role_verifier(
        principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if not principal.has_any_role(*roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role authorization",
            )
        return principal

    return _any_role_verifier


def require_permissions(
    *permissions: str,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Dependency factory requiring the principal to possess all specified permissions.

    Raises HTTP 401 if unauthenticated, or HTTP 403 if permissions are insufficient.
    """

    async def _permission_verifier(
        principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if not principal.has_all_permissions(*permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permission authorization",
            )
        return principal

    return _permission_verifier


def require_any_permission(
    *permissions: str,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Dependency factory requiring principal to possess at least one specified permission."""

    async def _any_permission_verifier(
        principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if not principal.has_any_permission(*permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permission authorization",
            )
        return principal

    return _any_permission_verifier
