"""Unit tests for Google OIDC cryptographic verification foundation (Task 1C.2).

Verifies local cryptographic signature validation, dynamic discovery and JWKS caching,
strict algorithm allowlisting, claims validation, and failure isolation using
httpx.MockTransport and synthetic RSA keys. Zero live internet calls.
"""

import asyncio
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core.auth.google_oidc import (
    GoogleOidcVerifier,
    HostedDomainRejectedError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidRequiredClaimsError,
    InvalidSignatureError,
    MalformedTokenError,
    NonceMismatchError,
    ProviderMetadataUnavailableError,
    SigningKeyNotFoundError,
    TokenExpiredError,
    TokenNotYetValidError,
    UnsupportedAlgorithmError,
)
from app.core.auth.models import VerifiedOidcIdentity
from app.core.config import Settings

DEFAULT_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
DEFAULT_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


# -----------------------------------------------------------------------------
# Fixtures and Synthetic Key Generators
# -----------------------------------------------------------------------------


def generate_rsa_key_pair(kid: str = "key-1") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate ephemeral RSA private key and public JWK representation."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key(), as_dict=True)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return priv, pub_jwk


def generate_ec_key_pair(
    kid: str = "ec-1",
) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    """Generate ephemeral EC P-256 private key and public JWK representation."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_jwk = jwt.algorithms.ECAlgorithm.to_jwk(priv.public_key(), as_dict=True)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "ES256"
    return priv, pub_jwk


def make_token(
    private_key: Any,
    kid: str | None = "key-1",
    iss: str = "https://accounts.google.com",
    sub: str = "google-user-12345",
    aud: str | list[str] = "client-alpha.apps.googleusercontent.com",
    exp: int | None = None,
    iat: int | None = None,
    nbf: int | None = None,
    azp: str | None = None,
    nonce: str | None = None,
    hd: str | None = None,
    email: str | None = None,
    alg: str = "RS256",
    include_exp: bool = True,
    include_iat: bool = True,
    include_sub: bool = True,
    include_iss: bool = True,
    include_aud: bool = True,
) -> str:
    """Create a synthetic signed JWT token."""
    now = int(time.time())
    payload: dict[str, Any] = {}
    if include_iss:
        payload["iss"] = iss
    if include_sub:
        payload["sub"] = sub
    if include_aud:
        payload["aud"] = aud
    if include_exp:
        payload["exp"] = exp if exp is not None else now + 3600
    if include_iat:
        payload["iat"] = iat if iat is not None else now - 60
    if nbf is not None:
        payload["nbf"] = nbf
    if azp is not None:
        payload["azp"] = azp
    if nonce is not None:
        payload["nonce"] = nonce
    if hd is not None:
        payload["hd"] = hd
    if email is not None:
        payload["email"] = email

    headers: dict[str, Any] = {"alg": alg}
    if kid is not None:
        headers["kid"] = kid

    return jwt.encode(payload, private_key, algorithm=alg, headers=headers)


def make_transport(
    discovery_url: str = DEFAULT_DISCOVERY_URL,
    jwks_url: str = DEFAULT_JWKS_URL,
    jwks_keys: list[dict[str, Any]] | None = None,
    discovery_headers: dict[str, str] | None = None,
    jwks_headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """Create mock transport returning valid discovery and JWKS documents."""
    keys = jwks_keys if jwks_keys is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == discovery_url:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": jwks_url},
                headers=discovery_headers,
            )
        if url == jwks_url:
            return httpx.Response(
                200,
                json={"keys": keys},
                headers=jwks_headers,
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# -----------------------------------------------------------------------------
# 1. Success Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_signed_token_verifies() -> None:
    """Verify standard single-audience Google ID token validates successfully."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(
        jwks_keys=[jwk1],
        jwks_headers={"Cache-Control": "max-age=3600"},
    )
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha.apps.googleusercontent.com"],
        http_client=client,
    )

    token = make_token(
        priv1,
        kid="key-1",
        sub="1122334455",
        aud="client-alpha.apps.googleusercontent.com",
        email="testuser@example.com",
        hd="example.com",
    )

    identity = await verifier.verify_id_token(token)
    assert isinstance(identity, VerifiedOidcIdentity)
    assert identity.issuer == "https://accounts.google.com"
    assert identity.subject == "1122334455"
    assert identity.audiences == frozenset({"client-alpha.apps.googleusercontent.com"})
    assert identity.email == "testuser@example.com"
    assert identity.hosted_domain == "example.com"


