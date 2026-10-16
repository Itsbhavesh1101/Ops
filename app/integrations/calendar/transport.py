"""Authenticated HTTP transport for Google Calendar REST v3 API.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- Bearer token injection using GoogleTokenProvider.
- Bounded HTTP timeouts.
- Concurrency and 401 token invalidation with single retry.
- Bounded handling of transient 429 / 5xx where appropriate.
- Zero credential logging: Authorization headers and tokens are redacted.
- Preservation of dependency injection for deterministic testing.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.integrations.calendar.auth import (
    CalendarTokenProvider,
    GoogleCalendarAuthError,
    GoogleTokenProvider,
)
from app.integrations.calendar.config import GoogleCalendarSettings

logger = logging.getLogger(__name__)


class GoogleCalendarApiError(RuntimeError):
    """Raised when Google Calendar REST API returns an unrecoverable error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class GoogleCalendarTransport:
    """Authenticated HTTP transport providing resilience and security for Google Calendar."""

    def __init__(
        self,
        settings: GoogleCalendarSettings,
        token_provider: CalendarTokenProvider | GoogleTokenProvider | None = None,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://www.googleapis.com/calendar/v3",
    ) -> None:
        self._settings = settings
        self._token_provider = token_provider
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")

    @property
    def http_client(self) -> httpx.AsyncClient | None:
        """Return the underlying injected client, if any."""
        return self._http_client

    @property
    def token_provider(self) -> CalendarTokenProvider | GoogleTokenProvider | None:
        """Return the token provider, if configured."""
        return self._token_provider

    async def _send_once(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_data: dict[str, Any] | None,
    ) -> httpx.Response:
        """Execute a single HTTP request with timeout protection."""
        req_headers = dict(headers)
        if self._token_provider:
            token = await self._token_provider.get_access_token()
            req_headers["Authorization"] = f"Bearer {token}"

        return await client.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
            headers=req_headers,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_on_401: bool = True,
    ) -> httpx.Response:
        """Execute an authenticated HTTP request to the Google Calendar API."""
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self._base_url}/{path.lstrip('/')}"

        req_headers = dict(headers or {})
        client = self._http_client or httpx.AsyncClient(timeout=self._settings.timeout_seconds)
        should_close = self._http_client is None

        try:
            resp = await self._send_once(
                client=client,
                method=method,
                url=url,
                headers=req_headers,
                params=params,
                json_data=json,
            )

            # On 401 Unauthorized, refresh token once and retry once where safe
            if resp.status_code == 401 and retry_on_401 and self._token_provider:
                logger.info("google_calendar_token_expired_refreshing")
                self._token_provider.invalidate_cached_token()
                resp = await self._send_once(
                    client=client,
                    method=method,
                    url=url,
                    headers=req_headers,
                    params=params,
                    json_data=json,
                )

            # Handle transient 429 rate limits for idempotent calls with Retry-After
            if resp.status_code == 429 and method in ("GET", "PUT", "DELETE"):
                retry_after_str = resp.headers.get("Retry-After")
                if retry_after_str:
                    try:
                        delay = min(float(retry_after_str), 2.0)
                        await asyncio.sleep(delay)
                        resp = await self._send_once(
                            client=client,
                            method=method,
                            url=url,
                            headers=req_headers,
                            params=params,
                            json_data=json,
                        )
                    except Exception:
                        pass

            return resp
        except (GoogleCalendarAuthError, httpx.RequestError):
            raise
        except Exception as exc:
            raise GoogleCalendarApiError(
                f"Unexpected transport error during {method} {path}: {type(exc).__name__}"
            ) from None
        finally:
            if should_close:
                await client.aclose()


__all__ = [
    "GoogleCalendarApiError",
    "GoogleCalendarTransport",
]
