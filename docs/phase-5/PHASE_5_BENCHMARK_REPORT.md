# Phase 5 Education Scheduling Benchmark Report

## 1. Executive Summary
This report presents the empirical evaluation between **Candidate A (UniTime CPSolver Adapter)** and **Candidate B (OR-Tools-Native Academic Profile)** for institutional educational scheduling within the IR Infotech E-7 Operations Engine.

Both candidates were benchmarked against the identical canonical dataset with full equipment coverage, hard constraints, and disruption scenarios under strict, independent evaluation by `EducationScheduleEvaluator`.

### Key Verdict
* **Winner**: **OR-Tools-Native Academic Profile**
* **Primary Drivers**:
  1. **Lower Total Wall Clock Duration**: 0.5311s vs 0.7919s (median of 3 trials, 1.49x total wall-clock advantage; 0.5299s vs 0.7695s engine duration, 1.45x engine advantage).
  2. **21.4x Faster Adapter Translation**: 0.0010s vs 0.0214s.
  3. **Zero Subprocess Overhead**: Native in-process C++ wheel execution without managing JVM child process lifecycles.
  4. **Equal Feasibility & Constraint Integrity**: 0 hard violations on feasible, infeasible, and all 6 repair edge cases with 100% equipment satisfaction.
  5. **Superior Explainability**: Deterministic assumption-literal conflict extraction for operator diagnostics.
  6. **Zero Licensing Risk**: Apache-2.0 native C++ extension; FET 7.10.5 AGPL-3.0 omitted under legal gate `NOT_RUN_LEGAL_GATE_NOT_APPROVED`.

---

## 2. Benchmark Environment & Methodology
- **Benchmark Run ID**: `PHASE_5_ACCELERATED_EDUCATION_BENCHMARK`
- **Benchmark Version**: `2.0.0`
- **Execution Timestamp**: `2026-10-06T04:13:22`
- **Operating System**: Windows (amd64)
- **Runtimes**:
  - Python: `3.12.13`
  - Java JRE: `1.8.0_491`
  - OR-Tools: `9.15`
  - UniTime CPSolver: `1.4.94` (Release `v4.9.152`)
- **Resource Constraints**: Single-thread worker budget (`Parallel.NrSolvers=1`, `num_search_workers=1`), seed = 42, 10-second timeout.
- **Independent Evaluation**: Output evaluated strictly via `EducationScheduleEvaluator` for 10 hard constraints.

### Cryptographic Manifest Digests (Dedicated Phase-5 Benchmark Dataset)
- `PHASE5-RS-EDU-001@2.0.0`: `4a1ca0d0fcccbd53c336d15b1efea75340bc745bbfa390fb217e07f2f57ef02a`
- `PHASE5-SC-EXAM-FEASIBLE-001@2.0.0`: `96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a`
- `PHASE5-SC-EXAM-INFEASIBLE-001@2.0.0`: `ed40a105179764f6794c3a2e1a7543c34fee85a12439a756a6661a2c7de79f67`
- `PHASE5-SC-EXAM-REPAIR-001@2.0.0`: `46cb07ef7e6f089dfa32850a5df23cab1c355509c05f028d74e8536259e0e8a4`
- `Combined Canonical Benchmark Hash`: `e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b`

*Same-Dataset Proof*: Both engines received the exact same dataset snapshot hash (`96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a`) and combined canonical benchmark hash (`e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b`) with zero engine-specific mutation prior to benchmarking. The frozen Phase-0 baseline fixtures remain unmodified and separate.

---

## 3. Empirical Results Across 8 Required Dimensions

### 3.1 Dimension 1: Feasibility
| Solver | Feasibility Status | Hard Violations | Evaluator Valid | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **OR-Tools-Native** | `FEASIBLE` | 0 | True | 100% compliant with all 10 hard constraints |
| **UniTime-CPSolver** | `FEASIBLE` | 0 | True | 100% compliant with all 10 hard constraints |

