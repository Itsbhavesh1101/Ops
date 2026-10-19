"""Authentication inspection and frontend identity API (Phase 10 Demo-First).

Provides:
- GET /auth/me: Safe frontend-facing identity representation of the verified principal.

Security Invariants:
- Strict dependency on canonical authenticate_request.
- Zero mock or unauthenticated bypasses.
- Safe allowlist: never returns raw bearer tokens, passwords, or service credentials.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from app.api.errors import ApiError, ApiErrorResponse
from app.core.auth.dependency import authenticate_request
from app.core.auth.models import AuthenticatedPrincipal


class AuthMeResponse(BaseModel):
    """Safe public representation of the authenticated principal's identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]
    principal_type: str
    authentication_method: str
    email: str | None = None
    display_name: str | None = None
    issuer: str | None = None


def create_auth_router(
    *,
    principal_dependency: Callable[..., Any] = authenticate_request,
) -> APIRouter:
    """Create decoupled router exposing authenticated identity inspection."""
    router = APIRouter(tags=["Authentication"])

    @router.get(
        "/auth/me",
        response_model=AuthMeResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Current Authenticated Principal Identity",
        responses={
            200: {
                "model": AuthMeResponse,
                "description": "Safe authenticated identity.",
            },
            401: {
                "model": ApiErrorResponse,
                "description": "Unauthenticated request.",
            },
        },
    )
    async def get_current_user(
        principal: Any = Depends(principal_dependency),
    ) -> AuthMeResponse:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise ApiError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="auth.unauthenticated",
                message="Valid authentication credentials required.",
            )

        email: str | None = None
        if principal.external_subject and "@" in principal.external_subject:
            email = principal.external_subject
        elif "@" in principal.principal_id:
            email = principal.principal_id

        display_name: str | None = None
        if email:
            display_name = email.split("@")[0].replace(".", " ").title()
        else:
            display_name = principal.principal_id

        return AuthMeResponse(
            principal_id=principal.principal_id,
            tenant_id=principal.tenant_id,
            roles=sorted(list(principal.roles)),
            permissions=sorted(list(principal.permissions)),
            principal_type=principal.principal_type.value,
            authentication_method=principal.authentication_method.value,
            email=email,
            display_name=display_name,
            issuer=principal.issuer,
        )

    return router