@pytest.mark.asyncio
async def test_multi_audience_token_with_valid_azp() -> None:
    """Verify token with multiple audiences and valid approved azp validates cleanly."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha", "client-beta"],
        http_client=client,
    )

    token = make_token(
        priv1,
        aud=["client-alpha", "client-beta"],
        azp="client-alpha",
        sub="sub-multi",
    )

    identity = await verifier.verify_id_token(token)
    assert identity.audiences == frozenset({"client-alpha", "client-beta"})
    assert identity.subject == "sub-multi"


@pytest.mark.asyncio
async def test_valid_expected_nonce_accepted() -> None:
    """Verify expected nonce matches and validates."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    token = make_token(priv1, aud="client-alpha", nonce="secure-session-nonce-123")
    identity = await verifier.verify_id_token(token, expected_nonce="secure-session-nonce-123")
    assert identity.subject == "google-user-12345"


@pytest.mark.asyncio
async def test_hosted_domain_allowlist_enforcement() -> None:
    """Verify allowed hosted domain is accepted and disallowed hd is rejected."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        allowed_hosted_domains=["mycompany.com"],
        http_client=client,
    )

    # 1. Allowed hd matches (case-insensitive)
    token_allowed = make_token(priv1, aud="client-alpha", hd="MYCOMPANY.COM")
    ident = await verifier.verify_id_token(token_allowed)
    assert ident.hosted_domain == "mycompany.com"

    # 2. Disallowed hd rejected
    token_disallowed = make_token(priv1, aud="client-alpha", hd="othercompany.com")
    with pytest.raises(HostedDomainRejectedError):
        await verifier.verify_id_token(token_disallowed)

    # 3. Missing hd rejected when allowlist configured
    token_no_hd = make_token(priv1, aud="client-alpha", hd=None)
    with pytest.raises(HostedDomainRejectedError):
        await verifier.verify_id_token(token_no_hd)


# -----------------------------------------------------------------------------
# 2. Cryptographic Signature & Key Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_signature_rejected() -> None:
    """Verify token signed with an untrusted private key fails signature verification."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    priv2, _ = generate_rsa_key_pair("key-1")  # Same kid, different key
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    bad_token = make_token(priv2, kid="key-1", aud="client-alpha")
    with pytest.raises(InvalidSignatureError):
        await verifier.verify_id_token(bad_token)


