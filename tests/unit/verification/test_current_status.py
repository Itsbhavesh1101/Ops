from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.verification.update_current_status import build_current_status

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    matrix = tmp_path / "requirements.md"
    matrix.write_text(
        "# Matrix\n\n### FR-TI-01: Goal intake\n\n### FR-INT-01: Notifications\n",
        encoding="utf-8",
    )
    verification = tmp_path / "verification.json"
    verification.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "overall_status": "PASS_WITH_OPTIONAL_FAILURES",
                "generated_at": "2026-10-30T00:00:00+00:00",
                "metrics": {"pytest_passed": 123, "pytest_skipped": 7},
                "requirements_verified": ["FR-TI-01"],
                "requirements_partially_verified": ["FR-INT-01"],
                "gates": [
                    {"id": "backend-core", "required": True, "status": "PASS"},
                    {
                        "id": "external-e8",
                        "required": False,
                        "status": "BLOCKED_PREREQUISITE",
                        "reason": "Missing environment setting(s): E7_E8_AMQP_URL",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "git": {"head_commit": "old-sha"},
                "release_gate": {
                    "verdict": "RELEASE_READY",
                    "authorized_for_release": True,
                }
            }
        ),
        encoding="utf-8",
    )
    sbom = tmp_path / "sbom.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [{"name": "one"}, {"name": "two"}, {"name": "three"}],
            }
        ),
        encoding="utf-8",
    )
    return matrix, verification, release, sbom


def test_status_uses_current_evidence_metrics_not_stale_document_totals(tmp_path: Path) -> None:
    matrix, verification, release, sbom = _write_inputs(tmp_path)

    status = build_current_status(
        requirement_matrix=matrix,
        verification_report=verification,
        release_manifest=release,
        sbom_path=sbom,
        git_sha="abc123",
        generated_at=datetime(2026, 10, 30, tzinfo=UTC),
    )

    assert "Canonical requirements | 2" in status
    assert "Verified requirements | 1 / 2" in status
    assert "Partially verified requirements | 1 / 2" in status
    assert "Pytest passed | 123" in status
    assert "Pytest skipped | 7" in status
    assert "CycloneDX components | 3" in status
    assert "2,393" not in status
    assert "2,407" not in status


def test_status_refuses_false_release_authorization_when_external_gates_fail(
    tmp_path: Path,
) -> None:
    matrix, verification, release, sbom = _write_inputs(tmp_path)

    status = build_current_status(
        requirement_matrix=matrix,
        verification_report=verification,
        release_manifest=release,
        sbom_path=sbom,
        git_sha="abc123",
        generated_at=datetime(2026, 10, 30, tzinfo=UTC),
    )

    assert "Release manifest authorization claim | TRUE" in status
    assert "Release evidence provenance | STALE_OR_MISSING" in status
    assert "Release manifest verdict | STALE_EVIDENCE (RELEASE_READY)" in status
    assert "Effective production authorization | FALSE" in status
    assert "External production gates | 0 / 1 passed" in status
    assert "external-e8 | BLOCKED_PREREQUISITE" in status
    assert "Missing environment setting(s): E7_E8_AMQP_URL" in status
    assert (
        "Repository implementation status | INTERNAL_ACCEPTANCE_COMPLETE_EXTERNAL_BLOCKED"
        in status
    )


def test_status_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    matrix, verification, release, sbom = _write_inputs(tmp_path)
    arguments = {
        "requirement_matrix": matrix,
        "verification_report": verification,
        "release_manifest": release,
        "sbom_path": sbom,
        "git_sha": "abc123",
        "generated_at": datetime(2026, 10, 30, tzinfo=UTC),
    }

    assert build_current_status(**arguments) == build_current_status(**arguments)


def test_status_marks_matching_dirty_release_as_diagnostic_only(tmp_path: Path) -> None:
    matrix, verification, release, sbom = _write_inputs(tmp_path)
    release.write_text(
        json.dumps(
            {
                "git": {
                    "head_commit": "abc123",
                    "working_tree_acknowledged": True,
                },
                "release_gate": {
                    "verdict": "DRY_RUN_PASSED",
                    "authorized_for_release": False,
                },
            }
        ),
        encoding="utf-8",
    )

    status = build_current_status(
        requirement_matrix=matrix,
        verification_report=verification,
        release_manifest=release,
        sbom_path=sbom,
        git_sha="abc123",
        generated_at=datetime(2026, 10, 30, tzinfo=UTC),
    )

    assert "Release evidence provenance | UNCOMMITTED_DIAGNOSTIC" in status
    assert "Release manifest verdict | UNCOMMITTED_DIAGNOSTIC (DRY_RUN_PASSED)" in status
    assert "Effective production authorization | FALSE" in status


def test_living_documents_point_to_current_status_without_stale_phase_zero() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROJECT_COMPLETE_PROGRESS_REPORT.md").read_text(
        encoding="utf-8"
    )
    dq6 = (
        REPO_ROOT
        / "docs"
        / "development-quality"
        / "DQ6_Full_Verification_Closure_Report.md"
    ).read_text(encoding="utf-8")
    index = (REPO_ROOT / "docs" / "production" / "DOCUMENTATION_INDEX.md").read_text(
        encoding="utf-8"
    )

    current_link = "docs/CURRENT_PROJECT_STATUS.md"
    assert current_link in readme
    assert current_link in progress
    assert current_link in dq6
    assert "../CURRENT_PROJECT_STATUS.md" in index
    assert "Current Status: Phase 0" not in readme
    assert "DQ6_REAL_APPLICATION_ACCEPTANCE_COMPLETE" not in dq6


def test_release_and_signoff_documents_are_bounded_as_historical_evidence() -> None:
    paths = (
        REPO_ROOT / "docs" / "phase-8" / "uat" / "UAT_SIGNOFF.md",
        REPO_ROOT / "docs" / "phase-9" / "PILOT_ACCEPTANCE.md",
        REPO_ROOT / "docs" / "phase-9" / "ROLLOUT_DECISION.md",
        REPO_ROOT / "docs" / "production" / "FINAL_RELEASE_GATE_MATRIX.md",
        REPO_ROOT / "docs" / "production" / "FINAL_PRODUCTION_READINESS_REPORT.md",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "CURRENT_PROJECT_STATUS.md" in text, path
        assert "HISTORICAL" in text, path
        assert "PRODUCTION_RELEASE_AUTHORIZED = FALSE" in text, path
