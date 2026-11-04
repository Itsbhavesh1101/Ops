from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from scripts.release.build_production_release import (
    TRIVY_SKIP_DIRS,
    inspect_sbom_artifact,
    validate_trivy_report,
)
from scripts.verification.run_trivy_scan import build_command

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_trivy_report(path: Path, vulnerabilities: list[dict[str, str]]) -> None:
    path.write_text(
        json.dumps(
            {
                "SchemaVersion": 2,
                "ArtifactName": "operations-engine",
                "Results": [
                    {
                        "Target": "requirements.lock.txt",
                        "Class": "lang-pkgs",
                        "Type": "pip",
                        "Vulnerabilities": vulnerabilities,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_missing_trivy_report_fails_closed(tmp_path: Path) -> None:
    result = validate_trivy_report(tmp_path / "missing.json")

    assert result["status"] == "BLOCKED"
    assert result["gated_passed"] is False
    assert result["error_code"] == "MISSING_REPORT"


def test_stale_trivy_report_fails_provenance_check(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    _write_trivy_report(report, [])
    now = datetime(2026, 10, 30, tzinfo=UTC)
    stale_time = (now - timedelta(hours=25)).timestamp()
    report.touch()
    import os

    os.utime(report, (stale_time, stale_time))

    result = validate_trivy_report(report, now=now, max_age=timedelta(hours=24))

    assert result["status"] == "FAIL"
    assert result["error_code"] == "STALE_REPORT"
    assert result["gated_passed"] is False


def test_malformed_trivy_report_fails_closed(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    report.write_text("{not-json", encoding="utf-8")

    result = validate_trivy_report(report)

    assert result["status"] == "FAIL"
    assert result["error_code"] == "MALFORMED_REPORT"


def test_wrong_shape_trivy_report_fails_closed(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    report.write_text("[]", encoding="utf-8")

    result = validate_trivy_report(report)

    assert result["status"] == "FAIL"
    assert result["error_code"] == "MALFORMED_REPORT"


def test_invalid_vulnerability_collection_fails_closed(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    report.write_text(
        json.dumps(
            {
                "SchemaVersion": 2,
                "Results": [
                    {"Target": "requirements.lock.txt", "Vulnerabilities": "none"}
                ],
            }
        ),
        encoding="utf-8",
    )

    result = validate_trivy_report(report)

    assert result["status"] == "FAIL"
    assert result["error_code"] == "MALFORMED_REPORT"


def test_unresolved_high_or_critical_findings_fail_gate(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    _write_trivy_report(
        report,
        [
            {"VulnerabilityID": "CVE-HIGH", "Severity": "HIGH"},
            {"VulnerabilityID": "CVE-CRITICAL", "Severity": "CRITICAL"},
        ],
    )

    result = validate_trivy_report(report)

    assert result["status"] == "FAIL"
    assert result["gated_passed"] is False
    assert result["unresolved_high"] == 1
    assert result["unresolved_critical"] == 1


def test_clean_trivy_report_records_digest_and_passes(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    _write_trivy_report(report, [])

    result = validate_trivy_report(report)

    assert result["status"] == "PASS"
    assert result["gated_passed"] is True
    assert result["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_sbom_counts_are_read_from_cyclonedx_artifact(tmp_path: Path) -> None:
    sbom = tmp_path / "SBOM.cyclonedx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {"name": "runtime", "scope": "required"},
                    {"name": "dev", "scope": "optional"},
                    {"name": "runtime-two", "scope": "required"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = inspect_sbom_artifact(sbom)

    assert result["status"] == "PASS"
    assert result["total_components"] == 3
    assert result["runtime_required_components"] == 2
    assert result["optional_dev_components"] == 1
    assert result["artifact_sha256"] == hashlib.sha256(sbom.read_bytes()).hexdigest()


def test_full_verification_installs_pinned_trivy_and_runs_security_groups() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "full-verification.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["linux-full-verification"]["steps"]
    install = next(step for step in steps if step.get("name") == "Install pinned Trivy")
    run_step = next(
        step for step in steps if step.get("name") == "Run complete verification manifest"
    )

    assert install["uses"] == "aquasecurity/setup-trivy@e07451d2e059ed86c2870430ea286b3a9e0bf241"
    assert install["with"]["version"] == "v0.76.1"
    assert "--group security" in run_step["run"]
    assert "--group release" in run_step["run"]


def test_manifest_trivy_scan_excludes_generated_dependency_trees() -> None:
    manifest = json.loads(
        (REPO_ROOT / "scripts" / "verification" / "verification_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_command = next(
        gate["command"] for gate in manifest["gates"] if gate["id"] == "trivy-filesystem"
    )
    assert manifest_command[1] == "scripts/verification/run_trivy_scan.py"

    command = build_command("trivy", Path("scan.json"))
    assert command[command.index("--scanners") + 1] == "vuln"

    skip_dirs = [
        command[index + 1]
        for index, token in enumerate(command[:-1])
        if token == "--skip-dirs"
    ]
    expected_skips = {
        ".git",
        ".cache",
        ".venv",
        "ui/operations-ui/node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "scratch",
        "screenshots",
        "ui/operations-ui/test-results",
        "ui/operations-ui/playwright-report",
    }
    assert expected_skips <= set(skip_dirs)
    assert expected_skips <= set(TRIVY_SKIP_DIRS)