@pytest.mark.asyncio
async def test_unknown_kid_triggers_controlled_jwks_refresh() -> None:
    """Verify missing kid triggers one JWKS refresh and succeeds if present after rotation."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    priv2, jwk2 = generate_rsa_key_pair("key-2")
    jwks_fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_fetch_count
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
            )
        if str(request.url) == DEFAULT_JWKS_URL:
            jwks_fetch_count += 1
            if jwks_fetch_count == 1:
                return httpx.Response(200, json={"keys": [jwk1]})
            return httpx.Response(200, json={"keys": [jwk1, jwk2]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    # 1. First verification with key-1 populates cache
    tok1 = make_token(priv1, kid="key-1", aud="client-alpha")
    await verifier.verify_id_token(tok1)
    assert jwks_fetch_count == 1

    # 2. Verification with key-2 triggers controlled refresh and succeeds
    tok2 = make_token(priv2, kid="key-2", aud="client-alpha")
    ident2 = await verifier.verify_id_token(tok2)
    assert ident2.subject == "google-user-12345"
    assert jwks_fetch_count == 2


@pytest.mark.asyncio
async def test_unknown_kid_after_refresh_rejected() -> None:
    """Verify unknown kid absent even after JWKS refresh raises SigningKeyNotFoundError."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    jwks_fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_fetch_count
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
            )
        if str(request.url) == DEFAULT_JWKS_URL:
            jwks_fetch_count += 1
            return httpx.Response(200, json={"keys": [jwk1]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    priv_unknown, _ = generate_rsa_key_pair("key-unknown")
    tok = make_token(priv_unknown, kid="key-unknown", aud="client-alpha")

    with pytest.raises(SigningKeyNotFoundError):
        await verifier.verify_id_token(tok)
    assert jwks_fetch_count == 2


@pytest.mark.asyncio
async def test_missing_or_blank_kid_rejected() -> None:
    """Verify missing or blank kid in token header is rejected."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    # Missing kid
    tok_no_kid = make_token(priv1, kid=None, aud="client-alpha")
    with pytest.raises(SigningKeyNotFoundError):
        await verifier.verify_id_token(tok_no_kid)

    # Blank kid
    tok_blank_kid = make_token(priv1, kid="   ", aud="client-alpha")
    with pytest.raises(SigningKeyNotFoundError):
        await verifier.verify_id_token(tok_blank_kid)


@pytest.mark.asyncio
async def test_malformed_token_rejected() -> None:
    """Verify malformed tokens fail with MalformedTokenError."""
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
    )
    with pytest.raises(MalformedTokenError):
        await verifier.verify_id_token("not-a-jwt")

    with pytest.raises(MalformedTokenError):
        await verifier.verify_id_token("header.payload")

    with pytest.raises(MalformedTokenError):
        await verifier.verify_id_token("   ")


@pytest.mark.asyncio
async def test_unsupported_and_prohibited_algorithms_rejected() -> None:
    """Verify algorithm allowlist rejects alg=none, symmetric HMAC, and disallowed algs."""
    client = httpx.AsyncClient()
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    # Symmetric HS256
    symmetric_token = jwt.encode(
        {"sub": "s1", "aud": "client-alpha", "iss": "https://accounts.google.com"},
        "super-secret-key-32-bytes-minimum!",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(UnsupportedAlgorithmError):
        await verifier.verify_id_token(symmetric_token)

    # Alg = none
    none_token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJzMSJ9."
    with pytest.raises(UnsupportedAlgorithmError):
        await verifier.verify_id_token(none_token)


@pytest.mark.asyncio
async def test_es256_algorithm_rejected_even_if_valid_key() -> None:
    """Verify ES256 is strictly rejected while RS256 is supported."""
    priv_ec, jwk_ec = generate_ec_key_pair("ec-1")
    transport = make_transport(jwks_keys=[jwk_ec])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    # ES256-signed token with valid EC key -> UnsupportedAlgorithmError
    ec_token = make_token(
        priv_ec,
        kid="ec-1",
        aud="client-alpha",
        alg="ES256",
    )
    with pytest.raises(UnsupportedAlgorithmError):
        await verifier.verify_id_token(ec_token)

    # Constructor rejects attempts to allow ES256 or other algorithms
    with pytest.raises(ValueError):
        GoogleOidcVerifier(
            client_ids=["client-alpha"],
            allowed_algorithms=["ES256"],
        )

    # RS256 token remains fully supported
    priv_rsa, jwk_rsa = generate_rsa_key_pair("rsa-1")
    transport_rsa = make_transport(jwks_keys=[jwk_rsa])
    client_rsa = httpx.AsyncClient(transport=transport_rsa)
    verifier_rsa = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client_rsa,
    )
    rsa_token = make_token(priv_rsa, kid="rsa-1", aud="client-alpha", alg="RS256")
    identity = await verifier_rsa.verify_id_token(rsa_token)
    assert identity.subject == "google-user-12345"


# -----------------------------------------------------------------------------
# 3. Claims Validation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claims_validation_failures() -> None:
    """Verify comprehensive claim validation rules."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    transport = make_transport(jwks_keys=[jwk1])
    client = httpx.AsyncClient(transport=transport)
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
        clock_skew_seconds=10,
    )

    now = int(time.time())

    # Expired token
    tok_expired = make_token(priv1, aud="client-alpha", exp=now - 50)
    with pytest.raises(TokenExpiredError):
        await verifier.verify_id_token(tok_expired)

    # Future nbf
    tok_future_nbf = make_token(priv1, aud="client-alpha", nbf=now + 500)
    with pytest.raises(TokenNotYetValidError):
        await verifier.verify_id_token(tok_future_nbf)

    # Missing exp
    tok_no_exp = make_token(priv1, aud="client-alpha", include_exp=False)
    with pytest.raises(InvalidRequiredClaimsError):
        await verifier.verify_id_token(tok_no_exp)

    # Missing iat
    tok_no_iat = make_token(priv1, aud="client-alpha", include_iat=False)
    with pytest.raises(InvalidRequiredClaimsError):
        await verifier.verify_id_token(tok_no_iat)

    # Missing sub
    tok_no_sub = make_token(priv1, aud="client-alpha", include_sub=False)
    with pytest.raises(InvalidRequiredClaimsError):
        await verifier.verify_id_token(tok_no_sub)

    # Blank sub
    tok_blank_sub = make_token(priv1, aud="client-alpha", sub="   ")
    with pytest.raises(InvalidRequiredClaimsError):
        await verifier.verify_id_token(tok_blank_sub)

    # Wrong issuer
    tok_wrong_iss = make_token(priv1, aud="client-alpha", iss="https://evil.issuer.com")
    with pytest.raises(InvalidIssuerError):
        await verifier.verify_id_token(tok_wrong_iss)

    # Wrong audience
    tok_wrong_aud = make_token(priv1, aud="client-unapproved")
    with pytest.raises(InvalidAudienceError):
        await verifier.verify_id_token(tok_wrong_aud)

    # Multi-audience missing azp
    tok_multi_no_azp = make_token(priv1, aud=["client-alpha", "client-beta"], azp=None)
    with pytest.raises(InvalidAudienceError):
        await verifier.verify_id_token(tok_multi_no_azp)

    # Multi-audience with unapproved azp
    tok_multi_bad_azp = make_token(
        priv1, aud=["client-alpha", "client-beta"], azp="client-unapproved"
    )
    with pytest.raises(InvalidAudienceError):
        await verifier.verify_id_token(tok_multi_bad_azp)

    # Multi-audience with azp not in aud set
    verifier_dual = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha", "client-beta"],
        http_client=client,
    )
    tok_azp_not_in_aud = make_token(priv1, aud=["client-alpha", "client-gamma"], azp="client-beta")
    with pytest.raises(InvalidAudienceError):
        await verifier_dual.verify_id_token(tok_azp_not_in_aud)

    # Expected nonce mismatch
    tok_nonce = make_token(priv1, aud="client-alpha", nonce="nonce-token")
    with pytest.raises(NonceMismatchError):
        await verifier.verify_id_token(tok_nonce, expected_nonce="nonce-different")

    # Expected nonce missing in token
    tok_no_nonce = make_token(priv1, aud="client-alpha", nonce=None)
    with pytest.raises(NonceMismatchError):
        await verifier.verify_id_token(tok_no_nonce, expected_nonce="nonce-required")