### 3.2 Dimension 2: Solve Duration (Median of 3 Trials)
| Metric | OR-Tools-Native | UniTime CPSolver | Comparison |
| :--- | :---: | :---: | :---: |
| **Trial 1 Solve Duration** | 0.4557 s | 0.7915 s | OR-Tools 1.74x faster |
| **Trial 2 Solve Duration** | 0.5299 s | 0.7269 s | OR-Tools 1.37x faster |
| **Trial 3 Solve Duration** | 0.5305 s | 0.7695 s | OR-Tools 1.45x faster |
| **Median Engine Solve Duration** | **0.5299 s** | **0.7695 s** | **OR-Tools 1.45x faster** |
| **Median Adapter Transformation**| **0.0010 s** | **0.0214 s** | **OR-Tools 21.4x faster** |
| **Median External Evaluator Time**| **0.0002 s** | **0.0003 s** | Independent validator overhead |
| **Median Total Wall Clock**| **0.5311 s** | **0.7919 s** | **OR-Tools 1.49x faster** |

### 3.3 Dimension 3: Soft Violations & Optimization Metrics
| Metric | OR-Tools-Native | UniTime CPSolver | Target / Baseline |
| :--- | :---: | :---: | :---: |
| **Preferred Room Satisfaction** | 100.0% (10/10) | 100.0% (10/10) | $\ge 80.0\%$ |
| **Preferred Invigilator Satisfaction** | 90.0% (9/10) | 100.0% (10/10) | $\ge 80.0\%$ |
| **Equipment Satisfaction Rate** | 100.0% (2/2) | 100.0% (2/2) | 100% mandatory |
| **Invigilator Workload Variance** | 0.3333 | 1.0000 | Lower is more balanced |
| **Evaluation Note** | Valid schedule | Valid schedule | Independent evaluator metrics |

*Optimality Claim Correction*: Both engines produced 100% valid schedules without hard violations. Evaluator scores are reported directly; no mathematical equivalence of the two distinct native objective formulations is implied.

### 3.4 Dimension 4: Schedule Quality
- **Room Allocation**: Both engines allocated appropriate rooms satisfying seating capacity.
- **Invigilator Rostering**: UniTime maximized preferred invigilator match (100%) by concentrating 4 sessions on one invigilator (workload variance 1.00). OR-Tools balanced invigilator workload (variance 0.33) while achieving 90% preference satisfaction.
- **Equipment Allocation**: Both engines successfully allocated `res-eq-004` (`LAB_KIT`) to `session-science-g10a` and `session-science-g10b` without temporal collision.

### 3.5 Dimension 5: Incremental-Change Stability (6 Disruption Scenarios)
All 6 required repair scenarios were evaluated against `SC-EXAM-REPAIR-001` containing 9 locked published assignments and 1 disrupted session.

| Disruption Scenario | OR-Tools Status | OR-Tools Churn (Moved / Locked Moved) | UniTime Status | UniTime Churn (Moved / Locked Moved) | Lock Invariant Satisfied? |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Faculty Absence (`res-inv-001`)** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |
| **2. Room Outage (`res-room-003`)** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |
| **3. Exam Gap Edge Case** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |
| **4. Fixed Event Lock** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |
| **5. Locked Published Assignment** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |
| **6. Partial Repair Churn Comparison** | FEASIBLE | 1 moved / 0 locked | FEASIBLE | 1 moved / 0 locked | **YES** |

- **Zero Locked Mutations**: Strictly 0 locked assignments were mutated across all 6 scenarios by either solver.
- **Minimal Churn**: Exactly 1 session moved in all disruption cases (the session directly affected by the disruption).

### 3.6 Dimension 6: Adapter Complexity
| Dimension | OR-Tools-Native Compiler | UniTime-CPSolver Adapter |
| :--- | :--- | :--- |
| **Source File** | `app/scheduling/education/ortools_compiler.py` | `app/scheduling/education/adapters/unitime.py` |
| **Lines of Code (LOC)** | ~268 LOC | ~644 LOC |
| **Transformation Stages** | 1 (direct in-process object translation) | 4 (ID map $\to$ XML build $\to$ subprocess $\to$ XML parse + post-solve equipment binding) |
| **External Runtime Processes** | 0 (in-process C++ wheel) | 1 (`java -cp ... org.cpsolver.exam.Test`) |
| **Runtime Dependencies** | `ortools` (pre-installed Python package) | Java 8+ JRE, `cpsolver-all-1.4.94.jar` |
| **Failure Surfaces** | Python exceptions | Missing Java, invalid classpath, XML parse errors, subprocess timeouts/crashes |
| **Configuration Model** | Standard `SolverProfile` | Typed `UniTimeRuntimeConfig` |

