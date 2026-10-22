"""Automated Production Release Packaging Workflow for E-7 Operations Engine.

Part of Stage 2 Production Completion.
Enforces:
1. Explicit version and commit identification.
2. Clean or explicitly acknowledged Git working-tree validation.
3. Reproducible container build preparation.
4. Container image digest recording.
5. CycloneDX/SPDX SBOM generation using approved tooling.
6. Trivy vulnerability scanning with honest dependency detection.
7. Configurable severity-based release gating.
8. Secret-scan execution (fails closed on unredacted secrets).
9. Machine-readable release manifest generation (release_manifest.json).
10. Human-readable release verification summary (RELEASE_SUMMARY.md).
11. Safe dry-run mode (--dry-run).
12. Clear failure reporting for missing dependencies or failed verification.

Governance Rules:
- Prohibits publishing images to registries or deploying containers.
- Prohibits creating git tags, pushing commits, or resetting history.
- Preserves the frozen Phase-8 release candidate.
- Never fabricates or fakes scan outcomes.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(
    os.environ.get("OPERATIONS_ENGINE_ROOT", Path(__file__).resolve().parents[2])
).resolve()

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FROZEN_PHASE_8_RC = "03e6305256db3d838b472aaac50893ddb513414e"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "Dockerfile"
PINNED_BASE_IMAGE = (
    "python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce"
)

SEVERITY_LEVELS = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


def get_git_info() -> dict[str, Any]:
    """Retrieve active git branch, HEAD commit hash, and working tree state."""
    branch = "unknown"
    commit = "unknown"
    is_dirty = False

    try:
        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        branch = res_branch.stdout.strip()

        res_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        commit = res_commit.stdout.strip()

        res_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        is_dirty = len(res_status.stdout.strip()) > 0
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return {
        "branch": branch,
        "commit": commit,
        "is_dirty": is_dirty,
    }


def parse_pyproject_version() -> str:
    """Extract application version from pyproject.toml."""
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return "1.0.0"
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            parts = line.split("=")
            if len(parts) >= 2:
                return parts[1].strip().strip('"').strip("'")
    return "1.0.0"


def validate_container_context() -> dict[str, Any]:
    """Verify presence of build definition and pinned base image."""
    if not DOCKERFILE_PATH.is_file():
        raise FileNotFoundError(f"Missing required Dockerfile at {DOCKERFILE_PATH}")

    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    if PINNED_BASE_IMAGE not in content:
        raise ValueError(
            f"Dockerfile does not reference expected pinned base image: {PINNED_BASE_IMAGE}"
        )

    required_files = [
        REPO_ROOT / "requirements.lock.txt",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "README.md",
    ]
    for rf in required_files:
        if not rf.is_file():
            raise FileNotFoundError(f"Missing container build requirement: {rf}")

    return {
        "dockerfile": str(DOCKERFILE_PATH.relative_to(REPO_ROOT)),
        "base_image": PINNED_BASE_IMAGE,
        "base_image_digest": PINNED_BASE_IMAGE.split("@")[-1],
        "base_digest": PINNED_BASE_IMAGE.split("@")[-1],
        "application_image_digest": (
            "NOT_BUILT (Packaging preparation does not invoke container engine)"
        ),
        "context_valid": True,
        "status": "PASS",
    }


def _safe_rel(path: Path) -> str:
    """Format path relative to repository root if possible, or return full path."""
    try:
        if path.is_relative_to(REPO_ROOT):
            return str(path.relative_to(REPO_ROOT))
    except Exception:
        pass
    return str(path)


def run_sbom_generation(output_dir: Path) -> dict[str, Any]:
    """Generate CycloneDX 1.5 JSON SBOM using existing approved generator."""
    from scripts.release.generate_sbom import generate_sbom

    target_path = output_dir / "SBOM.cyclonedx.json"
    sbom = generate_sbom(output_path=target_path)

    components = sbom.get("components", [])
    required_count = sum(1 for c in components if c.get("scope") == "required")
    optional_count = sum(1 for c in components if c.get("scope") == "optional")

    return {
        "sbom_file": _safe_rel(target_path),
        "format": "CycloneDX 1.5 JSON",
        "total_components": len(components),
        "runtime_required_components": required_count,
        "optional_dev_components": optional_count,
        "status": "PASS",
    }


def run_secret_scanning() -> dict[str, Any]:
    """Execute repository secret scanner and return structured results."""
    from scripts.release.scan_secrets import run_secret_scan

    scan_res = run_secret_scan()
    tree_findings = scan_res.get("tree_findings", [])
    history_findings = scan_res.get("history_findings", [])

    # Filter out known dummy test credentials in tests/ and historical docs
    critical_active = [
        f for f in tree_findings
        if f.get("severity") in ("CRITICAL", "HIGH")
        and not f.get("file", "").startswith("tests/")
    ]

    return {
        "status": "PASS" if len(critical_active) == 0 else "FAIL",
        "working_tree_findings_total": len(tree_findings),
        "active_critical_high_findings": len(critical_active),
        "git_history_findings_total": len(history_findings),
    }


def run_vulnerability_scan(
    max_severity: str = "HIGH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute Trivy vulnerability scanning if installed, or report dependency status."""
    trivy_bin = shutil.which("trivy")

    if not trivy_bin:
        # Honest reporting: trivy is not installed
        if dry_run:
            return {
                "status": "SKIPPED_MISSING_TOOL",
                "tool": "trivy",
                "installed": False,
                "message": (
                    "Trivy CLI is not installed on PATH. "
                    "Vulnerability scan skipped in dry-run; security posture unverified."
                ),
                "max_severity_gate": max_severity,
                "gated_passed": False,
            }
        return {
            "status": "BLOCKED",
            "tool": "trivy",
            "installed": False,
            "message": "Trivy binary required on PATH for production vulnerability release gating.",
            "max_severity_gate": max_severity,
            "gated_passed": False,
        }

    # If Trivy is installed, execute filesystem scan on repository
    try:
        cmd = [
            trivy_bin,
            "fs",
            "--format",
            "json",
            "--severity",
            ",".join(SEVERITY_LEVELS[SEVERITY_LEVELS.index(max_severity):]),
            str(REPO_ROOT),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        scan_data = json.loads(res.stdout)
        results = scan_data.get("Results", [])
        vuln_count = sum(len(r.get("Vulnerabilities", [])) for r in results)
        return {
            "status": "PASS" if vuln_count == 0 else "FAIL",
            "tool": "trivy",
            "installed": True,
            "findings_count": vuln_count,
            "max_severity_gate": max_severity,
            "gated_passed": vuln_count == 0,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "tool": "trivy",
            "installed": True,
            "error": str(exc),
            "max_severity_gate": max_severity,
            "gated_passed": False,
        }


def build_production_release(
    version: str | None = None,
    allow_uncommitted: bool = False,
    max_severity: str = "HIGH",
    dry_run: bool = True,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute the complete production release packaging workflow."""
    out_dir = output_dir or (REPO_ROOT / "dist" / "release")
    out_dir.mkdir(parents=True, exist_ok=True)

    git_info = get_git_info()
    app_version = version or parse_pyproject_version()
    timestamp = datetime.now(UTC).isoformat()

    # 1. Working tree validation
    if git_info["is_dirty"]:
        if not allow_uncommitted:
            raise RuntimeError(
                "Working tree has uncommitted modifications. "
                "Production release requires a clean working tree. "
                "To run diagnostic checks against uncommitted code, specify --allow-uncommitted."
            )
        working_tree_status = "SKIPPED_ACKNOWLEDGED_DIRTY"
    else:
        working_tree_status = "PASS"

    # 2. Container build context validation
    container_ctx = validate_container_context()

    # 3. SBOM generation
    sbom_info = run_sbom_generation(out_dir)

    # 4. Secret scan
    secret_info = run_secret_scanning()
    if secret_info["status"] != "PASS" and not dry_run:
        raise RuntimeError("Secret scanner detected active unredacted credentials!")

    # 5. Vulnerability scan
    vuln_info = run_vulnerability_scan(max_severity=max_severity, dry_run=dry_run)
    if not vuln_info.get("gated_passed", False) and not dry_run:
        raise RuntimeError(
            f"Vulnerability release gate failed: {vuln_info.get('message', 'Unresolved findings')}"
        )

    # 6. Overall verdict calculation
    checks = {
        "working_tree": working_tree_status,
        "container_context": container_ctx["status"],
        "sbom": sbom_info["status"],
        "secret_scan": secret_info["status"],
        "vulnerability_scan": vuln_info["status"],
    }

    reasons: list[str] = []
    if working_tree_status != "PASS":
        reasons.append(
            "Working tree has uncommitted changes (acknowledged for diagnostic packaging only)."
        )
    if secret_info["status"] != "PASS":
        reasons.append("Secret scan detected active unredacted credentials in the working tree.")
    if container_ctx["status"] != "PASS":
        reasons.append("Container build context validation failed.")
    if sbom_info["status"] != "PASS":
        reasons.append("CycloneDX SBOM generation failed.")
    if not vuln_info.get("gated_passed", False):
        scan_status = vuln_info.get("status")
        scan_msg = vuln_info.get("message", "Unresolved")
        reasons.append(f"Vulnerability scanning not passed ({scan_status}: {scan_msg}).")

    if not dry_run:
        if len(reasons) == 0:
            verdict = "RELEASE_READY"
        else:
            verdict = "RELEASE_GATED"
    else:
        # Dry-run execution
        reasons.append(
            "Dry-run execution mode: diagnostic verification only; does not authorize release."
        )
        diagnostic_passed = (
            container_ctx["status"] == "PASS"
            and sbom_info["status"] == "PASS"
            and secret_info["status"] == "PASS"
        )
        verdict = "DRY_RUN_PASSED" if diagnostic_passed else "RELEASE_GATED"

    authorized_for_release = (verdict == "RELEASE_READY")

    manifest: dict[str, Any] = {
        "manifest_version": "1.0.0",
        "release_version": app_version,
        "release_timestamp": timestamp,
        "dry_run": dry_run,
        "git": {
            "branch": git_info["branch"],
            "head_commit": git_info["commit"],
            "frozen_phase_8_rc": FROZEN_PHASE_8_RC,
            "working_tree_acknowledged": git_info["is_dirty"],
        },
        "container_build": container_ctx,
        "sbom": sbom_info,
        "secret_scan": secret_info,
        "vulnerability_scan": vuln_info,
        "release_gate": {
            "verdict": verdict,
            "authorized_for_release": authorized_for_release,
            "max_allowed_severity": max_severity,
            "checks": checks,
            "reasons": reasons,
        },
    }

    manifest_file = out_dir / "release_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    # Compute manifest SHA256 digest
    manifest_bytes = manifest_file.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    (out_dir / "release_manifest.sha256").write_text(
        f"{manifest_sha256}  release_manifest.json\n", encoding="utf-8"
    )

    # 7. Generate human-readable summary
    summary_md = f"""# E-7 Operations Engine — Production Release Packaging Summary

**Release Version:** `{app_version}`  
**Packaged At:** `{timestamp}`  
**Git Branch:** `{git_info['branch']}`  
**Git Commit HEAD:** `{git_info['commit']}`  
**Frozen Phase-8 RC Anchor:** `{FROZEN_PHASE_8_RC}`  
**Packaging Mode:** `{'DRY_RUN' if dry_run else 'PRODUCTION_BUILD'}`  
**Release Gate Verdict:** `{verdict}`  
**Authorized for Release:** `{'YES' if authorized_for_release else 'NO'}`  

---

## 1. Container Image Specification
- **Dockerfile:** `{container_ctx['dockerfile']}`
- **Pinned Base Image:** `{container_ctx['base_image']}`
- **Base Image Digest:** `{container_ctx['base_image_digest']}`
- **Application Image Digest:** `{container_ctx['application_image_digest']}`
- **Context Integrity:** `VERIFIED (Zero missing dependencies)`

## 2. Software Bill of Materials (SBOM)
- **Specification:** `CycloneDX 1.5 JSON`
- **Total Components:** `{sbom_info['total_components']}`
- **Production Runtime Required:** `{sbom_info['runtime_required_components']}`
- **Development / Optional:** `{sbom_info['optional_dev_components']}`
- **SBOM Artifact:** `{sbom_info['sbom_file']}`

## 3. Security Verification Gates
- **Secret Scanner:** `{secret_info['status']}`
  (Active critical findings: {secret_info['active_critical_high_findings']})
- **Vulnerability Scanner ({vuln_info.get('tool', 'trivy')}):** `{vuln_info.get('status')}`
- **Severity Gate Threshold:** `{max_severity}`
- **Scanner Message:** `{vuln_info.get('message', 'Scan completed successfully')}`

## 4. Gating Evaluation & Reasons
{chr(10).join(f"- {r}" for r in reasons)}

## 5. Governance Constraints
- No container images published to remote registries.
- No git commits, pushes, tags, or resets executed.
- Frozen Phase-8 release candidate preserved intact.
- Machine-readable manifest: `{_safe_rel(manifest_file)}` (SHA256: `{manifest_sha256}`)
"""
    (out_dir / "RELEASE_SUMMARY.md").write_text(summary_md, encoding="utf-8")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="E-7 Production Release Packaging and Verification Tool"
    )
    parser.add_argument("--version", type=str, help="Release version string")
    parser.add_argument(
        "--allow-uncommitted",
        action="store_true",
        help="Acknowledge dirty working tree for development packaging",
    )
    parser.add_argument(
        "--max-severity",
        choices=SEVERITY_LEVELS,
        default="HIGH",
        help="Maximum allowable vulnerability severity before gating",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Safe dry-run mode without failing on missing external scanners (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Execute real build gating (requires all tools present)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Custom output directory for release packaging artifacts",
    )

    args = parser.parse_args()
    dry_run = args.dry_run

    print("=================================================================")
    print("E-7 Operations Engine — Production Release Packaging")
    print("=================================================================")
    try:
        manifest = build_production_release(
            version=args.version,
            allow_uncommitted=args.allow_uncommitted,
            max_severity=args.max_severity,
            dry_run=dry_run,
            output_dir=args.output_dir,
        )
        verdict = manifest["release_gate"]["verdict"]
        print(f"Status: {verdict}")
        print(f"Authorized for Release: {manifest['release_gate']['authorized_for_release']}")
        print(f"Version: {manifest['release_version']}")
        print(f"Commit: {manifest['git']['head_commit']}")
        print(f"Dry Run: {manifest['dry_run']}")
        if manifest["release_gate"]["reasons"]:
            print("Gate Notes:")
            for r in manifest["release_gate"]["reasons"]:
                print(f"  - {r}")

        if not dry_run and verdict != "RELEASE_READY":
            print("RELEASE GATING FAILED: Release requirements not satisfied.", file=sys.stderr)
            sys.exit(1)
        elif dry_run and verdict != "DRY_RUN_PASSED":
            print("DIAGNOSTIC DRY RUN FAILED: Diagnostics encountered errors.", file=sys.stderr)
            sys.exit(1)

        print("Packaging completed successfully.")
    except Exception as exc:
        print(f"RELEASE PACKAGING FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
