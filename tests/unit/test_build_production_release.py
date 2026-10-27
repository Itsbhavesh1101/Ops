"""Unit tests for the production release packaging workflow.

Part of Stage 2 Production Completion.
Verifies:
- Git metadata detection (branch, commit, dirty status)
- Container build context verification (pinned base image, required files)
- SBOM generation and metadata validation
- Secret scan execution and fail-closed behavior
- Vulnerability scanning with dry-run support
- Manifest and summary artifact generation with SHA256 integrity
- Uncommitted working-tree gating and override flags
"""

import json
from pathlib import Path

import pytest

from scripts.release.build_production_release import (
    FROZEN_PHASE_8_RC,
    PINNED_BASE_IMAGE,
    build_production_release,
    get_git_info,
    parse_pyproject_version,
    run_sbom_generation,
    run_secret_scanning,
    run_vulnerability_scan,
    validate_container_context,
)


def test_get_git_info() -> None:
    """Verify git metadata extraction."""
    info = get_git_info()
    assert isinstance(info, dict)
    assert info["branch"] != "unknown"
    assert len(info["commit"]) == 40
    assert isinstance(info["is_dirty"], bool)


def test_parse_pyproject_version() -> None:
    """Verify version extraction from pyproject.toml."""
    version = parse_pyproject_version()
    assert version.count(".") >= 2, f"Expected semantic version, got: {version}"


def test_validate_container_context() -> None:
    """Verify container build context and pinned base image."""
    ctx = validate_container_context()
    assert ctx["context_valid"] is True
    assert ctx["base_image"] == PINNED_BASE_IMAGE
    assert len(ctx["base_digest"]) > 0
    assert len(ctx["base_image_digest"]) > 0
    assert "NOT_BUILT" in ctx["application_image_digest"]


def test_run_sbom_generation(tmp_path: Path) -> None:
    """Verify CycloneDX SBOM generation into target directory."""
    sbom_info = run_sbom_generation(tmp_path)
    assert sbom_info["status"] == "PASS"
    assert sbom_info["total_components"] > 0
    assert sbom_info["runtime_required_components"] > 0

    sbom_file = tmp_path / "SBOM.cyclonedx.json"
    assert sbom_file.is_file()
    data = json.loads(sbom_file.read_text(encoding="utf-8"))
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.5"


def test_run_secret_scanning() -> None:
    """Verify secret scanning executes and reports structured statistics."""
    sec_info = run_secret_scanning()
    assert "status" in sec_info
    assert sec_info["status"] == "PASS"
    assert sec_info["active_critical_high_findings"] == 0


def test_run_vulnerability_scan_dry_run() -> None:
    """Verify vulnerability scanning in dry-run mode when tool is missing."""
    vuln_info = run_vulnerability_scan(max_severity="HIGH", dry_run=True)
    assert "status" in vuln_info
    if not vuln_info["installed"]:
        assert vuln_info["status"] == "SKIPPED_MISSING_TOOL"
        assert vuln_info["gated_passed"] is False


def test_build_production_release_full_dry_run(tmp_path: Path) -> None:
    """Verify full end-to-end packaging in dry-run mode produces DRY_RUN_PASSED."""
    manifest = build_production_release(
        version="1.0.0-test",
        allow_uncommitted=True,
        max_severity="HIGH",
        dry_run=True,
        output_dir=tmp_path,
    )

    assert manifest["release_version"] == "1.0.0-test"
    assert manifest["dry_run"] is True
    assert manifest["git"]["frozen_phase_8_rc"] == FROZEN_PHASE_8_RC
    assert manifest["release_gate"]["verdict"] == "DRY_RUN_PASSED"
    assert manifest["release_gate"]["authorized_for_release"] is False
    assert manifest["release_gate"]["checks"]["working_tree"] in (
        "PASS",
        "SKIPPED_ACKNOWLEDGED_DIRTY",
    )

    manifest_file = tmp_path / "release_manifest.json"
    assert manifest_file.is_file()

    sha_file = tmp_path / "release_manifest.sha256"
    assert sha_file.is_file()

    summary_file = tmp_path / "RELEASE_SUMMARY.md"
    assert summary_file.is_file()
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "1.0.0-test" in summary_text
    assert FROZEN_PHASE_8_RC in summary_text
    assert "Authorized for Release:** `NO`" in summary_text


def test_dry_run_never_authorizes_release(tmp_path: Path) -> None:
    """Verify dry-run mode never produces RELEASE_READY or authorized_for_release=True."""
    manifest = build_production_release(
        version="1.0.0-test",
        allow_uncommitted=True,
        dry_run=True,
        output_dir=tmp_path,
    )
    assert manifest["release_gate"]["authorized_for_release"] is False
    assert manifest["release_gate"]["verdict"] != "RELEASE_READY"


def test_dirty_tree_with_allow_uncommitted_fails_production_gating(tmp_path: Path) -> None:
    """Verify dirty tree with --allow-uncommitted still produces failure under --no-dry-run."""
    info = get_git_info()
    if not info["is_dirty"]:
        pytest.skip("Working tree is clean; skipping dirty tree test.")

    # When no-dry-run is requested on a dirty tree with missing trivy, it must fail closed
    with pytest.raises(RuntimeError):
        build_production_release(
            version="1.0.0-test",
            allow_uncommitted=True,
            dry_run=False,
            output_dir=tmp_path,
        )


def test_working_tree_gating_fails_when_dirty_without_flag(tmp_path: Path) -> None:
    """Verify fail-closed behavior when working tree is dirty without --allow-uncommitted."""
    info = get_git_info()
    if not info["is_dirty"]:
        pytest.skip("Working tree is clean; skipping dirty tree gating test.")

    with pytest.raises(RuntimeError, match="uncommitted modifications"):
        build_production_release(
            version="1.0.0-test",
            allow_uncommitted=False,
            dry_run=True,
            output_dir=tmp_path,
        )
