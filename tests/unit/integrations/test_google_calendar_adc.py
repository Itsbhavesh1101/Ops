"""Focused unit tests for Google Calendar keyless Application Default Credentials (ADC) auth.

Part of Phase 7C Real Calendar Provider Adapter.
Verifies:
- ADC credentials successfully resolved with official google-auth.
- Calendar scope (https://www.googleapis.com/auth/calendar) requested.
- Token obtained through ADC provider.
- Concurrent token refresh is serialized via asyncio.Lock (prevents refresh storms).
- Expired token triggers refresh.
- 401 Unauthorized invalidates cached token and triggers refresh-once.
- ADC resolution failure fails closed with typed GoogleCalendarAuthError.
- Secret/token redaction on failure.
- Private key NOT required in ADC mode.
- Google Calendar disabled behavior.
- GoogleCalendarAdapter instantiation with ADC provider.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from pydantic import SecretStr

from app.integrations.calendar.auth import (
    CALENDAR_OAUTH_SCOPE,
    GoogleADCTokenProvider,
    GoogleCalendarAuthError,
    GoogleTokenProvider,
    create_token_provider,
)
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.transport import GoogleCalendarTransport


class MockCredentials:
    """Mock Google Credentials for deterministic unit testing without network calls."""

    def __init__(
        self,
        initial_token: str | None = None,
        initial_expiry: datetime | None = None,
    ) -> None:
        self.token = initial_token
        self.expiry = initial_expiry
        self.refresh_count = 0
        self.valid = bool(
            initial_token and initial_expiry and initial_expiry > datetime.now(UTC)
        )

    def refresh(self, request: Any) -> None:
        self.refresh_count += 1
        self.token = f"refreshed-adc-token-{self.refresh_count}"
        self.expiry = datetime.now(UTC) + timedelta(seconds=3600)
        self.valid = True

    @property
    def expired(self) -> bool:
        if not self.expiry:
            return True
        return datetime.now(UTC) >= self.expiry


class MockRequest:
    """Mock Request callable for google-auth refresh."""

    pass


# -----------------------------------------------------------------------------
# 1. Configuration & Private Key Not Required in ADC Mode
# -----------------------------------------------------------------------------


def test_private_key_not_required_in_adc_mode() -> None:
    """Verify that E7_GOOGLE_CALENDAR_AUTH_MODE=adc does NOT require private_key or client_email."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary_calendar@group.calendar.google.com",
    )
    assert settings.auth_mode == "adc"
    assert settings.enabled is True
    assert settings.calendar_id == "c_secondary_calendar@group.calendar.google.com"
    assert settings.private_key.get_secret_value() == ""
    assert settings.client_email == ""


def test_adc_mode_requires_calendar_id_when_enabled() -> None:
    """Verify that ADC mode still requires a non-empty calendar_id when enabled=True."""
    with pytest.raises(ValueError, match="calendar_id is empty"):
        GoogleCalendarSettings(
            enabled=True,
            auth_mode="adc",
            calendar_id="",
        )


def test_disabled_calendar_behavior() -> None:
    """When disabled, GoogleCalendarSettings does not require credentials or calendar_id."""
    settings = GoogleCalendarSettings(enabled=False, _env_file=None)
    assert settings.enabled is False
    assert settings.auth_mode == "adc"
    adapter = GoogleCalendarAdapter(settings=settings)
    assert adapter is not None


def test_google_calendar_settings_env_file_loading_regression(tmp_path: Any) -> None:
    """Verify GoogleCalendarSettings automatically loads values from a specified env_file."""
    env_content = (
        "E7_GOOGLE_CALENDAR_ENABLED=true\n"
        "E7_GOOGLE_CALENDAR_AUTH_MODE=adc\n"
        "E7_GOOGLE_CALENDAR_CALENDAR_ID=test-calendar@group.calendar.google.com\n"
    )
    temp_env = tmp_path / ".env.test"
    temp_env.write_text(env_content, encoding="utf-8")

    settings = GoogleCalendarSettings(_env_file=str(temp_env))
    assert settings.enabled is True
    assert settings.auth_mode == "adc"
    assert settings.calendar_id == "test-calendar@group.calendar.google.com"
    assert settings.private_key.get_secret_value() == ""


def test_google_calendar_settings_env_file_none_isolation() -> None:
    """Verify GoogleCalendarSettings(_env_file=None) ignores any .env file and uses defaults."""
    settings = GoogleCalendarSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.auth_mode == "adc"
    assert settings.calendar_id == ""


