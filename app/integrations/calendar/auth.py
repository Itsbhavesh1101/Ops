"""Google OAuth2 Authentication and Token Provider.

Part of Phase 7C Real Calendar Provider Adapter.
Enforces:
- Keyless Google Application Default Credentials (ADC) resolution (default & preferred).
- Local service-account impersonation, attached GCP credentials, or Workload Identity Federation.
- Backward-compatible RFC 7523 JWT Bearer exchange (RS256, iss, aud, scope, iat, exp).
- Lazy access token acquisition with in-memory caching and proactive skew renewal.
- Concurrency protection: asyncio.Lock prevents duplicate parallel refresh storms.
- Token invalidation support on upstream 401 responses.
- Fail-closed security: typed GoogleCalendarAuthError on failure.
- Zero credential logging: private keys, signed assertions, bearer tokens,
  and ADC credential JSON are strictly redacted from logs and exception messages.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx
import jwt
from pydantic import SecretStr

from app.integrations.calendar.config import GoogleCalendarSettings

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALENDAR_OAUTH_SCOPE = "https://www.googleapis.com/auth/calendar"
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
DEFAULT_EXPIRY_SKEW_SECONDS = 300


class GoogleCalendarAuthError(RuntimeError):
    """Raised when Google Calendar authentication, ADC resolution, or token exchange fails."""


@runtime_checkable
class CalendarTokenProvider(Protocol):
    """Protocol for calendar bearer token providers."""

    async def get_access_token(self) -> str:
        """Retrieve a valid OAuth2 bearer token."""
        ...

    def invalidate_cached_token(self) -> None:
        """Invalidate the cached bearer token, forcing exchange on next request."""
        ...

    def is_token_valid(self) -> bool:
        """Check whether the currently cached token is valid and unexpired."""
        ...


def _normalize_utc_datetime(dt: datetime | None) -> datetime | None:
    """Normalize datetime to UTC-aware datetime."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class GoogleADCTokenProvider:
    """Production token provider for Google Application Default Credentials (ADC).

    Supports keyless authentication for:
    - LOCAL: developer identity impersonating a dedicated service account via ADC
    - GOOGLE CLOUD: attached service-account credentials / Workload Identity
    - EXTERNAL CLOUD / ON-PREM: Workload Identity Federation credentials
    """

    def __init__(
        self,
        settings: GoogleCalendarSettings,
        credentials: Any = None,
        request: Any = None,
        scopes: list[str] | None = None,
        skew_seconds: int = DEFAULT_EXPIRY_SKEW_SECONDS,
        lifetime_seconds: int = DEFAULT_TOKEN_LIFETIME_SECONDS,
    ) -> None:
        self._settings = settings
        self._credentials = credentials
        self._request = request
        self._scopes = scopes or [CALENDAR_OAUTH_SCOPE]
        self._skew_seconds = skew_seconds
        self._lifetime_seconds = min(lifetime_seconds, 3600)

        self._lock = asyncio.Lock()
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None

    def _get_request(self) -> Any:
        """Obtain a bounded Request transport for official google-auth token refresh."""
        if self._request is not None:
            return self._request

        import google.auth.transport.urllib3
        import urllib3

        http = urllib3.PoolManager(
            timeout=urllib3.Timeout(total=self._settings.timeout_seconds)
        )
        return google.auth.transport.urllib3.Request(http)

    def _resolve_credentials(self) -> Any:
        """Resolve Google Application Default Credentials with calendar scope."""
        if self._credentials is not None:
            return self._credentials

        try:
            import google.auth

            creds, _project_id = google.auth.default(scopes=self._scopes)
            self._credentials = creds
            return self._credentials
        except Exception as exc:
            # Strictly fail closed without exposing underlying credentials or secrets
            err_type = type(exc).__name__
            raise GoogleCalendarAuthError(
                f"Failed to resolve Google Application Default Credentials (ADC): {err_type}. "
                "Ensure ADC is configured (e.g. 'gcloud auth application-default login "
                "--impersonate-service-account=<SA_EMAIL> "
                "--scopes=https://www.googleapis.com/auth/calendar') "
                "or attached service account / Workload Identity Federation is available."
            ) from None

    def _refresh_sync(self, creds: Any, request: Any) -> tuple[str, datetime | None]:
        """Perform synchronous credential refresh using official google-auth semantics."""
        try:
            creds.refresh(request)
        except Exception as exc:
            # Fail closed with typed error, strictly redacting any secret tokens or credentials
            raise GoogleCalendarAuthError(
                f"Google ADC token refresh failed: {type(exc).__name__}"
            ) from None

        token = getattr(creds, "token", None)
        if not token or not isinstance(token, str):
            raise GoogleCalendarAuthError(
                "Google ADC credentials refresh succeeded but did not yield an access token."
            )

        expiry = getattr(creds, "expiry", None)
        normalized_expiry = _normalize_utc_datetime(expiry)
        return token, normalized_expiry

    def invalidate_cached_token(self) -> None:
        """Invalidate the cached bearer token, forcing fresh exchange on next request."""
        self._cached_token = None
        self._expires_at = None
        if self._credentials is not None:
            if hasattr(self._credentials, "expiry"):
                self._credentials.expiry = None
            if hasattr(self._credentials, "token"):
                self._credentials.token = None

    def is_token_valid(self) -> bool:
        """Check whether the currently cached token is valid and not within the skew window."""
        if not self._cached_token or self._expires_at is None:
            return False
        cutoff = datetime.now(UTC) + timedelta(seconds=self._skew_seconds)
        if self._expires_at <= cutoff:
            return False
        if self._credentials is not None and getattr(self._credentials, "expired", False):
            return False
        return True

    async def get_access_token(self) -> str:
        """Retrieve a valid OAuth2 bearer token via ADC, refreshing lazily when needed."""
        if self.is_token_valid():
            assert self._cached_token is not None
            return self._cached_token

        async with self._lock:
            if self.is_token_valid():
                assert self._cached_token is not None
                return self._cached_token

            creds = self._resolve_credentials()

            # If credentials already have a valid token not within skew, use it
            if getattr(creds, "valid", False) and getattr(creds, "token", None):
                token = creds.token
                expiry = _normalize_utc_datetime(getattr(creds, "expiry", None))
                cutoff = datetime.now(UTC) + timedelta(seconds=self._skew_seconds)
                if expiry is None or expiry > cutoff:
                    self._cached_token = token
                    self._expires_at = expiry or (
                        datetime.now(UTC) + timedelta(seconds=self._lifetime_seconds)
                    )
                    return self._cached_token

            req = self._get_request()
            token, expiry = await asyncio.to_thread(self._refresh_sync, creds, req)

            self._cached_token = token
            self._expires_at = expiry or (
                datetime.now(UTC) + timedelta(seconds=self._lifetime_seconds)
            )
            return self._cached_token