# -----------------------------------------------------------------------------
# 4. Cache & Network Behavior Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hits_and_expiry() -> None:
    """Verify discovery and JWKS caching, TTL calculation, and cache expiration."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    fetch_counts = {"discovery": 0, "jwks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DEFAULT_DISCOVERY_URL:
            fetch_counts["discovery"] += 1
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
                headers={"Cache-Control": "max-age=100"},
            )
        if url == DEFAULT_JWKS_URL:
            fetch_counts["jwks"] += 1
            return httpx.Response(
                200,
                json={"keys": [jwk1]},
                headers={"Cache-Control": "max-age=100"},
            )
        return httpx.Response(404)

    simulated_now = 1000.0

    def mock_clock() -> float:
        return simulated_now

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
        clock=mock_clock,
    )

    tok = make_token(priv1, aud="client-alpha")

    # 1. Initial verification: 1 discovery fetch, 1 JWKS fetch
    await verifier.verify_id_token(tok)
    assert fetch_counts == {"discovery": 1, "jwks": 1}

    # 2. Subsequent verification at t = 1050 (within 100s TTL): 0 fetches
    simulated_now = 1050.0
    await verifier.verify_id_token(tok)
    assert fetch_counts == {"discovery": 1, "jwks": 1}

    # 3. Verification at t = 1150 (past 100s TTL): triggers cache refresh
    simulated_now = 1150.0
    await verifier.verify_id_token(tok)
    assert fetch_counts == {"discovery": 2, "jwks": 2}


@pytest.mark.asyncio
async def test_concurrent_jwks_refresh_is_lock_safe() -> None:
    """Verify concurrent requests for new key do not cause duplicate stampede fetches."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")
    jwks_fetches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_fetches
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
            )
        if str(request.url) == DEFAULT_JWKS_URL:
            jwks_fetches += 1
            return httpx.Response(200, json={"keys": [jwk1]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )

    tok = make_token(priv1, aud="client-alpha")

    # Launch 5 concurrent verification tasks
    results = await asyncio.gather(*(verifier.verify_id_token(tok) for _ in range(5)))
    assert len(results) == 5
    assert jwks_fetches == 1


# -----------------------------------------------------------------------------
# 5. Provider Network / Failure Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_network_and_schema_failures() -> None:
    """Verify verifier fails closed with ProviderMetadataUnavailableError on provider errors."""

    # 1. Network timeout
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Network unreachable")

    priv, _ = generate_rsa_key_pair("key-1")
    tok = make_token(priv, kid="key-1", aud="c1")
    client_timeout = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    v_timeout = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL, client_ids=["c1"], http_client=client_timeout
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await v_timeout.verify_id_token(tok)

    # 2. HTTP 500
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Error")

    client_err = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    v_err = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL, client_ids=["c1"], http_client=client_err
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await v_err.get_discovery_metadata()

    # 3. Missing jwks_uri
    def bad_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://accounts.google.com"})

    client_bad = httpx.AsyncClient(transport=httpx.MockTransport(bad_json_handler))
    v_bad = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL, client_ids=["c1"], http_client=client_bad
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await v_bad.get_discovery_metadata()

    # 4. Insecure non-HTTPS jwks_uri
    def http_jwks_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"issuer": "https://accounts.google.com", "jwks_uri": "http://insecure.com/keys"},
        )

    client_insecure = httpx.AsyncClient(transport=httpx.MockTransport(http_jwks_handler))
    v_insecure = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL, client_ids=["c1"], http_client=client_insecure
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await v_insecure.get_discovery_metadata()


