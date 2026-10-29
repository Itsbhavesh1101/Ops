from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from scripts.verification.run_verification import run_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_manifest(path: Path, gates: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": "1.0", "gates": gates}), encoding="utf-8")


def test_runner_records_pass_and_redacts_declared_secret(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "report.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "redaction",
                "group": "unit",
                "required": True,
                "command": [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['VERIFY_TEST_SECRET'])",
                ],
                "environment": {"VERIFY_TEST_SECRET": "top-secret-value"},
                "redact_environment": ["VERIFY_TEST_SECRET"],
            }
        ],
    )

    report, exit_code = run_manifest(manifest, output_path=output)

    assert exit_code == 0
    assert report["overall_status"] == "PASS"
    assert report["gates"][0]["status"] == "PASS"
    serialized = output.read_text(encoding="utf-8")
    assert "top-secret-value" not in serialized
    assert "[REDACTED]" in serialized


def test_runner_resolves_python_token_to_active_interpreter(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "active-python",
                "group": "unit",
                "required": True,
                "command": ["{python}", "-c", "print('active interpreter')"],
            }
        ],
    )

    report, exit_code = run_manifest(manifest)

    assert exit_code == 0
    assert report["gates"][0]["command"][0] == sys.executable
    assert "active interpreter" in report["gates"][0]["stdout"]


def test_runner_fails_when_required_executable_is_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "mandatory-tool",
                "group": "security",
                "required": True,
                "command": ["definitely-not-an-e7-command"],
                "executables": ["definitely-not-an-e7-command"],
            }
        ],
    )

    report, exit_code = run_manifest(manifest)

    assert exit_code == 2
    assert report["overall_status"] == "BLOCKED_PREREQUISITE"
    assert report["gates"][0]["status"] == "BLOCKED_PREREQUISITE"


def test_runner_keeps_optional_block_visible_without_failing(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "optional-tool",
                "group": "external",
                "required": False,
                "command": ["definitely-not-an-e7-command"],
                "executables": ["definitely-not-an-e7-command"],
            }
        ],
    )

    report, exit_code = run_manifest(manifest)

    assert exit_code == 0
    assert report["overall_status"] == "PASS_WITH_OPTIONAL_BLOCKS"
    assert report["gates"][0]["status"] == "BLOCKED_PREREQUISITE"


def test_runner_blocks_required_gate_when_tcp_service_is_unavailable(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]

    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "required-service",
                "group": "integration",
                "required": True,
                "command": [sys.executable, "-c", "print('must not execute')"],
                "services": [
                    {"name": "unavailable-test-service", "host": "127.0.0.1", "port": unused_port}
                ],
            }
        ],
    )

    report, exit_code = run_manifest(manifest)

    assert exit_code == 2
    assert report["gates"][0]["status"] == "BLOCKED_PREREQUISITE"
    assert "unavailable-test-service" in report["gates"][0]["reason"]


def test_runner_preserves_failed_command_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "failure",
                "group": "unit",
                "required": True,
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; print('controlled failure'); sys.exit(7)",
                ],
            }
        ],
    )

    report, exit_code = run_manifest(manifest)

    assert exit_code == 1
    assert report["overall_status"] == "FAIL"
    assert report["gates"][0]["status"] == "FAIL"
    assert report["gates"][0]["exit_code"] == 7
    assert "controlled failure" in report["gates"][0]["stdout"]


def test_runner_filters_groups_without_hiding_requested_gate_results(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "id": "selected",
                "group": "selected-group",
                "required": True,
                "command": [sys.executable, "-c", "print('selected')"],
            },
            {
                "id": "not-selected",
                "group": "other-group",
                "required": True,
                "command": [sys.executable, "-c", "raise SystemExit(9)"],
            },
        ],
    )

    report, exit_code = run_manifest(manifest, groups={"selected-group"})

    assert exit_code == 0
    assert report["overall_status"] == "PASS"
    assert [gate["id"] for gate in report["gates"]] == ["selected"]


def test_repository_manifest_defines_every_completion_gate_group() -> None:
    manifest_path = REPO_ROOT / "scripts" / "verification" / "verification_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    groups = {str(gate["group"]) for gate in manifest["gates"]}
    assert groups == {
        "backend",
        "browser",
        "external",
        "frontend",
        "recovery",
        "release",
        "security",
        "static",
        "uat",
    }
    assert all(isinstance(gate["command"], list) and gate["command"] for gate in manifest["gates"])
