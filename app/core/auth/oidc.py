"""Generic OpenID Connect (OIDC) cryptographic verification adapter.

Supports standards-compliant OpenID Connect providers (e.g. Keycloak, Okta,
Google Workspace) with:
- Dynamic discovery metadata resolution (<issuer>/.well-known/openid-configuration)
- Local cryptographic asymmetric signature validation (RS256)
- Monotonic caching of discovery and JWKS documents with Cache-Control support
- Audience, issuer, expiration, not-before, and claims verification
- Provider-neutral extraction of identity, roles, and preferred username
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
from app.core.config import Settings

logger = structlog.get_logger("app.core.auth.oidc")

ALLOWED_ID_TOKEN_ALGORITHMS: frozenset[str] = frozenset({"RS256"})
PROHIBITED_ALGORITHMS: frozenset[str] = frozenset(
    {"none", "ES256", "PS256", "RS384", "RS512", "HS256", "HS384", "HS512"}
)


# -----------------------------------------------------------------------------
# OIDC Verification Exception Taxonomy
# -----------------------------------------------------------------------------


class OidcVerificationError(Exception):
    """Base exception for all OIDC verification failures."""

    reason_code: str = "OIDC_VERIFICATION_FAILED"

    def __init__(self, message: str, reason_code: str | None = None) -> None:
        super().__init__(message)
        if reason_code:
            self.reason_code = reason_code


class MalformedTokenError(OidcVerificationError):
    reason_code = "MALFORMED_TOKEN"


class UnsupportedAlgorithmError(OidcVerificationError):
    reason_code = "UNSUPPORTED_ALGORITHM"


class SigningKeyNotFoundError(OidcVerificationError):
    reason_code = "SIGNING_KEY_NOT_FOUND"


class InvalidSignatureError(OidcVerificationError):
    reason_code = "INVALID_SIGNATURE"


class InvalidIssuerError(OidcVerificationError):
    reason_code = "INVALID_ISSUER"


class InvalidAudienceError(OidcVerificationError):
    reason_code = "INVALID_AUDIENCE"


class TokenExpiredError(OidcVerificationError):
    reason_code = "TOKEN_EXPIRED"


class TokenNotYetValidError(OidcVerificationError):
    reason_code = "TOKEN_NOT_YET_VALID"


class InvalidRequiredClaimsError(OidcVerificationError):
    reason_code = "INVALID_REQUIRED_CLAIMS"


class NonceMismatchError(OidcVerificationError):
    reason_code = "NONCE_MISMATCH"


class HostedDomainRejectedError(OidcVerificationError):
    reason_code = "HOSTED_DOMAIN_REJECTED"


class ProviderMetadataUnavailableError(OidcVerificationError):
    reason_code = "PROVIDER_METADATA_UNAVAILABLE"


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


class OidcVerifier:
    """Provider-neutral cryptographic verifier for standard OpenID Connect tokens.

    Supports local Keycloak, Google Workspace, or any compliant OIDC provider.
    Verifies JOSE headers, asymmetric signatures against cached JWKS, standard claims,
    and returns a provider-neutral VerifiedOidcIdentity.
    """

    def __init__(
        self,
        issuer_url: str | None = None,
        discovery_url: str | None = None,
        client_ids: Iterable[str] = (),
        allowed_issuers: Iterable[str] | None = None,
        allowed_jwks_hosts: Iterable[str] | None = None,
        allowed_hosted_domains: Iterable[str] | None = None,
        http_timeout_seconds: float = 10.0,
        clock_skew_seconds: int = 60,
        cache_fallback_seconds: int = 3600,
        cache_max_seconds: int = 86400,
        allowed_algorithms: Iterable[str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not issuer_url and not discovery_url:
            raise ValueError("OidcVerifier requires either issuer_url or discovery_url")

        if discovery_url and discovery_url.strip():
            self.discovery_url = discovery_url.strip()
        else:
            assert issuer_url is not None
            clean_issuer = issuer_url.strip().rstrip("/")
            self.discovery_url = f"{clean_issuer}/.well-known/openid-configuration"

        parsed_disc = urlparse(self.discovery_url)
        if not parsed_disc.scheme or not parsed_disc.netloc:
            raise ValueError(f"Invalid discovery_url format: '{self.discovery_url}'")

        cleaned_client_ids = [str(c).strip() for c in client_ids if str(c).strip()]
        if not cleaned_client_ids:
            raise ValueError("OidcVerifier requires at least one approved client ID / audience")
        self.client_ids: frozenset[str] = frozenset(cleaned_client_ids)

        if allowed_issuers:
            self.allowed_issuers: frozenset[str] = frozenset(
                str(i).strip().rstrip("/") for i in allowed_issuers if str(i).strip()
            )
        elif issuer_url:
            self.allowed_issuers = frozenset({issuer_url.strip().rstrip("/")})
        else:
            self.allowed_issuers = frozenset()

        if allowed_jwks_hosts:
            self.allowed_jwks_hosts: frozenset[str] = frozenset(
                str(h).strip().lower() for h in allowed_jwks_hosts if str(h).strip()
            )
        else:
            disc_host = (parsed_disc.hostname or "").lower()
            self.allowed_jwks_hosts = frozenset({disc_host}) if disc_host else frozenset()

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
            if any(alg in PROHIBITED_ALGORITHMS or alg.lower() == "none" for alg in cleaned_algs):
                raise ValueError("Prohibited algorithms (none, HMAC, etc.) cannot be used")
            self.allowed_algorithms = cleaned_algs
        else:
            self.allowed_algorithms = ALLOWED_ID_TOKEN_ALGORITHMS

        self.clock = clock
        self._http_client = http_client
        self._owns_http_client = http_client is None

        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

        self._discovery_cache: tuple[dict[str, Any], float] | None = None
        self._jwks_cache: tuple[jwt.PyJWKSet, float] | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "OidcVerifier":
        """Construct OidcVerifier directly from application Settings."""
        client_ids = (
            settings.oidc_client_ids
            or settings.oidc_allowed_audiences
            or settings.google_oidc_client_ids
        )
        return cls(
            issuer_url=settings.oidc_issuer_url,
            discovery_url=settings.oidc_discovery_url,
            client_ids=client_ids,
            allowed_issuers=settings.oidc_allowed_issuers or None,
            allowed_jwks_hosts=settings.oidc_allowed_jwks_hosts or None,
            allowed_hosted_domains=(
                settings.oidc_allowed_hosted_domains
                or (
                    settings.google_oidc_allowed_hosted_domains
                    if settings.oidc_provider_name.lower() == "google"
                    else None
                )
            ),
            http_timeout_seconds=settings.oidc_http_timeout_seconds,
            clock_skew_seconds=settings.oidc_clock_skew_seconds,
            cache_fallback_seconds=settings.oidc_cache_fallback_seconds,
            cache_max_seconds=settings.oidc_cache_max_seconds,
            http_client=http_client,
            clock=clock,
        )

    def _get_http_client(self) -> httpx.AsyncClient:
        """Retrieve active HTTP client or instantiate internal client."""
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

    async def __aenter__(self) -> "OidcVerifier":
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

            clean_issuer = issuer.strip().rstrip("/")
            if self.allowed_issuers and clean_issuer not in self.allowed_issuers:
                issuers_str = sorted(self.allowed_issuers)
                raise ProviderMetadataUnavailableError(
                    f"Discovery document issuer '{clean_issuer}' is not in approved issuers: "
                    f"{issuers_str}"
                )

            jwks_uri = data.get("jwks_uri")
            if not jwks_uri or not isinstance(jwks_uri, str) or not jwks_uri.strip():
                raise ProviderMetadataUnavailableError(
                    "Discovery document missing valid 'jwks_uri'"
                )

            clean_jwks_uri = jwks_uri.strip()
            parsed_jwks = urlparse(clean_jwks_uri)
            jwks_hostname = (parsed_jwks.hostname or "").lower()

            if self.allowed_jwks_hosts and jwks_hostname not in self.allowed_jwks_hosts:
                hosts_str = sorted(self.allowed_jwks_hosts)
                raise ProviderMetadataUnavailableError(
                    f"Discovery 'jwks_uri' host '{jwks_hostname}' is not in approved hosts: "
                    f"{hosts_str}"
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

            if not isinstance(jwks_data, dict) or not isinstance(jwks_data.get("keys"), list):
                raise ProviderMetadataUnavailableError("Malformed JWKS structure")

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
        """Lookup signing key by kid with controlled single forced refresh on miss."""
        jwk_set = await self.get_jwks(force_refresh=False)
        try:
            return jwk_set[kid]
        except KeyError:
            pass

        async with self._jwks_lock:
            now = self.clock()
            if self._jwks_cache is not None:
                cached_set, expiry = self._jwks_cache
                if now < expiry:
                    try:
                        return cached_set[kid]
                    except KeyError:
                        pass

            discovery = await self.get_discovery_metadata(force_refresh=False)
            jwks_uri = discovery["jwks_uri"]
            client = self._get_http_client()
            try:
                resp = await client.get(jwks_uri, follow_redirects=False)
            except Exception as exc:
                raise ProviderMetadataUnavailableError(
                    f"Failed to refresh JWKS document: {exc.__class__.__name__}"
                ) from exc

            if resp.status_code != 200:
                raise ProviderMetadataUnavailableError(
                    f"JWKS refresh returned HTTP {resp.status_code}"
                )

            try:
                jwks_data = resp.json()
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
        """Cryptographically verify OIDC ID token and return VerifiedOidcIdentity."""
        try:
            return await self._verify_id_token_internal(token, expected_nonce)
        except OidcVerificationError as exc:
            logger.warning(
                "oidc_verification_failed",
                reason_code=exc.reason_code,
                provider="oidc",
            )
            raise

    async def _verify_id_token_internal(
        self,
        token: str,
        expected_nonce: str | None,
    ) -> VerifiedOidcIdentity:
        if not isinstance(token, str) or not token.strip():
            raise MalformedTokenError("Token must be a non-empty string")
        token_clean = token.strip()
        if token_clean.count(".") != 2:
            raise MalformedTokenError("Malformed JWT token structure")

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

        signing_key = await self.get_signing_key(clean_kid)
        public_key = signing_key.key

        discovery = await self.get_discovery_metadata()
        expected_issuers = (
            list(self.allowed_issuers)
            if self.allowed_issuers
            else [discovery["issuer"].rstrip("/")]
        )

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

        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise InvalidRequiredClaimsError(
                "Token 'sub' claim must not be empty or whitespace-only"
            )
        clean_sub = sub.strip()

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

        if expected_nonce is not None:
            token_nonce = payload.get("nonce")
            if token_nonce is None:
                raise NonceMismatchError("Token missing expected 'nonce' claim")
            if token_nonce != expected_nonce:
                raise NonceMismatchError("Token 'nonce' does not match expected nonce")

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

        email_raw = payload.get("email")
        final_email = (
            email_raw.strip() if (isinstance(email_raw, str) and email_raw.strip()) else None
        )

        return VerifiedOidcIdentity(
            issuer=payload["iss"],
            subject=clean_sub,
            audiences=frozenset(clean_aud_list),
            email=final_email,
            hosted_domain=final_hd,
        )


__all__ = [
    "HostedDomainRejectedError",
    "InvalidAudienceError",
    "InvalidIssuerError",
    "InvalidRequiredClaimsError",
    "InvalidSignatureError",
    "MalformedTokenError",
    "NonceMismatchError",
    "OidcVerificationError",
    "OidcVerifier",
    "ProviderMetadataUnavailableError",
    "SigningKeyNotFoundError",
    "TokenExpiredError",
    "TokenNotYetValidError",
    "UnsupportedAlgorithmError",
]
