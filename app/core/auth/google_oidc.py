"""Google OpenID Connect (OIDC) ID-token cryptographic verification adapter.

Implements local cryptographic signature verification, dynamic discovery and JWKS caching,
strict asymmetric algorithm allowlisting, and claims validation.
Returns a frozen VerifiedOidcIdentity without deriving tenant or role authorization.
"""

import asyncio
import re
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
import structlog

from app.core.auth.models import VerifiedOidcIdentity
from app.core.auth.oidc import OidcVerificationError
from app.core.config import Settings

logger = structlog.get_logger("app.core.auth.google_oidc")

GOOGLE_CANONICAL_DISCOVERY_URL: str = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ALLOWED_ISSUERS: frozenset[str] = frozenset(
    {"https://accounts.google.com", "accounts.google.com"}
)
GOOGLE_ALLOWED_JWKS_HOSTS: frozenset[str] = frozenset(
    {"www.googleapis.com", "googleapis.com", "accounts.google.com"}
)

ALLOWED_GOOGLE_ID_TOKEN_ALGORITHMS: frozenset[str] = frozenset({"RS256"})
ALLOWED_ALGORITHMS: frozenset[str] = ALLOWED_GOOGLE_ID_TOKEN_ALGORITHMS
PROHIBITED_ALGORITHMS: frozenset[str] = frozenset(
    {"none", "ES256", "PS256", "RS384", "RS512", "HS256", "HS384", "HS512"}
)

# -----------------------------------------------------------------------------
# Internal Verification Exception Taxonomy
# -----------------------------------------------------------------------------


class GoogleOidcVerificationError(OidcVerificationError):
    """Base exception for all Google OIDC verification failures."""

    reason_code: str = "OIDC_VERIFICATION_FAILED"

    def __init__(self, message: str, reason_code: str | None = None) -> None:
        super().__init__(message)
        if reason_code:
            self.reason_code = reason_code


class MalformedTokenError(GoogleOidcVerificationError):
    reason_code = "MALFORMED_TOKEN"


class UnsupportedAlgorithmError(GoogleOidcVerificationError):
    reason_code = "UNSUPPORTED_ALGORITHM"


class SigningKeyNotFoundError(GoogleOidcVerificationError):
    reason_code = "SIGNING_KEY_NOT_FOUND"


class InvalidSignatureError(GoogleOidcVerificationError):
    reason_code = "INVALID_SIGNATURE"


class InvalidIssuerError(GoogleOidcVerificationError):
    reason_code = "INVALID_ISSUER"


class InvalidAudienceError(GoogleOidcVerificationError):
    reason_code = "INVALID_AUDIENCE"


class TokenExpiredError(GoogleOidcVerificationError):
    reason_code = "TOKEN_EXPIRED"


class TokenNotYetValidError(GoogleOidcVerificationError):
    reason_code = "TOKEN_NOT_YET_VALID"


class InvalidRequiredClaimsError(GoogleOidcVerificationError):
    reason_code = "INVALID_REQUIRED_CLAIMS"


class NonceMismatchError(GoogleOidcVerificationError):
    reason_code = "NONCE_MISMATCH"


class HostedDomainRejectedError(GoogleOidcVerificationError):
    reason_code = "HOSTED_DOMAIN_REJECTED"