class GoogleTokenProvider:
    """Production token provider for legacy service-account private key authentication."""

    def __init__(
        self,
        settings: GoogleCalendarSettings,
        http_client: httpx.AsyncClient | None = None,
        token_url: str = GOOGLE_TOKEN_ENDPOINT,
        skew_seconds: int = DEFAULT_EXPIRY_SKEW_SECONDS,
        lifetime_seconds: int = DEFAULT_TOKEN_LIFETIME_SECONDS,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._token_url = token_url
        self._skew_seconds = skew_seconds
        self._lifetime_seconds = min(lifetime_seconds, 3600)

        self._lock = asyncio.Lock()
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None

    def _extract_private_key_pem(self) -> str:
        """Extract and normalize PEM private key string without exposing secrets."""
        raw_key = self._settings.private_key
        key_str = raw_key.get_secret_value() if isinstance(raw_key, SecretStr) else str(raw_key)
        key_str = key_str.strip()
        if not key_str:
            raise GoogleCalendarAuthError(
                "Service account private key is empty or not configured."
            )
        # Normalize escaped newlines in PEM format
        if "\\n" in key_str:
            key_str = key_str.replace("\\n", "\n")
        return key_str

    def _generate_jwt_assertion(self) -> str:
        """Generate a signed RFC 7523 RS256 JWT assertion."""
        client_email = self._settings.client_email.strip()
        if not client_email:
            raise GoogleCalendarAuthError(
                "Service account client email is empty or not configured."
            )

        key_pem = self._extract_private_key_pem()
        now_epoch = int(datetime.now(UTC).timestamp())
        exp_epoch = now_epoch + self._lifetime_seconds

        claims: dict[str, Any] = {
            "iss": client_email,
            "scope": CALENDAR_OAUTH_SCOPE,
            "aud": self._token_url,
            "iat": now_epoch,
            "exp": exp_epoch,
            "jti": str(uuid.uuid4()),
        }

        try:
            signed_jwt = jwt.encode(claims, key_pem, algorithm="RS256")
            if isinstance(signed_jwt, bytes):
                return signed_jwt.decode("utf-8")
            return str(signed_jwt)
        except Exception as exc:
            raise GoogleCalendarAuthError(
                f"Failed to generate signed JWT assertion: {type(exc).__name__}"
            ) from None

    def invalidate_cached_token(self) -> None:
        """Invalidate the cached bearer token, forcing exchange on next request."""
        self._cached_token = None
        self._expires_at = None

    def is_token_valid(self) -> bool:
        """Check whether the currently cached token is valid and not within the skew window."""
        if not self._cached_token or self._expires_at is None:
            return False
        cutoff = datetime.now(UTC) + timedelta(seconds=self._skew_seconds)
        return self._expires_at > cutoff

    async def get_access_token(self) -> str:
        """Retrieve a valid OAuth2 bearer token, using cache or performing token exchange."""
        if self.is_token_valid():
            assert self._cached_token is not None
            return self._cached_token

        async with self._lock:
            # Double-check inside concurrency lock
            if self.is_token_valid():
                assert self._cached_token is not None
                return self._cached_token

            assertion = self._generate_jwt_assertion()
            payload = {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }

            client = self._http_client or httpx.AsyncClient(
                timeout=self._settings.timeout_seconds
            )
            should_close = self._http_client is None

            try:
                resp = await client.post(
                    self._token_url,
                    data=payload,
                    headers=headers,
                )
            except Exception as net_exc:
                raise GoogleCalendarAuthError(
                    f"Network error during Google OAuth2 token exchange: {type(net_exc).__name__}"
                ) from None
            finally:
                if should_close:
                    await client.aclose()

            if resp.status_code != 200:
                # Never include the request assertion or response authorization in the error
                raise GoogleCalendarAuthError(
                    f"Google OAuth2 token exchange failed: HTTP {resp.status_code}"
                )

            try:
                data = resp.json()
            except Exception:
                raise GoogleCalendarAuthError(
                    "Google OAuth2 token exchange returned invalid JSON."
                ) from None

            access_token = data.get("access_token")
            if not access_token or not isinstance(access_token, str):
                raise GoogleCalendarAuthError(
                    "Google OAuth2 token response did not contain a valid access_token."
                )

            expires_in = data.get("expires_in", self._lifetime_seconds)
            try:
                expires_in_int = int(expires_in)
            except (ValueError, TypeError):
                expires_in_int = self._lifetime_seconds

            self._cached_token = access_token
            self._expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_int)
            return self._cached_token


