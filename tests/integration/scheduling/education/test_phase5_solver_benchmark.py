"""Phase 5 Comprehensive Education Scheduling Solver Benchmark Integration Tests.

Repeatable, reproducible integration benchmark suite comparing:
1. OR-Tools Native Academic Profile
2. UniTime CPSolver Adapter (org.cpsolver 1.4.94)

Gated by environment variable:
E7_RUN_EDUCATION_SOLVER_BENCHMARK=1

When this flag is not set, this module is skipped during standard test runs.
When this flag IS set, all prerequisites (Java, CPSolver classpath, solvers)
are strictly required and will FAIL the test if missing or broken.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from app.scheduling.education.benchmark import (
    FET_BENCHMARK_STATUS,
    EducationBenchmarkHarness,
    save_benchmark_evidence,
)
from app.scheduling.education.dataset import (
    build_benchmark_manifest,
    load_education_dataset,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
EVIDENCE_FILE = REPO_ROOT / "docs" / "phase-5" / "PHASE_5_BENCHMARK_EVIDENCE.json"

# ============================================================================
# Opt-in Runtime Gate Enforcement
# ============================================================================

OPT_IN_FLAG = os.getenv("E7_RUN_EDUCATION_SOLVER_BENCHMARK") == "1"

if not OPT_IN_FLAG:
    pytest.skip(
        "Phase-5 education solver benchmark skipped. "
        "Set E7_RUN_EDUCATION_SOLVER_BENCHMARK=1 to run external JVM benchmark.",
        allow_module_level=True,
    )


@pytest.fixture(scope="module", autouse=True)
def verify_benchmark_infrastructure() -> None:
    """Strictly verify runtime requirements when benchmark flag is enabled.

    Fails explicitly if Java runtime or CPSolver classpath is missing.
    """
    java_bin = shutil.which("java")
    if not java_bin:
        pytest.fail(
            "E7_RUN_EDUCATION_SOLVER_BENCHMARK=1 is set, but 'java' executable "
            "was not found on PATH. An active Java 8+ runtime is strictly required."
        )

    cache_dir = REPO_ROOT / ".cache" / "unitime"
    jars = list(cache_dir.glob("*.jar"))
    if not jars:
        pytest.fail(
            f"E7_RUN_EDUCATION_SOLVER_BENCHMARK=1 is set, but no CPSolver JARs "
            f"were found in {cache_dir}. The UniTime CPSolver runtime is strictly required."
        )


# ============================================================================
# Benchmark Test Cases
# ============================================================================


def test_same_dataset_manifest_and_hash_proof() -> None:
    """Prove that OR-Tools and UniTime receive the exact same dataset without mutation."""
    manifest = build_benchmark_manifest()
    dataset_feasible, resources = load_education_dataset("FEASIBLE")

    # Cryptographic proof of dataset integrity
    feasible_hash = manifest.dataset_hashes["PHASE5-SC-EXAM-FEASIBLE-001"]
    assert feasible_hash == "96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a"
    assert (
        manifest.combined_canonical_benchmark_hash
        == "e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b"
    )

    # Equipment verification in canonical dataset
    assert manifest.entity_counts["equipment"] == 1
    eq_res = [r for r in resources if r.id == "res-eq-004"]
    assert len(eq_res) == 1
    assert "LAB_KIT" in eq_res[0].skills

    # Sessions requiring equipment
    sessions_with_eq = [s for s in dataset_feasible.sessions if s.required_equipment]
    assert len(sessions_with_eq) == 2
    assert {s.id for s in sessions_with_eq} == {"session-science-g10a", "session-science-g10b"}


def test_benchmark_feasible_ortools_and_unitime_median_of_three() -> None:
    """Execute 3-trial benchmark of both solvers against the same canonical dataset."""
    harness = EducationBenchmarkHarness()
    results = harness.run_baseline_benchmark(trials=3)

    ortools_runs = results["ortools_runs"]
    unitime_runs = results["unitime_runs"]

    assert len(ortools_runs) == 3
    assert len(unitime_runs) == 3

    # OR-Tools trial validation
    for run in ortools_runs:
        assert run["status"] == "FEASIBLE"
        assert run["hard_violations"] == 0
        assert run["is_accepted"] is True
        assert run["soft_metrics"]["equipment_satisfaction_rate"] == 1.0
        assert run["soft_metrics"]["preferred_room_satisfaction_rate"] >= 0.8

    # UniTime trial validation
    for run in unitime_runs:
        assert run["status"] == "FEASIBLE"
        assert run["hard_violations"] == 0
        assert run["is_accepted"] is True
        assert run["soft_metrics"]["equipment_satisfaction_rate"] == 1.0
        assert run["soft_metrics"]["preferred_room_satisfaction_rate"] >= 0.8

    # Solve latency comparison
    med_ot = results["median_solve_duration"]["ortools"]
    med_ut = results["median_solve_duration"]["unitime"]
    assert med_ot > 0.0
    assert med_ut > 0.0
    assert med_ot < med_ut, f"OR-Tools ({med_ot}s) expected faster than UniTime ({med_ut}s)"


def test_benchmark_infeasible_scenario_truthful_diagnosis() -> None:
    """Verify both solvers handle impossible constraints with truthful attribution."""
    harness = EducationBenchmarkHarness()
    res = harness.run_infeasible_benchmark()

    # OR-Tools native CP-SAT detection via mathematical assumption conflict
    assert res["ortools"]["status"] == "INFEASIBLE"
    assert res["ortools"]["detected_infeasible"] is True
    assert len(res["ortools"]["diagnostics"]) > 0

    # UniTime detection via E-7 adapter prevalidation stage
    assert res["unitime"]["status"] == "INFEASIBLE"
    assert res["unitime"]["detected_infeasible"] is True
    assert "E7_ADAPTER_PREVALIDATION" in res["unitime"]["diagnostics"][0]


def test_benchmark_six_repair_edge_cases() -> None:
    """Verify all 6 repair edge cases pass with zero hard violations and zero locked movements."""
    harness = EducationBenchmarkHarness()
    repair_results = harness.run_repair_edge_cases()

    assert len(repair_results) == 6

    for r in repair_results:
        # Zero hard violations on both solvers
        assert r.ortools_hard_violations == 0, f"OR-Tools hard violations in {r.scenario_name}"
        assert r.unitime_hard_violations == 0, f"UniTime hard violations in {r.scenario_name}"

        # Strict lock invariance: zero locked assignments moved
        assert r.ortools_locked_moved == 0, f"OR-Tools moved locked session in {r.scenario_name}"
        assert r.unitime_locked_moved == 0, f"UniTime moved locked session in {r.scenario_name}"

        # Invariant confirmed
        assert r.lock_invariant_satisfied is True


def test_persist_machine_readable_benchmark_evidence() -> None:
    """Generate and persist the authoritative machine-readable benchmark evidence artifact."""
    evidence_path = save_benchmark_evidence()
    assert evidence_path.exists()
    assert evidence_path == EVIDENCE_FILE

    with open(evidence_path, encoding="utf-8") as f:
        data = json.load(f)

    # Validate structure
    assert data["benchmark_id"] == "PHASE_5_ACCELERATED_EDUCATION_BENCHMARK"
    assert data["benchmark_version"] == "2.0.0"
    assert "environment" in data
    assert "same_dataset_proof" in data
    assert data["same_dataset_proof"]["hashes_identical"] is True
    assert "manifest" in data
    assert "baseline" in data
    assert "infeasible" in data
    assert "repair_edge_cases" in data
    assert len(data["repair_edge_cases"]) == 6
    assert "comparison_dimensions" in data

    # Validate 8 required dimensions
    dims = data["comparison_dimensions"]
    assert "1_feasibility" in dims
    assert "2_solve_duration" in dims
    assert "3_soft_violations" in dims
    assert "4_schedule_quality" in dims
    assert "5_incremental_change_stability" in dims
    assert "6_adapter_complexity" in dims
    assert "7_deployment_operations_cost" in dims
    assert "8_explainability" in dims

    # Verify FET legal gate status
    assert data["environment"]["fet_legal_gate"] == "NOT_APPROVED"
    assert data["environment"]["fet_benchmark_status"] == FET_BENCHMARK_STATUS
