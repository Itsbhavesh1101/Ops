"""Verify corporate OIDC discovery, issuer consistency, and signing keys."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from scripts.verification.probe_common import finish_cli, run_bounded_probe


def evaluate_metadata(
    expected_issuer: str, discovery: dict[str, Any], jwks: dict[str, Any]
) -> dict[str, Any]:
    required_endpoints = ("jwks_uri", "authorization_endpoint", "token_endpoint")
    issuer_match = discovery.get("issuer") == expected_issuer.rstrip("/")
    endpoints_secure = all(
        urlparse(str(discovery.get(name, ""))).scheme == "https" for name in required_endpoints
    )
    signing_keys = [
        key
        for key in jwks.get("keys", [])
        if key.get("kty") == "RSA" and key.get("kid") and key.get("n") and key.get("e")
    ]
    observed = {
        "issuer_match": issuer_match,
        "secure_endpoints": endpoints_secure,
        "usable_signing_key_count": len(signing_keys),
    }
    return {"status": "PASS" if all(observed.values()) else "FAIL", "observed": observed}


async def probe_oidc() -> dict[str, Any]:
    issuer = os.environ["E7_OIDC_ISSUER"].rstrip("/")
    http_timeout = float(os.environ.get("E7_OIDC_HTTP_TIMEOUT", "5"))
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        discovery_response = await client.get(f"{issuer}/.well-known/openid-configuration")
        discovery_response.raise_for_status()
        discovery = discovery_response.json()
        jwks_response = await client.get(str(discovery["jwks_uri"]))
        jwks_response.raise_for_status()
        return evaluate_metadata(issuer, discovery, jwks_response.json())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    payload = asyncio.run(
        run_bounded_probe(
            "PRODUCTION_COMPANY_OIDC", probe_oidc(), timeout_seconds=args.timeout
        )
    )
    return finish_cli(payload, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
