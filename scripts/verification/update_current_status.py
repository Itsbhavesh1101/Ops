"""Generate the single living project-status document from machine evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT_PATTERN = re.compile(r"^### (FR-[A-Z]+-\d+):", re.MULTILINE)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _bool_label(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def build_current_status(
    *,
    requirement_matrix: Path,
    verification_report: Path,
    release_manifest: Path,
    sbom_path: Path,
    git_sha: str,
    generated_at: datetime,
) -> str:
    """Render deterministic status from the supplied evidence artifacts."""
    matrix_text = requirement_matrix.read_text(encoding="utf-8")
    requirement_ids = set(REQUIREMENT_PATTERN.findall(matrix_text))
    verification = _load_json(verification_report) or {}
    release = _load_json(release_manifest) or {}
    sbom = _load_json(sbom_path) or {}

    raw_verified_ids = verification.get("requirements_verified")
    verified_values = raw_verified_ids if isinstance(raw_verified_ids, list) else []
    verified_ids = {str(item) for item in verified_values} & requirement_ids
    raw_partial_ids = verification.get("requirements_partially_verified")
    partial_values = raw_partial_ids if isinstance(raw_partial_ids, list) else []
    partial_ids = {str(item) for item in partial_values} & requirement_ids
    raw_gates = verification.get("gates")
    gates: list[dict[str, Any]] = (
        [gate for gate in raw_gates if isinstance(gate, dict)]
        if isinstance(raw_gates, list)
        else []
    )
    external_gates = [
        gate
        for gate in gates
        if str(gate.get("id", "")).startswith("external-")
    ]
    external_passed = sum(1 for gate in external_gates if gate.get("status") == "PASS")
    open_external_gates = [gate for gate in external_gates if gate.get("status") != "PASS"]
    required_gates = [gate for gate in gates if gate.get("required")]
    required_passed = bool(required_gates) and all(
        gate.get("status") == "PASS" for gate in required_gates
    )
    raw_metrics = verification.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    raw_components = sbom.get("components")
    components: list[Any] = raw_components if isinstance(raw_components, list) else []
    raw_release_gate = release.get("release_gate")
    release_gate: dict[str, Any] = (
        raw_release_gate if isinstance(raw_release_gate, dict) else {}
    )
    raw_release_git = release.get("git")
    release_git: dict[str, Any] = raw_release_git if isinstance(raw_release_git, dict) else {}
    release_head_matches = release_git.get("head_commit") == git_sha
    release_dirty_diagnostic = (
        release_head_matches and release_git.get("working_tree_acknowledged") is True
    )
    release_provenance_current = release_head_matches and not release_dirty_diagnostic
    manifest_authorized = release_gate.get("authorized_for_release") is True
    raw_release_verdict = str(release_gate.get("verdict", "MISSING"))
    if release_provenance_current:
        release_verdict = raw_release_verdict
    elif release_dirty_diagnostic:
        release_verdict = f"UNCOMMITTED_DIAGNOSTIC ({raw_release_verdict})"
    else:
        release_verdict = f"STALE_EVIDENCE ({raw_release_verdict})"
    effective_authorized = (
        manifest_authorized
        and release_provenance_current
        and verification.get("overall_status") == "PASS"
        and required_passed
        and len(verified_ids) == len(requirement_ids)
        and bool(external_gates)
        and external_passed == len(external_gates)
    )
    if effective_authorized:
        repository_status = "PRODUCTION_READY"
    elif required_passed and external_gates and external_passed < len(external_gates):
        repository_status = "INTERNAL_ACCEPTANCE_COMPLETE_EXTERNAL_BLOCKED"
    elif required_passed:
        repository_status = "INTERNAL_ACCEPTANCE_COMPLETE_EXTERNAL_EVIDENCE_PENDING"
    else:
        repository_status = "INTERNAL_ACCEPTANCE_IN_PROGRESS"

    required_pass_count = sum(
        1 for gate in required_gates if gate.get("status") == "PASS"
    )
    if release_provenance_current:
        release_provenance = "CURRENT_GIT"
    elif release_dirty_diagnostic:
        release_provenance = "UNCOMMITTED_DIAGNOSTIC"
    else:
        release_provenance = "STALE_OR_MISSING"
    blocker_lines: list[str] = []
    if open_external_gates:
        blocker_lines = [
            "",
            "Open production blockers:",
            "",
            "| Gate | Status | Required operator input |",
            "|---|---|---|",
        ]
        for gate in open_external_gates:
            reason = str(gate.get("reason") or "External evidence has not passed.").replace(
                "|", "\\|"
            )
            blocker_lines.append(
                f"| {gate.get('id', 'external-unknown')} | "
                f"{gate.get('status', 'MISSING')} | {reason} |"
            )
    lines = [
        "# E-7 Operations Engine — Current Project Status",
        "",
        (
            "This is the single living status page. Phase reports and closure reports "
            "are historical evidence; they do not independently authorize production release."
        ),
        "",
        "<!-- BEGIN GENERATED CURRENT STATUS -->",
        "",
        (
            "Generated from repository evidence at "
            f"`{generated_at.astimezone(UTC).isoformat()}` for Git `{git_sha}`."
        ),
        "",
        "| Metric | Current evidence |",
        "|---|---:|",
        f"| Repository implementation status | {repository_status} |",
        f"| Canonical requirements | {len(requirement_ids)} |",
        f"| Verified requirements | {len(verified_ids)} / {len(requirement_ids)} |",
        (
            "| Partially verified requirements | "
            f"{len(partial_ids)} / {len(requirement_ids)} |"
        ),
        f"| Verification manifest status | {verification.get('overall_status', 'MISSING')} |",
        f"| Required verification gates | {required_pass_count} / {len(required_gates)} passed |",
        f"| External production gates | {external_passed} / {len(external_gates)} passed |",
        f"| Pytest passed | {metrics.get('pytest_passed', 'NOT_RECORDED')} |",
        f"| Pytest skipped | {metrics.get('pytest_skipped', 'NOT_RECORDED')} |",
        f"| CycloneDX components | {len(components) if components else 'MISSING'} |",
        f"| Release evidence provenance | {release_provenance} |",
        f"| Release manifest verdict | {release_verdict} |",
        f"| Release manifest authorization claim | {_bool_label(manifest_authorized)} |",
        f"| Effective production authorization | {_bool_label(effective_authorized)} |",
        *blocker_lines,
        "",
        (
            "Production authorization is effective only when all 25 canonical requirements, "
            "every required verification group, all four real external probes, mandatory "
            "security evidence, institutional recovery/UAT sign-off, merge state, and "
            "release-authority approval are simultaneously evidenced. Missing evidence "
            "fails closed."
        ),
        "",
        "Evidence inputs:",
        "",
        f"- Requirements: `{requirement_matrix.as_posix()}`",
        f"- Verification: `{verification_report.as_posix()}`",
        f"- Release manifest: `{release_manifest.as_posix()}`",
        f"- CycloneDX SBOM: `{sbom_path.as_posix()}`",
        "",
        "<!-- END GENERATED CURRENT STATUS -->",
        "",
    ]
    return "\n".join(lines)


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=REPO_ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.md",
    )
    parser.add_argument(
        "--verification-report",
        type=Path,
        default=REPO_ROOT / "dist" / "verification" / "full-verification.json",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=REPO_ROOT / "dist" / "release" / "release_manifest.json",
    )
    parser.add_argument(
        "--sbom",
        type=Path,
        default=REPO_ROOT / "dist" / "release" / "SBOM.cyclonedx.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / "CURRENT_PROJECT_STATUS.md",
    )
    parser.add_argument("--generated-at", type=datetime.fromisoformat)
    args = parser.parse_args(argv)
    rendered = build_current_status(
        requirement_matrix=args.requirements,
        verification_report=args.verification_report,
        release_manifest=args.release_manifest,
        sbom_path=args.sbom,
        git_sha=_git_sha(),
        generated_at=args.generated_at or datetime.now(UTC),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
