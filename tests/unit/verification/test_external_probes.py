from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts.verification.probe_common import run_bounded_probe, write_evidence_atomic
from scripts.verification.verify_e8_broker import BLOCKER_ID as E8_BLOCKER_ID
from scripts.verification.verify_e8_broker import evaluate_topology
from scripts.verification.verify_gateway_ingress import evaluate_gateway
from scripts.verification.verify_mysql_connection import BLOCKER_ID as MYSQL_BLOCKER_ID
from scripts.verification.verify_mysql_connection import evaluate_grants
from scripts.verification.verify_oidc_provider import evaluate_metadata


def test_mysql_probe_requires_complete_select_only_grants() -> None:
    assert MYSQL_BLOCKER_ID == "MYSQL_LIVE_READ_VERIFICATION"
    compliant = [
        "GRANT USAGE ON *.* TO `e7_ro_sync`@`%`",
        *[
            f"GRANT SELECT ON `iredu`.`{table}` TO `e7_ro_sync`@`%`"
            for table in (
                "sch_settings", "sessions", "classes", "sections", "class_sections",
                "subjects", "staff", "department", "staff_designation",
                "student_session", "subject_timetable",
            )
        ],
    ]
    assert evaluate_grants(compliant)["status"] == "PASS"

    unsafe = [*compliant, "GRANT INSERT ON `iredu`.`staff` TO `e7_ro_sync`@`%`"]
    result = evaluate_grants(unsafe)
    assert result["status"] == "FAIL"
    assert result["write_privileges_detected"] is True
    assert "e7_ro_sync" not in json.dumps(result)


def test_oidc_probe_validates_issuer_and_usable_jwks() -> None:
    issuer = "https://login.example.test"
    discovery = {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
    }
    jwks = {"keys": [{"kty": "RSA", "kid": "production-key", "use": "sig", "n": "n", "e": "AQAB"}]}

    assert evaluate_metadata(issuer, discovery, jwks)["status"] == "PASS"
    mismatched = evaluate_metadata(
        issuer, {**discovery, "issuer": "https://evil.test"}, jwks
    )
    assert mismatched["status"] == "FAIL"
    assert evaluate_metadata(issuer, discovery, {"keys": []})["status"] == "FAIL"


def test_e8_probe_requires_secure_durable_idempotent_topology() -> None:
    assert E8_BLOCKER_ID == "E8_NOTIFICATION_INTEGRATION"
    passing = evaluate_topology(
        scheme="amqps",
        exchange_type="topic",
        exchange_durable=True,
        publisher_confirms=True,
        mandatory_routing=True,
        idempotency_header="Idempotency-Key",
    )
    assert passing["status"] == "PASS"
    assert evaluate_topology(**{**passing["observed"], "scheme": "amqp"})["status"] == "FAIL"


def test_gateway_probe_requires_tls_headers_rate_limit_and_waf() -> None:
    result = evaluate_gateway(
        scheme="https",
        tls_version="TLSv1.3",
        headers={
            "strict-transport-security": "max-age=31536000",
            "content-security-policy": "default-src 'self'",
            "x-content-type-options": "nosniff",
        },
        burst_statuses=[200, 200, 429],
        waf_status=403,
    )
    assert result["status"] == "PASS"
    assert evaluate_gateway(**{**result["observed"], "tls_version": "TLSv1.2"})["status"] == "FAIL"


def test_bounded_probe_reports_timeout_without_leaking_secret() -> None:
    async def slow_probe() -> dict[str, object]:
        await asyncio.sleep(0.05)
        return {"secret": "should-never-appear"}

    evidence = asyncio.run(
        run_bounded_probe(
            "PRODUCTION_COMPANY_OIDC",
            slow_probe(),
            timeout_seconds=0.001,
            secret_values=["should-never-appear"],
        )
    )

    assert evidence["status"] == "FAIL"
    assert evidence["error_code"] == "TIMEOUT"
    assert "should-never-appear" not in json.dumps(evidence)


def test_bounded_probe_suppresses_credential_bearing_exception_text() -> None:
    async def failing_probe() -> dict[str, object]:
        raise RuntimeError("connection rejected for password=live-production-secret")

    evidence = asyncio.run(
        run_bounded_probe(
            "MYSQL_LIVE_READ_VERIFICATION",
            failing_probe(),
            timeout_seconds=1,
            secret_values=["live-production-secret"],
        )
    )

    serialized = json.dumps(evidence)
    assert evidence["error_type"] == "RuntimeError"
    assert "live-production-secret" not in serialized
    assert "connection rejected" not in serialized


def test_atomic_evidence_write_replaces_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "probe.json"
    output.write_text("stale", encoding="utf-8")
    payload = {"schema_version": "1.0", "status": "PASS"}

    write_evidence_atomic(output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert not (tmp_path / ".probe.json.tmp").exists()


def test_external_manifest_gates_write_individual_evidence_files() -> None:
    manifest_path = Path("scripts/verification/verification_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    external = [gate for gate in manifest["gates"] if gate["group"] == "external"]

    assert len(external) == 4
    assert all(gate["command"][1] == "-m" for gate in external)
    assert all("--output" in gate["command"] for gate in external)
    assert all(gate["required"] is False for gate in external)
    assert all(gate.get("required_environment") for gate in external)


def test_external_handover_commands_match_probe_cli_contracts() -> None:
    paths = [
        Path("docs/production/FINAL_EXTERNAL_DEPENDENCIES.md"),
        *Path("docs/production/handover").glob("*.md"),
    ]
    documentation = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for module in (
        "verify_mysql_connection",
        "verify_oidc_provider",
        "verify_e8_broker",
        "verify_gateway_ingress",
    ):
        assert f"python -m scripts.verification.{module} --output" in documentation
    for unsupported_flag in ("--host", "--client-id", "--dsn", "--target-url"):
        assert unsupported_flag not in documentation