def test_google_calendar_settings_env_file_none_fail_closed_when_enabled() -> None:
    """Verify _env_file=None fails closed when enabled=True without calendar_id."""
    with pytest.raises(ValueError, match="calendar_id is empty"):
        GoogleCalendarSettings(enabled=True, _env_file=None)


# -----------------------------------------------------------------------------
# 2. Scope & Resolution via Official google-auth
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adc_credentials_successfully_resolved_and_scoped() -> None:
    """Verify ADC credentials resolution requests CALENDAR_OAUTH_SCOPE."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    provider = GoogleADCTokenProvider(settings=settings)

    mock_creds = MockCredentials()
    with patch(
        "google.auth.default", return_value=(mock_creds, "mock-project-id")
    ) as mock_default:
        token = await provider.get_access_token()
        mock_default.assert_called_once_with(scopes=[CALENDAR_OAUTH_SCOPE])
        assert token == "refreshed-adc-token-1"
        assert mock_creds.refresh_count == 1


@pytest.mark.asyncio
async def test_adc_resolution_failure_fails_closed() -> None:
    """When ADC resolution fails, raise typed GoogleCalendarAuthError without leaking secrets."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    provider = GoogleADCTokenProvider(settings=settings)

    with patch(
        "google.auth.default",
        side_effect=DefaultCredentialsError("Could not automatically determine credentials"),
    ):
        with pytest.raises(GoogleCalendarAuthError) as exc_info:
            await provider.get_access_token()

        err_msg = str(exc_info.value)
        assert "Failed to resolve Google Application Default Credentials (ADC)" in err_msg
        assert "DefaultCredentialsError" in err_msg
        assert "gcloud auth application-default login" in err_msg


# -----------------------------------------------------------------------------
# 3. Token Acquisition, Caching & Expiry
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adc_token_obtained_and_cached() -> None:
    """Verify that once obtained, the access token is cached and reused on subsequent calls."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    creds = MockCredentials()
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
    )

    # First acquisition triggers refresh
    token1 = await provider.get_access_token()
    assert token1 == "refreshed-adc-token-1"
    assert creds.refresh_count == 1
    assert provider.is_token_valid() is True

    # Second acquisition uses cache
    token2 = await provider.get_access_token()
    assert token2 == "refreshed-adc-token-1"
    assert creds.refresh_count == 1


@pytest.mark.asyncio
async def test_adc_expired_token_triggers_refresh() -> None:
    """Verify that when a cached token expires, get_access_token() triggers a refresh."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    creds = MockCredentials(
        initial_token="stale-token",
        initial_expiry=datetime.now(UTC) - timedelta(seconds=60),
    )
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
    )

    assert provider.is_token_valid() is False
    token = await provider.get_access_token()
    assert token == "refreshed-adc-token-1"
    assert creds.refresh_count == 1
    assert provider.is_token_valid() is True


@pytest.mark.asyncio
async def test_adc_proactive_skew_refresh() -> None:
    """Verify that a token expiring within the skew window is refreshed proactively."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    # Token expires in 100 seconds, but skew is 300 seconds
    creds = MockCredentials(
        initial_token="about-to-expire-token",
        initial_expiry=datetime.now(UTC) + timedelta(seconds=100),
    )
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
        skew_seconds=300,
    )

    # is_token_valid should return False due to skew window
    assert provider.is_token_valid() is False
    token = await provider.get_access_token()
    assert token == "refreshed-adc-token-1"
    assert creds.refresh_count == 1


# -----------------------------------------------------------------------------
# 4. Concurrency Safety & Stampede Protection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adc_concurrent_token_refresh_prevents_storms() -> None:
    """Verify that multiple concurrent calls to get_access_token() execute exactly 1 refresh."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    refresh_calls = 0

    class DelayedRefreshCredentials:
        def __init__(self) -> None:
            self.token: str | None = None
            self.expiry: datetime | None = None
            self.valid: bool = False

        def refresh(self, request: Any) -> None:
            nonlocal refresh_calls
            refresh_calls += 1
            import time

            time.sleep(0.04)  # simulate network delay in worker thread
            self.token = "concurrent-adc-token"
            self.expiry = datetime.now(UTC) + timedelta(seconds=3600)
            self.valid = True

        @property
        def expired(self) -> bool:
            return self.token is None

    mock_creds = DelayedRefreshCredentials()
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=mock_creds,
        request=MockRequest(),
    )

    tasks = [asyncio.create_task(provider.get_access_token()) for _ in range(12)]
    results = await asyncio.gather(*tasks)

    assert refresh_calls == 1
    assert len(results) == 12
    assert all(tok == "concurrent-adc-token" for tok in results)


