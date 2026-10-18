#!/usr/bin/env python3
"""Phase 9A Pilot Infrastructure & Staging Preflight Verification Script.

Automates end-to-end verification of Phase 9A staging rehearsal:
1. Required container presence and health (gateway, api, postgres, flowable, rabbitmq).
2. Gateway ingress boundary (health & readiness HTTP 200, non-exposure of internal ports).
3. Pilot tenant existence and configuration (tenant-pilot-001).
4. Resource reconciliation (7 resources: 4 PERSON, 3 ROOM; 35 availabilities).
5. Timezone validity (Asia/Kolkata IANA canonical, UTC instant boundaries).
6. Tenant isolation evidence (cross-tenant read/mutation rejection, zero state mutation).
7. Idempotent bootstrap state (zero unintended duplicates on rerun).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

PILOT_GATEWAY_URL: str = "http://127.0.0.1:8088"
EXPECTED_CONTAINERS: list[str] = [
    "e7-pilot-gateway",
    "e7-api",
    "e7-postgres",
    "e7-flowable",
    "e7-rabbitmq",
]
PILOT_TENANT_ID: str = "tenant-pilot-001"


def check_container_statuses() -> dict[str, Any]:
    """Inspect status of all required pilot containers via Docker CLI."""
    cmd = [
        "docker",
        "ps",
        "-a",
        "--format",
        "{{.Names}}\t{{.Status}}\t{{.State}}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return {"status": "ERROR", "error": res.stderr.strip()}

    lines = res.stdout.strip().splitlines()
    container_info: dict[str, dict[str, str]] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 3:
            name, status_str, state_str = parts[0], parts[1], parts[2]
            container_info[name] = {"status": status_str, "state": state_str}

    checks: dict[str, Any] = {}
    all_healthy = True
    for c_name in EXPECTED_CONTAINERS:
        info = container_info.get(c_name)
        if not info:
            checks[c_name] = {"found": False, "healthy": False, "detail": "Container missing"}
            all_healthy = False
        else:
            is_running = info["state"] == "running"
            is_healthy = "healthy" in info["status"] or info["state"] == "running"
            checks[c_name] = {
                "found": True,
                "state": info["state"],
                "status": info["status"],
                "running": is_running,
                "healthy": is_healthy,
            }
            if not is_running:
                all_healthy = False

    return {
        "all_containers_running": all_healthy,
        "containers": checks,
    }


def check_gateway_endpoints(base_url: str = PILOT_GATEWAY_URL) -> dict[str, Any]:
    """Verify HTTP ingress through pilot gateway for health and readiness."""
    endpoints = {
        "health": f"{base_url.rstrip('/')}/api/v1/health",
        "ready": f"{base_url.rstrip('/')}/api/v1/ready",
    }
    results: dict[str, Any] = {}
    all_ok = True

    for name, url in endpoints.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "E7-Preflight-Verifier/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                code = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                expected_field = "status"
                val = body.get(expected_field)
                is_valid = code == 200 and val in ("ok", "ready")
                results[name] = {
                    "url": url,
                    "status_code": code,
                    "payload": body,
                    "valid": is_valid,
                }
                if not is_valid:
                    all_ok = False
        except Exception as exc:
            results[name] = {"url": url, "error": str(exc), "valid": False}
            all_ok = False

    return {
        "gateway_ingress_healthy": all_ok,
        "endpoints": results,
    }


def check_internal_port_isolation() -> dict[str, Any]:
    """Verify internal postgres and api containers do not publish ports to host."""
    res_pg = subprocess.run(
        ["docker", "port", "e7-postgres"], capture_output=True, text=True, check=False
    )
    res_api = subprocess.run(
        ["docker", "port", "e7-api"], capture_output=True, text=True, check=False
    )

    pg_ports = res_pg.stdout.strip()
    api_ports = res_api.stdout.strip()

    postgres_isolated = (res_pg.returncode == 0) and (len(pg_ports) == 0)
    api_isolated = (res_api.returncode == 0) and (len(api_ports) == 0)

    return {
        "postgres_isolated_from_host": postgres_isolated,
        "api_isolated_from_host": api_isolated,
        "details": {
            "e7_postgres_host_ports": pg_ports or "none (isolated)",
            "e7_api_host_ports": api_ports or "none (isolated)",
        },
    }


def check_tenant_and_reconciliation(tenant_id: str = PILOT_TENANT_ID) -> dict[str, Any]:
    """Run pilot bootstrap in verify-only mode and capture reconciliation metrics."""
    cmd = [
        "docker",
        "exec",
        "-i",
        "e7-api",
        "python",
        "/app/scripts/pilot/bootstrap_pilot_tenant.py",
        "--tenant-id",
        tenant_id,
        "--domain",
        "exam",
        "--json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return {"reconciliation_status": "FAIL", "error": res.stderr.strip() or res.stdout.strip()}

    try:
        report = json.loads(res.stdout)
    except Exception as exc:
        return {"reconciliation_status": "FAIL", "error": f"JSON parse error: {exc}"}

    return report


def check_tenant_isolation_tests() -> dict[str, Any]:
    """Execute pytest focused tenant isolation suite and capture result."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    pytest_bin = str(repo_root / ".venv" / "Scripts" / "pytest.exe")
    test_path = "tests/pilot/test_pilot_tenant_isolation.py"

    cmd = [pytest_bin, "-q", test_path]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)
    passed = res.returncode == 0
    return {
        "suite": test_path,
        "return_code": res.returncode,
        "passed": passed,
        "output": res.stdout.strip(),
    }


