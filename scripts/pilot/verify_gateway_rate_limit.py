#!/usr/bin/env python3
"""Pilot Gateway Edge Rate Limiting Verification Script (Phase 8I).

Validates:
1. Normal request to gateway succeeds (HTTP 200) with expected health payload.
2. Concurrent burst exceeds capacity producing HTTP 429 Too Many Requests.
3. HTTP 429 response contains 'Retry-After' header.
4. Zero unexpected 5xx status codes during rate-limiting enforcement.
5. Gateway recovers after token bucket reset period (HTTP 200).
6. Non-dependence on external internet services.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def make_request(url: str, timeout: float = 5.0) -> tuple[int, dict[str, str], str]:
    """Execute a single HTTP request and return (status_code, headers, body)."""
    req = Request(url, headers={"User-Agent": "E7-Pilot-Gateway-Verifier/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, headers, body
    except HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()}
        body = e.read().decode("utf-8", errors="replace")
        return e.code, headers, body
    except URLError as e:
        return 0, {}, str(e.reason)
    except Exception as e:
        return 0, {}, str(e)


def run_verification(base_url: str = "http://127.0.0.1:8088") -> dict:
    health_url = f"{base_url.rstrip('/')}/api/v1/health"
    results = {
        "base_url": base_url,
        "health_url": health_url,
        "normal_request": {},
        "burst_test": {},
        "recovery_test": {},
        "all_passed": False,
    }

    print(f"[1/4] Verifying normal request to {health_url}...")
    code, headers, body = make_request(health_url)
    results["normal_request"] = {
        "status_code": code,
        "headers": headers,
        "body_preview": body[:200],
    }

    if code != 200:
        print(f"FAILED: Expected HTTP 200, got {code}: {body}")
        return results

    try:
        payload = json.loads(body)
        print(f"  OK: HTTP 200 received: status={payload.get('status')}")
    except Exception:
        print(f"  OK: HTTP 200 received: {body[:80]}")

    # Burst test
    # Configured: 10r/s rate, burst=20 nodelay
    # A burst of 60 parallel requests will exceed capacity and trigger 429
    burst_count = 60
    concurrency = 30
    print(f"\n[2/4] Executing burst test ({burst_count} parallel requests, concurrency={concurrency})...")

    status_counts: dict[int, int] = {}
    retry_after_seen = False
    retry_after_value = None

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(make_request, health_url) for _ in range(burst_count)]
        for fut in as_completed(futures):
            c, hdrs, b = fut.result()
            status_counts[c] = status_counts.get(c, 0) + 1
            if c == 429:
                ra = hdrs.get("retry-after")
                if ra:
                    retry_after_seen = True
                    retry_after_value = ra

    count_200 = status_counts.get(200, 0)
    count_429 = status_counts.get(429, 0)
    count_unexpected = sum(cnt for st, cnt in status_counts.items() if st not in (200, 429))

    print(f"  Counts: HTTP 200={count_200}, HTTP 429={count_429}, Unexpected={count_unexpected}")
    print(f"  All status codes seen: {status_counts}")
    print(f"  Retry-After header verified on 429: {retry_after_seen} (Value: '{retry_after_value}')")

    results["burst_test"] = {
        "requests_sent": burst_count,
        "status_counts": status_counts,
        "count_200": count_200,
        "count_429": count_429,
        "count_unexpected": count_unexpected,
        "retry_after_verified": retry_after_seen,
        "retry_after_value": retry_after_value,
    }

    if count_200 == 0:
        print("FAILED: Burst test had 0 successful requests (expected around 20-25).")
        return results

    if count_429 == 0:
        print("FAILED: Burst test had 0 rate-limited 429 requests.")
        return results

    if not retry_after_seen:
        print("FAILED: HTTP 429 response did NOT include required Retry-After header.")
        return results

    if count_unexpected > 0:
        print(f"FAILED: Unexpected status codes encountered: {status_counts}")
        return results

    # Wait for rate limit token bucket recovery
    # At 10r/s, waiting 2.5s guarantees burst capacity (20 tokens) is completely restored
    wait_seconds = 2.5
    print(f"\n[3/4] Waiting {wait_seconds}s for rate-limit bucket replenishment...")
    time.sleep(wait_seconds)

    print(f"[4/4] Verifying post-burst recovery on {health_url}...")
    rec_code, rec_headers, rec_body = make_request(health_url)
    results["recovery_test"] = {
        "status_code": rec_code,
        "body_preview": rec_body[:200],
    }

    if rec_code == 200:
        print(f"  OK: Gateway recovered successfully (HTTP 200).")
        results["all_passed"] = True
    else:
        print(f"FAILED: Post-burst recovery failed. Expected HTTP 200, got {rec_code}: {rec_body}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify pilot gateway edge rate limiting.")
    parser.add_argument("--url", default="http://127.0.0.1:8088", help="Gateway base URL (default: http://127.0.0.1:8088)")
    parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    args = parser.parse_args()

    res = run_verification(args.url)

    if args.json:
        print("\n" + json.dumps(res, indent=2))

    if res.get("all_passed"):
        print("\n=== PILOT GATEWAY RATE LIMITING VERIFICATION: PASS ===")
        return 0
    else:
        print("\n=== PILOT GATEWAY RATE LIMITING VERIFICATION: FAIL ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())