# -----------------------------------------------------------------------------
# 5. 401 Refresh-Once Path with GoogleCalendarTransport
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adc_transport_401_refresh_once() -> None:
    """Verify transport handles 401 by invalidating ADC token and refreshing once."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    creds = MockCredentials()
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
    )

    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        auth_header = request.headers.get("Authorization")
        if call_count == 1:
            assert auth_header == "Bearer refreshed-adc-token-1"
            return httpx.Response(401, json={"error": "token_expired"})
        else:
            assert auth_header == "Bearer refreshed-adc-token-2"
            return httpx.Response(200, json={"items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    transport = GoogleCalendarTransport(
        settings=settings,
        token_provider=provider,
        http_client=client,
    )

    resp = await transport.request("GET", "calendars/c_secondary/events")
    assert resp.status_code == 200
    assert call_count == 2
    assert creds.refresh_count == 2


@pytest.mark.asyncio
async def test_adc_transport_bounded_401_prevents_infinite_loop() -> None:
    """Verify transport bounds 401 retries to a single attempt if authorization fails repeatedly."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    creds = MockCredentials()
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
    )

    call_count = 0

    def persistent_401_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": "invalid_grant"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(persistent_401_handler))
    transport = GoogleCalendarTransport(
        settings=settings,
        token_provider=provider,
        http_client=client,
    )

    resp = await transport.request("GET", "calendars/c_secondary/events")
    assert resp.status_code == 401
    assert call_count == 2  # Original attempt + 1 retry only


# -----------------------------------------------------------------------------
# 6. Secret / Credential Redaction on Failure
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adc_token_provider_secret_redaction_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure that exceptions and logs never expose underlying refresh tokens or credential data."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )

    secret_raw = "super-confidential-refresh-token-value-xyz987"

    class FailingCredentials:
        def refresh(self, request: Any) -> None:
            raise RefreshError(
                f"OAuth endpoint rejected refresh with payload containing {secret_raw}"
            )

        @property
        def expired(self) -> bool:
            return True

    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=FailingCredentials(),
        request=MockRequest(),
    )

    with pytest.raises(GoogleCalendarAuthError) as exc_info:
        await provider.get_access_token()

    # The exception message must NOT expose raw token or confidential payload
    assert secret_raw not in str(exc_info.value)
    # Log records must not contain the secret
    for record in caplog.records:
        assert secret_raw not in record.message


# -----------------------------------------------------------------------------
# 7. Adapter Integration & Provider Factory
# -----------------------------------------------------------------------------


def test_create_token_provider_adc() -> None:
    """Verify create_token_provider instantiates GoogleADCTokenProvider for auth_mode=adc."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    provider = create_token_provider(settings)
    assert isinstance(provider, GoogleADCTokenProvider)


def test_create_token_provider_service_account_key() -> None:
    """Verify create_token_provider instantiates GoogleTokenProvider for legacy mode."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="service_account_key",
        client_email="sa@google.com",
        private_key=SecretStr("mock_key"),
        calendar_id="c_secondary@group.calendar.google.com",
    )
    provider = create_token_provider(settings)
    assert isinstance(provider, GoogleTokenProvider)


def test_adapter_default_uses_adc_token_provider() -> None:
    """GoogleCalendarAdapter defaults to GoogleADCTokenProvider when auth_mode is adc."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    adapter = GoogleCalendarAdapter(settings=settings)
    assert isinstance(adapter.transport.token_provider, GoogleADCTokenProvider)


@pytest.mark.asyncio
async def test_adapter_end_to_end_mock_adc_bearer_injection() -> None:
    """Verify adapter injects ADC bearer token on outgoing calendar requests."""
    settings = GoogleCalendarSettings(
        enabled=True,
        auth_mode="adc",
        calendar_id="c_secondary@group.calendar.google.com",
    )
    creds = MockCredentials()
    provider = GoogleADCTokenProvider(
        settings=settings,
        credentials=creds,
        request=MockRequest(),
    )

    seen_auth_header = ""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_auth_header
        seen_auth_header = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={
                "id": "event-123",
                "summary": "Meeting",
                "start": {"dateTime": "2026-10-10T10:00:00Z"},
                "end": {"dateTime": "2026-10-10T11:00:00Z"},
                "etag": '"etag-1"',
            },
        )

    mock_http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    transport = GoogleCalendarTransport(
        settings=settings,
        token_provider=provider,
        http_client=mock_http_client,
    )
    adapter = GoogleCalendarAdapter(settings=settings, transport=transport)

    event = await adapter.get_event("tenant-1", "event-123")
    assert event is not None
    assert event.title == "Meeting"
    assert seen_auth_header == "Bearer refreshed-adc-token-1"