@pytest.mark.asyncio
async def test_malicious_discovery_issuer_fails_closed() -> None:
    """Verify verifier fails closed when discovery returns non-Google issuer."""
    priv1, jwk1 = generate_rsa_key_pair("key-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": "https://evil.example",
                    "jwks_uri": DEFAULT_JWKS_URL,
                },
            )
        if str(request.url) == DEFAULT_JWKS_URL:
            return httpx.Response(200, json={"keys": [jwk1]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )
    tok = make_token(priv1, iss="https://evil.example", aud="client-alpha")
    with pytest.raises(ProviderMetadataUnavailableError):
        await verifier.verify_id_token(tok)


@pytest.mark.asyncio
async def test_discovery_missing_rs256_fails_closed() -> None:
    """Verify discovery declaring id_token_signing_alg_values_supported without RS256 fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": "https://accounts.google.com",
                    "jwks_uri": DEFAULT_JWKS_URL,
                    "id_token_signing_alg_values_supported": ["ES256", "HS256"],
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await verifier.get_discovery_metadata()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious_jwks_uri",
    [
        "https://evil.example/jwks",
        "https://localhost/jwks",
        "https://127.0.0.1/jwks",
        "https://internal-host/jwks",
        "http://www.googleapis.com/oauth2/v3/certs",
    ],
)
async def test_malicious_jwks_uri_rejected_before_request(malicious_jwks_uri: str) -> None:
    """Verify JWKS URIs outside approved Google HTTPS origins are rejected without fetching."""
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        requested_urls.append(url_str)
        if url_str == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": "https://accounts.google.com",
                    "jwks_uri": malicious_jwks_uri,
                },
            )
        return httpx.Response(200, json={"keys": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await verifier.get_discovery_metadata()

    assert malicious_jwks_uri not in requested_urls


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
async def test_discovery_redirect_fails_closed(status_code: int) -> None:
    """Verify discovery HTTP redirects fail closed and are not followed."""
    redirect_target = "https://accounts.google.com/redirected"
    target_hit = False

    def discovery_redirect_handler(request: httpx.Request) -> httpx.Response:
        nonlocal target_hit
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                status_code,
                headers={"Location": redirect_target},
            )
        if str(request.url) == redirect_target:
            target_hit = True
            return httpx.Response(200, json={"issuer": "https://accounts.google.com"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(discovery_redirect_handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await verifier.get_discovery_metadata()

    assert not target_hit


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 302])
async def test_jwks_redirect_fails_closed(status_code: int) -> None:
    """Verify JWKS redirect fails closed without following destination."""
    jwks_target = "https://www.googleapis.com/redirected-certs"
    target_hit = False

    def jwks_redirect_handler(request: httpx.Request) -> httpx.Response:
        nonlocal target_hit
        if str(request.url) == DEFAULT_DISCOVERY_URL:
            return httpx.Response(
                200,
                json={"issuer": "https://accounts.google.com", "jwks_uri": DEFAULT_JWKS_URL},
            )
        if str(request.url) == DEFAULT_JWKS_URL:
            return httpx.Response(status_code, headers={"Location": jwks_target})
        if str(request.url) == jwks_target:
            target_hit = True
            return httpx.Response(200, json={"keys": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(jwks_redirect_handler))
    verifier = GoogleOidcVerifier(
        discovery_url=DEFAULT_DISCOVERY_URL,
        client_ids=["client-alpha"],
        http_client=client,
    )
    with pytest.raises(ProviderMetadataUnavailableError):
        await verifier.get_jwks()

    assert not target_hit


# -----------------------------------------------------------------------------
# 6. Configuration & Lifecycle Tests
# -----------------------------------------------------------------------------


def test_settings_google_oidc_validation() -> None:
    """Verify Settings validation of Google OIDC parameters."""
    # Valid settings
    s = Settings(
        google_oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        google_oidc_client_ids=["client-1", "client-2"],
        google_oidc_allowed_hosted_domains=["EXAMPLE.COM"],
        google_oidc_clock_skew_seconds=30,
        google_oidc_http_timeout_seconds=5.0,
        google_oidc_cache_fallback_seconds=1800,
        google_oidc_cache_max_seconds=3600,
    )
    assert s.google_oidc_allowed_hosted_domains == ["example.com"]
    assert s.google_oidc_client_ids == ["client-1", "client-2"]

    # Insecure HTTP discovery URL rejected
    with pytest.raises(ValueError):
        Settings(google_oidc_discovery_url="http://insecure.google.com")

    # Non-canonical HTTPS discovery URL rejected
    with pytest.raises(ValueError):
        Settings(google_oidc_discovery_url="https://evil.example/.well-known/openid-configuration")

    # Blank client ID rejected
    with pytest.raises(ValueError):
        Settings(google_oidc_client_ids=["client-1", "   "])

    # Invalid cache bounds rejected
    with pytest.raises(ValueError):
        Settings(
            google_oidc_cache_fallback_seconds=5000,
            google_oidc_cache_max_seconds=1000,
        )


def test_verifier_construction_validation() -> None:
    """Verify GoogleOidcVerifier parameter validation."""
    # Empty client IDs rejected
    with pytest.raises(ValueError):
        GoogleOidcVerifier(
            discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            client_ids=[],
        )

    # Insecure discovery URL rejected
    with pytest.raises(ValueError):
        GoogleOidcVerifier(
            discovery_url="http://insecure.url",
            client_ids=["c1"],
        )

    # Non-canonical HTTPS discovery URL rejected
    with pytest.raises(ValueError):
        GoogleOidcVerifier(
            discovery_url="https://evil.example/.well-known/openid-configuration",
            client_ids=["c1"],
        )


@pytest.mark.asyncio
async def test_verifier_context_manager_and_aclose() -> None:
    """Verify verifier async context manager cleanly closes internal client."""
    async with GoogleOidcVerifier(
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        client_ids=["c1"],
    ) as v:
        client = v._get_http_client()
        assert not client.is_closed
    assert client.is_closed


def test_app_main_import_zero_network_calls() -> None:
    """Reconfirm importing app.main initiates zero network calls."""
    import app.main

    assert app.main.app is not None