### 3.7 Dimension 7: Deployment & Operations Cost
- **OR-Tools-Native**:
  - In-process native library bundled with standard Python wheel.
  - Requires zero auxiliary container images, sidecars, or Java JRE installations.
  - Lightweight execution footprint running inside the existing Celery worker pool.
- **UniTime-CPSolver**:
  - Multi-runtime architecture requiring Java 8+ JRE and external classpath JARs.
  - Requires managing external JVM subprocesses or running a standalone Java daemon.
  - Higher structural memory footprint (JVM process heap + metaspace vs. in-process shared library).
  - *Memory Claim Disposition*: Previously claimed numeric RSS figures (~20 MB vs ~250 MB) have been removed from ADR claims due to lack of reproducible process-isolated measurement instrumentation. The architectural comparison is stated qualitatively.

### 3.8 Dimension 8: Explainability & Infeasibility Handling (`PHASE5-SC-EXAM-INFEASIBLE-001`)
Scenario `PHASE5-SC-EXAM-INFEASIBLE-001` compresses 3 major examinations into a 3-calendar-day horizon. Under the mandatory 1-clear-day gap policy, scheduling 3 major exams requires at least 5 calendar days.

| Capability | OR-Tools-Native Academic Profile | UniTime-CPSolver Adapter |
| :--- | :--- | :--- |
| **Solver Status** | `INFEASIBLE` | `INFEASIBLE` |
| **Detection Stage** | **Native Solver Engine** (CP-SAT) | **E-7 Adapter Prevalidation** |
| **Detection Latency** | 0.0070 seconds | 0.0001 seconds |
| **Diagnostic Attribution** | `RESOURCE_OVERLAP_CONFLICT` (extracted from native CP-SAT assumption literals) | `E7_ADAPTER_PREVALIDATION_INFEASIBLE` (detected by E-7 adapter prevalidation prior to CPSolver execution) |
| **Native Solver Executed?** | Yes | No (caught at adapter prevalidation gate) |
| **Partial Schedule Leakage** | 0 sessions assigned (Fail-closed) | 0 sessions assigned (Fail-closed) |

---

## 4. Legal & Licensing Audit Summary
* **OR-Tools**: Apache-2.0. Permissive; zero viral copyleft risk.
* **UniTime CPSolver**: Apache-2.0 (v4.9.152, CPSolver 1.4.94). Permissive license.
* **FET 7.10.5**: GNU AGPL-3.0 copyleft.
  - Gate Status: `FET_BENCHMARK_LEGAL_GATE = NOT_APPROVED`.
  - Benchmark Status: Set to `NOT_RUN_LEGAL_GATE_NOT_APPROVED`.
  - Zero FET binaries or source trees packaged or downloaded in the repository.

---

## 5. Machine-Readable Evidence Persistence
The authoritative, reproducible machine-readable benchmark evidence is persisted in:
`docs/phase-5/PHASE_5_BENCHMARK_EVIDENCE.json`

It captures:
- Benchmark version (`2.0.0`)
- Identical input dataset SHA-256 hashes
- Full environment details
- 3 raw trial latencies for each solver
- Median solve and wall durations
- Canonical evaluator soft metrics and equipment satisfaction
- All 6 disruption scenario results with lock invariant verification
- Structured comparison across all 8 required dimensions
- Zero machine secrets or local path leaks

---

## 6. Final Recommendation & Production Architecture
1. **Production Choice**: **OR-Tools-Native Academic Profile** via `AcademicConstraintCompiler` is confirmed and **ACCEPTED** as the primary production education scheduler.
2. **Reference Adapter**: `UniTimeSchedulerAdapter` is retained in `app/scheduling/education/adapters/` with explicit typed configuration (`UniTimeRuntimeConfig`) for institutional clients requiring native UniTime XML interop.
3. **Phase-5 Exit Gate**: All Phase-5 benchmark closure requirements are fully satisfied. Phase 5 is ready to freeze.
