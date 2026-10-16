"""Calendar Integration API endpoints.

Part of Phase 7C Real Calendar Provider Adapter.
Mounts:
- POST /api/v1/integrations/calendar/google/webhook
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.db.engine import DatabaseManager
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.inbound import InboundCalendarSyncService
from app.integrations.calendar.webhook import GoogleCalendarWebhookHandler

_db_manager: DatabaseManager | None = None


def get_default_db_manager() -> DatabaseManager:
    """Return singleton DatabaseManager for API runtime session provisioning."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped AsyncSession."""
    db = get_default_db_manager()
    async with db.session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


class WebhookResponse(BaseModel):
    """Response payload for Google Calendar push notification callback."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    message: str
    channel_id: str


def create_calendar_router(
    session_dependency: Callable[..., Any] = get_db_session,
    webhook_handler: GoogleCalendarWebhookHandler | None = None,
    adapter: CalendarAdapter | None = None,
    sync_service: InboundCalendarSyncService | None = None,
) -> APIRouter:
    """Create APIRouter for Google Calendar push webhooks."""
    router = APIRouter()
    handler = webhook_handler or GoogleCalendarWebhookHandler()

    @router.post(
        "/google/webhook",
        response_model=WebhookResponse,
        status_code=status.HTTP_200_OK,
        summary="Google Calendar Push Notification Callback",
    )
    async def google_calendar_webhook(
        request: Request,
        session: AsyncSession = Depends(session_dependency),
    ) -> WebhookResponse:
        """Handle incoming Google Calendar push notification.

        Google push notifications are a SIGNAL TO SYNC.
        Headers are verified, channel is resolved from durable state,
        durable replay protection is enforced, and incremental synchronization
        is triggered.
        """
        headers = dict(request.headers)
        result = await handler.handle_durable_webhook(
            session=session,
            headers=headers,
            adapter=adapter,
            sync_service=sync_service,
        )

        if result.status == "REJECTED":
            msg_lower = result.message.lower()
            if "unknown" in msg_lower or "inactive" in msg_lower:
                raise ApiError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="calendar.channel_not_found",
                    message=result.message,
                )
            if "missing channel token" in msg_lower:
                raise ApiError(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    code="calendar.token_missing",
                    message=result.message,
                )
            if "token verification failed" in msg_lower:
                raise ApiError(
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="calendar.token_mismatch",
                    message=result.message,
                )
            if "resource id mismatch" in msg_lower:
                raise ApiError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="calendar.resource_mismatch",
                    message=result.message,
                )
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="calendar.invalid_header",
                message=result.message,
            )

        return WebhookResponse(
            status=result.status,
            message=result.message,
            channel_id=result.channel_id,
        )

    return router


def register_calendar_routes(parent_router: APIRouter) -> None:
    """Register Google Calendar webhook integration routes onto a parent router."""
    calendar_router = create_calendar_router()
    parent_router.include_router(
        calendar_router, prefix="/integrations/calendar", tags=["Calendar"]
    )


__all__ = [
    "WebhookResponse",
    "create_calendar_router",
    "register_calendar_routes",
    "get_db_session",
]