class ProviderMetadataUnavailableError(GoogleOidcVerificationError):
    reason_code = "PROVIDER_METADATA_UNAVAILABLE"


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _parse_cache_control_max_age(cache_control_header: str | None) -> int | None:
    """Extract max-age integer from a Cache-Control HTTP header value if present."""
    if not cache_control_header:
        return None
    match = re.search(r"max-age=(\d+)", cache_control_header, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


# -----------------------------------------------------------------------------
# Google OIDC Verifier
# -----------------------------------------------------------------------------


class GoogleOidcVerifier:
    """Cryptographic verifier for Google OpenID Connect ID tokens.

    Operates strictly within the provider adapter boundary. Normalizes validated
    claims into a provider-neutral VerifiedOidcIdentity. Does NOT derive tenant_id,
    assign roles/permissions, construct AuthenticatedPrincipal, or query databases.
    """

    def __init__(
        self,
        discovery_url: str = GOOGLE_CANONICAL_DISCOVERY_URL,
        client_ids: Iterable[str] = (),
        allowed_hosted_domains: Iterable[str] | None = None,
        http_timeout_seconds: float = 10.0,
        clock_skew_seconds: int = 60,
        cache_fallback_seconds: int = 3600,
        cache_max_seconds: int = 86400,
        allowed_algorithms: Iterable[str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not discovery_url or not isinstance(discovery_url, str) or not discovery_url.strip():
            raise ValueError("GoogleOidcVerifier discovery_url must be a non-empty string")
        clean_discovery_url = discovery_url.strip()
        parsed_url = urlparse(clean_discovery_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc.lower() != "accounts.google.com"
            or parsed_url.path != "/.well-known/openid-configuration"
        ):
            raise ValueError(
                "GoogleOidcVerifier discovery_url must point to Google's "
                f"canonical discovery endpoint: {GOOGLE_CANONICAL_DISCOVERY_URL}"
            )
        self.discovery_url = clean_discovery_url

        cleaned_client_ids = [str(c).strip() for c in client_ids if str(c).strip()]
        if not cleaned_client_ids:
            raise ValueError("GoogleOidcVerifier requires at least one approved client ID")
        self.client_ids: frozenset[str] = frozenset(cleaned_client_ids)

        if allowed_hosted_domains:
            self.allowed_hosted_domains: frozenset[str] = frozenset(
                str(d).strip().lower() for d in allowed_hosted_domains if str(d).strip()
            )
        else:
            self.allowed_hosted_domains = frozenset()

        if http_timeout_seconds <= 0 or http_timeout_seconds > 60.0:
            raise ValueError("http_timeout_seconds must be between 0.1 and 60.0 seconds")
        self.http_timeout_seconds = http_timeout_seconds

        if clock_skew_seconds < 0 or clock_skew_seconds > 300:
            raise ValueError("clock_skew_seconds must be between 0 and 300 seconds")
        self.clock_skew_seconds = clock_skew_seconds

        if cache_fallback_seconds <= 0 or cache_fallback_seconds > 604800:
            raise ValueError("cache_fallback_seconds must be between 1 and 604800 seconds")
        self.cache_fallback_seconds = cache_fallback_seconds

        if cache_max_seconds < cache_fallback_seconds or cache_max_seconds > 2592000:
            raise ValueError("cache_max_seconds must be >= cache_fallback_seconds and <= 2592000")
        self.cache_max_seconds = cache_max_seconds

        if allowed_algorithms is not None:
            cleaned_algs = frozenset(str(a).strip() for a in allowed_algorithms if str(a).strip())
            # Ensure no prohibited algorithms sneak in
            if any(alg in PROHIBITED_ALGORITHMS or alg.lower() == "none" for alg in cleaned_algs):
                raise ValueError("Prohibited algorithms (none, HMAC, ES256, etc.) cannot be used")
            if not cleaned_algs.issubset(ALLOWED_GOOGLE_ID_TOKEN_ALGORITHMS):
                raise ValueError(
                    f"Google ID tokens only support {sorted(ALLOWED_GOOGLE_ID_TOKEN_ALGORITHMS)}; "
                    f"got {sorted(cleaned_algs)}"
                )
            self.allowed_algorithms = cleaned_algs
        else:
            self.allowed_algorithms = ALLOWED_GOOGLE_ID_TOKEN_ALGORITHMS

        self.clock = clock

        # HTTP client lifecycle
        self._http_client = http_client
        self._owns_http_client = http_client is None

        # Concurrency and cache locks
        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

        # In-process monotonic caches: (data, expiry_monotonic)
        self._discovery_cache: tuple[dict[str, Any], float] | None = None
        self._jwks_cache: tuple[jwt.PyJWKSet, float] | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "GoogleOidcVerifier":
        """Construct GoogleOidcVerifier directly from application Settings."""
        return cls(
            discovery_url=settings.google_oidc_discovery_url,
            client_ids=settings.google_oidc_client_ids,
            allowed_hosted_domains=settings.google_oidc_allowed_hosted_domains,
            http_timeout_seconds=settings.google_oidc_http_timeout_seconds,
            clock_skew_seconds=settings.google_oidc_clock_skew_seconds,
            cache_fallback_seconds=settings.google_oidc_cache_fallback_seconds,
            cache_max_seconds=settings.google_oidc_cache_max_seconds,
            http_client=http_client,
            clock=clock,
        )

    def _get_http_client(self) -> httpx.AsyncClient:
        """Retrieve active HTTP client or instantiate internal client if owned."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=self.http_timeout_seconds,
                follow_redirects=False,
            )
            self._owns_http_client = True
        return self._http_client

    async def aclose(self) -> None:
        """Close internally managed HTTP client cleanly."""
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> "GoogleOidcVerifier":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    def _compute_ttl(self, cache_control_header: str | None) -> float:
        """Calculate cache TTL honoring Cache-Control max-age within configured bounds."""
        parsed_max_age = _parse_cache_control_max_age(cache_control_header)
        if parsed_max_age is not None and parsed_max_age > 0:
            ttl = min(parsed_max_age, self.cache_max_seconds)
        else:
            ttl = min(self.cache_fallback_seconds, self.cache_max_seconds)
        return float(ttl)

    async def get_discovery_metadata(self, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch and cache validated OIDC discovery metadata."""
        now = self.clock()
        if not force_refresh and self._discovery_cache is not None:
            cached_data, expiry = self._discovery_cache
            if now < expiry:
                return cached_data

        async with self._discovery_lock:
            now = self.clock()
            if not force_refresh and self._discovery_cache is not None:
                cached_data, expiry = self._discovery_cache
                if now < expiry:
                    return cached_data

            client = self._get_http_client()
            try:
                resp = await client.get(self.discovery_url, follow_redirects=False)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to fetch OIDC discovery document: {exc.__class__.__name__}"
                ) from exc

            if resp.is_redirect or (300 <= resp.status_code < 400):
                raise ProviderMetadataUnavailableError(
                    f"OIDC discovery endpoint returned redirect HTTP {resp.status_code}"
                )

            if resp.status_code != 200:
                raise ProviderMetadataUnavailableError(
                    f"OIDC discovery endpoint returned HTTP {resp.status_code}"
                )

            try:
                data = resp.json()
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Malformed discovery JSON response: {exc.__class__.__name__}"
                ) from exc

            if not isinstance(data, dict):
                raise ProviderMetadataUnavailableError(
                    "Discovery document root must be a JSON object"
                )

            issuer = data.get("issuer")
            if not issuer or not isinstance(issuer, str) or not issuer.strip():
                raise ProviderMetadataUnavailableError("Discovery document missing valid 'issuer'")

            clean_issuer = issuer.strip()
            if clean_issuer not in GOOGLE_ALLOWED_ISSUERS:
                issuers_str = sorted(GOOGLE_ALLOWED_ISSUERS)
                raise ProviderMetadataUnavailableError(
                    f"Discovery document issuer '{clean_issuer}' is not in "
                    f"approved Google issuers: {issuers_str}"
                )

            jwks_uri = data.get("jwks_uri")
            if not jwks_uri or not isinstance(jwks_uri, str) or not jwks_uri.strip():
                raise ProviderMetadataUnavailableError(
                    "Discovery document missing valid 'jwks_uri'"
                )

            clean_jwks_uri = jwks_uri.strip()
            parsed_jwks = urlparse(clean_jwks_uri)
            jwks_hostname = (parsed_jwks.hostname or "").lower()
            if (
                parsed_jwks.scheme != "https"
                or jwks_hostname not in GOOGLE_ALLOWED_JWKS_HOSTS
                or (parsed_jwks.port is not None and parsed_jwks.port != 443)
            ):
                raise ProviderMetadataUnavailableError(
                    "Discovery 'jwks_uri' must be an approved Google HTTPS endpoint; "
                    f"got '{clean_jwks_uri}'"
                )

            # Validate discovery algorithm consistency if present
            supported_algs = data.get("id_token_signing_alg_values_supported")
            if supported_algs is not None:
                if not isinstance(supported_algs, list) or "RS256" not in supported_algs:
                    raise ProviderMetadataUnavailableError(
                        "Discovery metadata declares id_token_signing_alg_values_supported "
                        "but RS256 is absent"
                    )

            ttl = self._compute_ttl(resp.headers.get("Cache-Control"))
            self._discovery_cache = (data, now + ttl)
            return data

    async def get_jwks(self, force_refresh: bool = False) -> jwt.PyJWKSet:
        """Fetch and cache validated JWKS document."""
        now = self.clock()
        if not force_refresh and self._jwks_cache is not None:
            cached_set, expiry = self._jwks_cache
            if now < expiry:
                return cached_set

        async with self._jwks_lock:
            now = self.clock()
            if not force_refresh and self._jwks_cache is not None:
                cached_set, expiry = self._jwks_cache
                if now < expiry:
                    return cached_set

            discovery = await self.get_discovery_metadata(force_refresh=force_refresh)
            jwks_uri = discovery["jwks_uri"]

            client = self._get_http_client()
            try:
                resp = await client.get(jwks_uri, follow_redirects=False)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to fetch JWKS document: {exc.__class__.__name__}"
                ) from exc

            if resp.is_redirect or (300 <= resp.status_code < 400):
                raise ProviderMetadataUnavailableError(
                    f"JWKS document endpoint returned redirect HTTP {resp.status_code}"
                )

            if resp.status_code != 200:
                raise ProviderMetadataUnavailableError(
                    f"JWKS document endpoint returned HTTP {resp.status_code}"
                )

            try:
                jwks_data = resp.json()
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Malformed JWKS JSON response: {exc.__class__.__name__}"
                ) from exc

            if not isinstance(jwks_data, dict):
                raise ProviderMetadataUnavailableError("JWKS document root must be a JSON object")

            keys = jwks_data.get("keys")
            if not isinstance(keys, list):
                raise ProviderMetadataUnavailableError("JWKS 'keys' attribute must be a list")

            try:
                jwk_set = jwt.PyJWKSet.from_dict(jwks_data)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to parse JWKS: {exc.__class__.__name__}"
                ) from exc

            ttl = self._compute_ttl(resp.headers.get("Cache-Control"))
            self._jwks_cache = (jwk_set, now + ttl)
            return jwk_set

    async def get_signing_key(self, kid: str) -> jwt.PyJWK:
        """Lookup signing key by kid, triggering at most one forced refresh if absent."""
        # 1. Lookup in current cached JWKS
        jwk_set = await self.get_jwks(force_refresh=False)
        try:
            return jwk_set[kid]
        except KeyError:
            pass

        # 2. Key was missing: perform one controlled forced refresh under lock
        async with self._jwks_lock:
            now = self.clock()
            # Double check if another concurrent task refreshed the JWKS cache
            if self._jwks_cache is not None:
                cached_set, expiry = self._jwks_cache
                if now < expiry:
                    try:
                        return cached_set[kid]
                    except KeyError:
                        pass

            # Controlled refresh
            discovery = await self.get_discovery_metadata(force_refresh=False)
            jwks_uri = discovery["jwks_uri"]
            client = self._get_http_client()
            try:
                resp = await client.get(jwks_uri, follow_redirects=False)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to refresh JWKS document: {exc.__class__.__name__}"
                ) from exc

            if resp.is_redirect or (300 <= resp.status_code < 400):
                raise ProviderMetadataUnavailableError(
                    f"JWKS refresh returned redirect HTTP {resp.status_code}"
                )

            if resp.status_code != 200:
                raise ProviderMetadataUnavailableError(
                    f"JWKS refresh returned HTTP {resp.status_code}"
                )

            try:
                jwks_data = resp.json()
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Malformed JWKS JSON refresh response: {exc.__class__.__name__}"
                ) from exc

            if not isinstance(jwks_data, dict) or not isinstance(jwks_data.get("keys"), list):
                raise ProviderMetadataUnavailableError("Malformed JWKS structure during refresh")

            try:
                refreshed_set = jwt.PyJWKSet.from_dict(jwks_data)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to parse refreshed JWKS: {exc.__class__.__name__}"
                ) from exc

            ttl = self._compute_ttl(resp.headers.get("Cache-Control"))
            self._jwks_cache = (refreshed_set, now + ttl)

            try:
                return refreshed_set[kid]
            except KeyError as exc:
                raise SigningKeyNotFoundError(
                    "Signing key with specified 'kid' not found in provider JWKS after refresh"
                ) from exc

    async def verify_id_token(
        self,
        token: str,
        expected_nonce: str | None = None,
    ) -> VerifiedOidcIdentity:
        """Cryptographically verify Google ID token and return VerifiedOidcIdentity.

        Validates JOSE header, asymmetric signature against cached JWKS, issuer,
        audience, expiration, mandatory claims (sub, iss, aud, exp, iat), optional nbf,
        multi-audience/azp consistency, expected nonce, and hosted domain allowlist.
        """
        try:
            return await self._verify_id_token_internal(token, expected_nonce)
        except GoogleOidcVerificationError as exc:
            logger.warning(
                "google_oidc_verification_failed",
                reason_code=exc.reason_code,
                provider="google_oidc",
            )
            raise

    async def _verify_id_token_internal(
        self,
        token: str,
        expected_nonce: str | None,
    ) -> VerifiedOidcIdentity:
        # 1. Structure validation
        if not isinstance(token, str) or not token.strip():
            raise MalformedTokenError("Token must be a non-empty string")
        token_clean = token.strip()
        if token_clean.count(".") != 2:
            raise MalformedTokenError("Malformed JWT token structure")

        # 2. JOSE Header inspection (unverified safe read of alg and kid only)
        try:
            header = jwt.get_unverified_header(token_clean)
        except Exception as exc:
            raise MalformedTokenError("Failed to parse token JOSE header") from exc

        if not isinstance(header, dict):
            raise MalformedTokenError("Token JOSE header must be a JSON object")

        alg = header.get("alg")
        if not isinstance(alg, str) or not alg.strip():
            raise UnsupportedAlgorithmError("Token header missing or blank 'alg'")
        clean_alg = alg.strip()
        if (
            clean_alg.lower() == "none"
            or clean_alg in PROHIBITED_ALGORITHMS
            or clean_alg not in self.allowed_algorithms
        ):
            raise UnsupportedAlgorithmError(
                f"Algorithm '{clean_alg}' is not in approved asymmetric algorithm allowlist"
            )

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid.strip():
            raise SigningKeyNotFoundError("Token header missing or blank 'kid' claim")
        clean_kid = kid.strip()

        # 3. Retrieve public key
        signing_key = await self.get_signing_key(clean_kid)
        public_key = signing_key.key

        # 4. Discovery issuer boundary & provider pinning
        await self.get_discovery_metadata()
        expected_issuers = sorted(GOOGLE_ALLOWED_ISSUERS)

        # 5. Cryptographic signature and core claim validation
        try:
            payload = jwt.decode(
                token_clean,
                key=public_key,
                algorithms=list(self.allowed_algorithms),
                issuer=expected_issuers,
                audience=list(self.client_ids),
                leeway=self.clock_skew_seconds,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_nbf": True,
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("Token has expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise TokenNotYetValidError("Token is not yet valid (nbf in future)") from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidIssuerError(
                "Token issuer does not match approved provider issuer"
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidAudienceError(
                "Token audience does not match configured client IDs"
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidSignatureError(
                "Token cryptographic signature verification failed"
            ) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise InvalidRequiredClaimsError(f"Token missing required claim: {exc}") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise UnsupportedAlgorithmError(f"Invalid algorithm: {exc}") from exc
        except jwt.InvalidTokenError as exc:
            raise MalformedTokenError("Invalid token claims or structure") from exc

        # 6. Detailed claim semantics & safety
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise InvalidRequiredClaimsError(
                "Token 'sub' claim must not be empty or whitespace-only"
            )
        clean_sub = sub.strip()

        # Audience normalization & Multi-Audience / AZP validation
        raw_aud = payload.get("aud")
        if isinstance(raw_aud, str):
            clean_aud_list = [raw_aud.strip()]
        elif isinstance(raw_aud, (list, tuple, set, frozenset)):
            clean_aud_list = [str(a).strip() for a in raw_aud]
        else:
            raise InvalidAudienceError("Token 'aud' claim must be a string or list of strings")

        if not clean_aud_list or any(not a for a in clean_aud_list):
            raise InvalidAudienceError(
                "Token 'aud' claim contains empty or whitespace-only audience"
            )

        azp = payload.get("azp")
        if len(clean_aud_list) > 1:
            # Multi-audience token requires azp per OIDC Core 3.1.3.7
            if not azp or not isinstance(azp, str) or not azp.strip():
                raise InvalidAudienceError("Multi-audience token missing required 'azp' claim")
            clean_azp = azp.strip()
            if clean_azp not in self.client_ids:
                raise InvalidAudienceError("Authorized party 'azp' is not an approved client ID")
            if clean_azp not in set(clean_aud_list):
                raise InvalidAudienceError(
                    "Authorized party 'azp' is not present in token audiences"
                )
        elif azp is not None:
            if not isinstance(azp, str) or not azp.strip():
                raise InvalidAudienceError(
                    "Token 'azp' claim, if present, must be non-empty string"
                )
            clean_azp = azp.strip()
            if clean_azp not in self.client_ids:
                raise InvalidAudienceError("Authorized party 'azp' is not an approved client ID")

        # Nonce validation
        if expected_nonce is not None:
            token_nonce = payload.get("nonce")
            if token_nonce is None:
                raise NonceMismatchError("Token missing expected 'nonce' claim")
            if token_nonce != expected_nonce:
                raise NonceMismatchError("Token 'nonce' does not match expected nonce")

        # Hosted domain (hd) validation
        hd_raw = payload.get("hd")
        if self.allowed_hosted_domains:
            if not hd_raw or not isinstance(hd_raw, str) or not hd_raw.strip():
                raise HostedDomainRejectedError("Token missing required hosted domain ('hd') claim")
            clean_hd = hd_raw.strip().lower()
            if clean_hd not in self.allowed_hosted_domains:
                raise HostedDomainRejectedError("Token hosted domain is not permitted by allowlist")
            final_hd: str | None = clean_hd
        else:
            final_hd = (
                hd_raw.strip().lower() if (isinstance(hd_raw, str) and hd_raw.strip()) else None
            )

        # Email attribute (descriptive only)
        email_raw = payload.get("email")
        if email_raw is not None:
            if not isinstance(email_raw, str) or not email_raw.strip():
                raise InvalidRequiredClaimsError(
                    "Token 'email' claim, if present, must be a non-empty string"
                )
            final_email: str | None = email_raw.strip()
        else:
            final_email = None

        # Return frozen canonical VerifiedOidcIdentity
        return VerifiedOidcIdentity(
            issuer=payload["iss"],
            subject=clean_sub,
            audiences=frozenset(clean_aud_list),
            email=final_email,
            hosted_domain=final_hd,
        )