class DeterministicCalendarTokenProvider:
    """Deterministic token provider implementing CalendarTokenProvider for test scenarios.

    Injectable in contract and E2E environments without external credentials while
    preserving real adapter and transport wire execution and header injection.
    """

    def __init__(
        self,
        token: str = "ya29.synthetic_deterministic_test_token",
        valid: bool = True,
    ) -> None:
        self._token = token
        self._valid = valid
        self._access_count = 0
        self._invalidation_count = 0

    @property
    def access_count(self) -> int:
        return self._access_count

    @property
    def invalidation_count(self) -> int:
        return self._invalidation_count

    async def get_access_token(self) -> str:
        if not self._valid:
            raise GoogleCalendarAuthError(
                "Deterministic token provider is configured as invalid / expired."
            )
        self._access_count += 1
        return self._token

    def invalidate_cached_token(self) -> None:
        self._invalidation_count += 1
        self._valid = False

    def is_token_valid(self) -> bool:
        return self._valid


def create_token_provider(
    settings: GoogleCalendarSettings,
    http_client: httpx.AsyncClient | None = None,
) -> CalendarTokenProvider | None:
    """Create the appropriate token provider based on configured auth_mode."""
    mode = getattr(settings, "auth_mode", "adc")
    if mode == "adc":
        return GoogleADCTokenProvider(settings=settings)
    elif mode == "service_account_key":
        private_key_val = settings.private_key.get_secret_value()
        if settings.client_email.strip() and private_key_val.strip():
            return GoogleTokenProvider(settings=settings, http_client=http_client)
    return None


__all__ = [
    "CALENDAR_OAUTH_SCOPE",
    "CalendarTokenProvider",
    "DEFAULT_EXPIRY_SKEW_SECONDS",
    "DEFAULT_TOKEN_LIFETIME_SECONDS",
    "DeterministicCalendarTokenProvider",
    "GOOGLE_TOKEN_ENDPOINT",
    "GoogleADCTokenProvider",
    "GoogleCalendarAuthError",
    "GoogleTokenProvider",
    "create_token_provider",
]
