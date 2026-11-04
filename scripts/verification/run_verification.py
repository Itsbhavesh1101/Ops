from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTEST_SUMMARY_PATTERN = re.compile(r"(?m)(\d+) passed(?:, (\d+) skipped)?")


def _resolve_command(command: list[Any], environment: dict[str, str]) -> list[str]:
    resolved = [sys.executable if str(part) == "{python}" else str(part) for part in command]
    if resolved:
        resolved[0] = shutil.which(resolved[0], path=environment.get("PATH")) or resolved[0]
    return resolved


def _redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _blocked_reason(gate: dict[str, Any], environment: dict[str, str]) -> str | None:
    missing_environment = [
        str(name)
        for name in gate.get("required_environment", [])
        if not environment.get(str(name), "").strip()
    ]
    if missing_environment:
        return f"Missing environment setting(s): {', '.join(missing_environment)}"

    missing = [
        executable
        for executable in gate.get("executables", [])
        if str(executable) != "{python}"
        and shutil.which(str(executable), path=environment.get("PATH")) is None
    ]
    if missing:
        return f"Missing executable(s): {', '.join(missing)}"

    for service in gate.get("services", []):
        name = str(service.get("name") or "unnamed-service")
        host = str(service["host"])
        port = int(service["port"])
        timeout = float(service.get("timeout_seconds", 0.25))
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError:
            return f"Unavailable service: {name} ({host}:{port})"
    return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_manifest(
    manifest_path: Path,
    *,
    groups: set[str] | None = None,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Execute the selected verification manifest gates and return report plus exit code."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = [
        gate
        for gate in manifest.get("gates", [])
        if groups is None or str(gate.get("group")) in groups
    ]
    results: list[dict[str, Any]] = []

    for gate in selected:
        started = time.monotonic()
        environment = os.environ.copy()
        environment.setdefault("E7_PYTHON_EXECUTABLE", sys.executable)
        environment.setdefault("PYTHONUTF8", "1")
        for name in gate.get("unset_environment", []):
            environment.pop(str(name), None)
        environment.update({str(k): str(v) for k, v in gate.get("environment", {}).items()})
        secret_values = [
            environment.get(str(name), "") for name in gate.get("redact_environment", [])
        ]
        blocked = _blocked_reason(gate, environment)
        base_result: dict[str, Any] = {
            "id": str(gate["id"]),
            "group": str(gate["group"]),
            "required": bool(gate.get("required", True)),
            "command": _resolve_command(gate["command"], environment),
        }

        if blocked is not None:
            results.append(
                {
                    **base_result,
                    "status": "BLOCKED_PREREQUISITE",
                    "reason": blocked,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "duration_seconds": round(time.monotonic() - started, 6),
                }
            )
            continue

        try:
            completed = subprocess.run(
                base_result["command"],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(gate.get("timeout_seconds", 900.0)),
                check=False,
            )
            results.append(
                {
                    **base_result,
                    "status": "PASS" if completed.returncode == 0 else "FAIL",
                    "reason": None,
                    "exit_code": completed.returncode,
                    "stdout": _redact(completed.stdout, secret_values),
                    "stderr": _redact(completed.stderr, secret_values),
                    "duration_seconds": round(time.monotonic() - started, 6),
                }
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            results.append(
                {
                    **base_result,
                    "status": "FAIL",
                    "reason": f"Timed out after {gate.get('timeout_seconds', 900.0)} seconds",
                    "exit_code": None,
                    "stdout": _redact(stdout, secret_values),
                    "stderr": _redact(stderr, secret_values),
                    "duration_seconds": round(time.monotonic() - started, 6),
                }
            )

    required_failures = [
        result for result in results if result["required"] and result["status"] == "FAIL"
    ]
    required_blocks = [
        result
        for result in results
        if result["required"] and result["status"] == "BLOCKED_PREREQUISITE"
    ]
    optional_blocks = [
        result
        for result in results
        if not result["required"] and result["status"] == "BLOCKED_PREREQUISITE"
    ]
    optional_failures = [
        result for result in results if not result["required"] and result["status"] == "FAIL"
    ]
    if required_failures:
        overall_status, exit_code = "FAIL", 1
    elif required_blocks:
        overall_status, exit_code = "BLOCKED_PREREQUISITE", 2
    elif optional_failures:
        overall_status, exit_code = "PASS_WITH_OPTIONAL_FAILURES", 0
    elif optional_blocks:
        overall_status, exit_code = "PASS_WITH_OPTIONAL_BLOCKS", 0
    else:
        overall_status, exit_code = "PASS", 0

    pytest_passed = 0
    pytest_skipped = 0
    for result in results:
        if "pytest" not in result["command"]:
            continue
        summaries = PYTEST_SUMMARY_PATTERN.findall(result["stdout"])
        if not summaries:
            continue
        passed, skipped = summaries[-1]
        pytest_passed += int(passed)
        pytest_skipped += int(skipped or 0)

    required_results = [result for result in results if result["required"]]
    metrics = {
        "pytest_passed": pytest_passed,
        "pytest_skipped": pytest_skipped,
        "required_gates_passed": sum(
            1 for result in required_results if result["status"] == "PASS"
        ),
        "required_gates_total": len(required_results),
        "optional_gates_blocked": len(optional_blocks),
        "optional_gates_failed": len(optional_failures),
    }

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "selected_groups": sorted(groups) if groups is not None else None,
        "overall_status": overall_status,
        "metrics": metrics,
        "gates": results,
    }
    evidence_groups = {
        str(group) for group in manifest.get("requirements_evidence_groups", [])
    }
    executed_groups = {str(result["group"]) for result in results}
    evidence_gates = [
        result
        for result in required_results
        if str(result["group"]) in evidence_groups
    ]
    requirements_evidenced = (
        bool(evidence_groups)
        and evidence_groups <= executed_groups
        and bool(evidence_gates)
        and all(result["status"] == "PASS" for result in evidence_gates)
    )
    if requirements_evidenced:
        report["requirements_verified"] = [
            str(requirement)
            for requirement in manifest.get("requirements_verified_on_pass", [])
        ]
        report["requirements_partially_verified"] = [
            str(requirement)
            for requirement in manifest.get(
                "requirements_partially_verified_on_pass", []
            )
        ]
    if output_path is not None:
        _write_json_atomic(output_path, report)
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the E-7 verification manifest.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("verification_manifest.json"),
    )
    parser.add_argument("--group", action="append", dest="groups")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report, exit_code = run_manifest(
        args.manifest,
        groups=set(args.groups) if args.groups else None,
        output_path=args.output,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