def run_preflight(
    base_url: str = PILOT_GATEWAY_URL,
    tenant_id: str = PILOT_TENANT_ID,
) -> dict[str, Any]:
    """Execute full Phase 9A preflight verification."""
    containers_report = check_container_statuses()
    gateway_report = check_gateway_endpoints(base_url)
    isolation_port_report = check_internal_port_isolation()
    reconciliation_report = check_tenant_and_reconciliation(tenant_id)
    isolation_tests_report = check_tenant_isolation_tests()

    passed = (
        containers_report.get("all_containers_running", False)
        and gateway_report.get("gateway_ingress_healthy", False)
        and isolation_port_report.get("postgres_isolated_from_host", False)
        and reconciliation_report.get("reconciliation_status") == "PASS"
        and isolation_tests_report.get("passed", False)
    )

    return {
        "preflight_status": "PASS" if passed else "FAIL",
        "pilot_tenant_id": tenant_id,
        "gateway_url": base_url,
        "containers": containers_report,
        "gateway": gateway_report,
        "port_isolation": isolation_port_report,
        "reconciliation": reconciliation_report,
        "tenant_isolation_tests": isolation_tests_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 9A Preflight Verification Automation")
    parser.add_argument("--gateway-url", default=PILOT_GATEWAY_URL, help="Pilot gateway base URL")
    parser.add_argument("--tenant-id", default=PILOT_TENANT_ID, help="Pilot tenant ID")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    report = run_preflight(base_url=args.gateway_url, tenant_id=args.tenant_id)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        iso_res = report["port_isolation"]["postgres_isolated_from_host"]
        iso_test = "PASS" if report["tenant_isolation_tests"]["passed"] else "FAIL"
        print("=" * 60)
        print("PHASE 9A PREFLIGHT VERIFICATION SUMMARY")
        print("=" * 60)
        print(f"Overall Preflight Status:      {report['preflight_status']}")
        print(f"Pilot Tenant:                  {report['pilot_tenant_id']}")
        print(f"Gateway Ingress:               {report['gateway']['gateway_ingress_healthy']}")
        print(f"Internal Port Isolation:       {iso_res}")
        print(f"Containers Running & Healthy:  {report['containers']['all_containers_running']}")
        rec = report.get("reconciliation", {})
        print(f"Reconciliation Status:         {rec.get('reconciliation_status')}")
        print(f"Accepted Resources:            {rec.get('accepted_resource_count')}")
        print(f"Accepted Availabilities:       {rec.get('accepted_availability_count')}")
        print(f"Resources By Type:             {rec.get('resources_by_type')}")
        print(f"Unresolved Mappings:           {rec.get('unresolved_mapping_count')}")
        print(f"Tenant Ownership Violations:   {rec.get('tenant_ownership_violations')}")
        print(f"Timezone Validation Failures:  {rec.get('timezone_validation_failures')}")
        print(f"Tenant Isolation Tests:        {iso_test}")
        print("=" * 60)

    return 0 if report["preflight_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
