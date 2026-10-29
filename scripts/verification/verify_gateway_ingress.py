"""Verify production gateway TLS, security headers, rate limiting, and WAF."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from scripts.verification.probe_common import finish_cli, run_bounded_probe

REQUIRED_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
)


def evaluate_gateway(
    *,
    scheme: str,
    tls_version: str,
    headers: dict[str, str],
    burst_statuses: list[int],
    waf_status: int,
) -> dict[str, Any]:
    normalized_headers = {name.lower(): value for name, value in headers.items()}
    headers_present = {name: name in normalized_headers for name in REQUIRED_HEADERS}
    observed = {
        "scheme": scheme,
        "tls_version": tls_version,
        "headers": headers_present,
        "burst_statuses": burst_statuses,
        "waf_status": waf_status,
    }
    passed = (
        scheme == "https"
        and tls_version == "TLSv1.3"
        and all(headers_present.values())
        and 429 in burst_statuses
        and waf_status in {403, 406}
        and not any(status >= 500 for status in burst_statuses)
    )
    return {"status": "PASS" if passed else "FAIL", "observed": observed}


def _tls_version(host: str, port: int, timeout: float) -> str:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
            return str(tls_socket.version())


async def probe_gateway() -> dict[str, Any]:
    base_url = os.environ["E7_PRODUCTION_GATEWAY_URL"].rstrip("/")
    parsed = urlparse(base_url)
    timeout = float(os.environ.get("E7_GATEWAY_HTTP_TIMEOUT", "5"))
    tls_version = await asyncio.to_thread(
        _tls_version, str(parsed.hostname), parsed.port or 443, timeout
    )
    health_path = os.environ.get("E7_GATEWAY_HEALTH_PATH", "/api/v1/health")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        health = await client.get(f"{base_url}{health_path}")
        health.raise_for_status()
        burst_count = int(os.environ.get("E7_GATEWAY_BURST_COUNT", "30"))
        burst = await asyncio.gather(
            *(client.get(f"{base_url}{health_path}") for _ in range(burst_count))
        )
        waf = await client.get(f"{base_url}{health_path}?e7_waf_probe=%27%20OR%201%3D1--")
    return evaluate_gateway(
        scheme=parsed.scheme,
        tls_version=tls_version,
        headers=dict(health.headers),
        burst_statuses=[response.status_code for response in burst],
        waf_status=waf.status_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    payload = asyncio.run(
        run_bounded_probe(
            "PRODUCTION_GATEWAY", probe_gateway(), timeout_seconds=args.timeout
        )
    )
    return finish_cli(payload, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
