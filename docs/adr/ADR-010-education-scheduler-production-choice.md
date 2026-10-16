# ADR-010: Education Scheduler Production Implementation Choice

## Status
Accepted

## Date
2026-10-05

## Context
Phase 4 established Google OR-Tools CP-SAT as the canonical general-purpose scheduling engine for the IR Infotech E-7 Operations Engine (ADR-003). Phase 5 requires selecting the authoritative production education-specific scheduling implementation without compromising the frozen generic scheduling architecture, canonical PostgreSQL system of record, or tenant-isolation boundaries.

The comparative evaluation pitted two mandatory candidate paths against the identical canonical education dataset:
1. **Candidate A (UniTime Service / Adapter)**: Decoupled adapter interfacing with the UniTime CPSolver timetabling engine (Apache-2.0).
2. **Candidate B (OR-Tools-Native Academic Profile)**: Domain-specific academic constraint compiler mapping educational requirements onto the frozen Phase-4 `CpSatScheduler` and constraint catalog.
3. **Candidate C (FET 7.10.5 Benchmark/Fallback)**: Governed by strict AGPL-3.0 legal screening (`FET_BENCHMARK_LEGAL_GATE`).

## Canonical Dataset & Provenance
All candidate solvers were benchmarked against identical canonical fixture datasets in the dedicated Phase-5 benchmark directory `tests/fixtures/scheduling/education/phase5/` with verified cryptographic integrity:
* `PHASE5-RS-EDU-001@2.0.0` (Resource Catalog): SHA-256 `4a1ca0d0fcccbd53c336d15b1efea75340bc745bbfa390fb217e07f2f57ef02a`
* `PHASE5-SC-EXAM-FEASIBLE-001@2.0.0` (Feasible Exam Session Baseline): SHA-256 `96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a`
* `PHASE5-SC-EXAM-INFEASIBLE-001@2.0.0` (Impossible Spacing Overlap): SHA-256 `ed40a105179764f6794c3a2e1a7543c34fee85a12439a756a6661a2c7de79f67`
* `PHASE5-SC-EXAM-REPAIR-001@2.0.0` (Disruption & Lock Preservation): SHA-256 `46cb07ef7e6f089dfa32850a5df23cab1c355509c05f028d74e8536259e0e8a4`
* Combined Canonical Benchmark Hash: `e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b`

Frozen Phase-0 baseline fixtures remain immutable and isolated.

Canonical entity inventory: 2 student cohorts (Grade 10A [24 students], Grade 10B [30 students]), 5 academic subjects (Mathematics, Social Studies, Science, Computer Applications, Language), 10 examination sessions (2 hours each), 3 rooms (Exam Rooms Alpha/Beta, Science Lab), 4 invigilators with role/subject skills, 1 equipment resource (`res-eq-004`, `LAB_KIT`), 1 clear calendar day major subject gap policy, and 9 locked published baseline assignments. Real student PII was strictly excluded.

## Licensing & Compliance Screening
* **OR-Tools**: Apache-2.0. Verified in `third_party/licenses/LICENSE.ortools.txt`. Permissive in-process Python C-extension; zero contamination.
* **UniTime / CPSolver**: Apache-2.0 (v4.9.152, CPSolver 1.4.94). Verified in `third_party/licenses/LICENSE.unitime.txt`. Permissive license, but introduces Java runtime dependency.
* **FET 7.10.5**: AGPL-3.0 copyleft. Evaluated against repository legal governance. `FET_BENCHMARK_LEGAL_GATE = NOT_APPROVED`. Status set to `NOT_RUN_LEGAL_GATE_NOT_APPROVED`. FET was neither installed, packaged, nor embedded into E-7 application images or containers, preserving compliance.

## Benchmark Methodology & Environment
* **Platform**: Windows (amd64), Python 3.12, Java JRE 1.8.0_491, OR-Tools 9.15, UniTime CPSolver 1.4.94.
* **Fairness**: Identical problem semantics, identical horizon (2026-11-09T08:00 to 2026-11-13T17:00 Asia/Kolkata), identical single-worker thread budget (`Parallel.NrSolvers=1`, `num_search_workers=1`), identical seed (42), and identical 10-second timeout budget.
* **Independent Canonical Evaluator**: Zero solver self-certification. Every solver output was transformed into canonical `CanonicalEducationAssignment` instances and independently evaluated by `EducationScheduleEvaluator` for 10 hard constraint categories (including `REQUIRED_EQUIPMENT_AVAILABLE` and `NO_EQUIPMENT_DOUBLE_BOOKING`) and normalized soft metrics.

## Empirical Benchmark Results

### 1. Feasibility & Solve Duration (Median of 3 Trials)
| Metric | OR-Tools-Native Profile | UniTime-CPSolver Adapter | Relative Delta |
| :--- | :---: | :---: | :---: |
| **Solver Status** | **FEASIBLE** | **FEASIBLE** | Identical |
| **Hard Violations** | **0** | **0** | Identical (Proven 0) |
| **Evaluator Valid** | **True** | **True** | Identical |
| **Solve Wall Duration** | **0.5299 s** | **0.7695 s** | **OR-Tools 1.45x faster** |
| **Adapter/Transformation** | **0.0010 s** | **0.0214 s** | **OR-Tools 21.4x faster** |
| **Total Wall Clock Duration**| **0.5311 s** | **0.7919 s** | **OR-Tools 1.49x faster** |

### 2. Soft Objective Quality
| Metric | OR-Tools-Native Profile | UniTime-CPSolver Adapter | Notes |
| :--- | :---: | :---: | :--- |
| **Preferred Room Satisfaction** | 100.0% | 100.0% | Both placed in preferred rooms |
| **Preferred Invigilator Satisfaction**| 90.0% | 100.0% | Both exceed acceptable threshold |
| **Equipment Satisfaction Rate** | 100.0% | 100.0% | Both allocated required `LAB_KIT` equipment |
| **Invigilator Workload Variance**| 0.3333 | 1.0000 | OR-Tools achieves more balanced spread |
| **Soft Quality Assessment** | Valid schedule | Valid schedule | Reported via independent evaluator |

*Note on Optimality*: Both solvers produced 100% valid schedules without hard violations. Evaluator metrics are reported directly; no mathematical equivalence of underlying solver objective functions is claimed.

### 3. Repair & Stability Benchmark (6 Edge Cases)
| Disruption Scenario | OR-Tools (Hard / Moved / Locked Moved) | UniTime (Hard / Moved / Locked Moved) | Lock Invariant Satisfied? |
| :--- | :---: | :---: | :---: |
| **1. Faculty Absence (`res-inv-001`)** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Zero locked moved) |
| **2. Room Outage (`res-room-003`)** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Zero locked moved) |
| **3. Exam Gap Edge Case** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Zero locked moved) |
| **4. Fixed Event Lock** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Zero locked moved) |
| **5. Locked Published Assignment** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Zero locked moved) |
| **6. Partial Repair Churn Comparison** | 0 / 1 / 0 | 0 / 1 / 0 | **YES** (Minimal churn = 1) |

Both solvers achieved identical repair stability: exactly 1 reassignment (reallocating the affected session to an eligible alternate resource), 0 unneeded room reassignments, 0 time shifts, and strictly 0 mutations of locked published assignments.

### 4. Infeasible Dataset Handling (`PHASE5-SC-EXAM-INFEASIBLE-001`)
* **OR-Tools-Native**: Detected `INFEASIBLE` in **0.007 seconds**. Extracted sufficient conflict subset via native CP-SAT assumption literals (`RESOURCE_OVERLAP_CONFLICT`), isolating the major subject gap window unsatisfiability.
* **UniTime-CPSolver**: Detected `INFEASIBLE` at E-7 adapter prevalidation stage (`E7_ADAPTER_PREVALIDATION`). Correctly diagnosed gap policy deficiency (3 major exams require at least 5 days under 1-day clear gap policy, but horizon spans only 3 days) prior to external CPSolver execution.
* **Silent Partial Success**: Zero tolerance; both candidates returned 0 partial assignments (fail-closed).

### 5. Operational Complexity & Deployment Footprint
| Dimension | OR-Tools-Native Profile | UniTime Service / Adapter | Production Impact |
| :--- | :--- | :--- | :--- |
| **Process Model** | In-process native Python (C++ wheel) | Headless Java subprocess / standalone service | OR-Tools requires 0 subprocesses |
| **Runtime Dependencies** | None (already locked in `ortools>=9.10.0`) | Java 8+ JRE, Log4j2, Dom4j, XML parsers | UniTime adds external JRE dependencies |
| **Container Topology** | No additional containers | Requires dedicated sidecar container | UniTime increases deployment complexity |
| **Memory Architecture** | Native in-process shared memory | Separate JVM process with heap & metaspace | OR-Tools avoids JVM process overhead |
| **Mapping Complexity** | 0 external ID mappings (uses canonical E-7 IDs) | 4 bidirectional dictionary translations + XML | UniTime incurs secondary mapping drift |
| **Explainability** | Native assumption-literal conflict extractor | Adapter-level horizon checks; global penalty reports | OR-Tools provides fine-grained root-cause |

## Decision
1. **OR-Tools-Native Academic Profile** is selected as the **PRIMARY PRODUCTION EDUCATION SCHEDULER** for the IR Infotech E-7 Operations Engine.
2. **UniTime Adapter** is retained as an **OPTIONAL REFERENCE ADAPTER** for institutional clients requiring native UniTime XML import/export compatibility. It will not be deployed as a default production runtime or container dependency.
3. **FET 7.10.5** remains **NOT_RUN_LEGAL_GATE_NOT_APPROVED**. Packaging or embedding FET into E-7 core remains strictly prohibited due to AGPL-3.0 copyleft isolation boundaries.
4. **Canonical Authority**: E-7 PostgreSQL retains exclusive canonical ownership of all schedule IDs, resources, constraints, publication lifecycle, repair lineage, audit history, and external event synchronization.

## Rationale
Per the Phase-5 Decision Rule: *"Adopt UniTime only if evidence shows it materially reduces education constraint implementation and maintenance burden while preserving E-7 API, canonical data model, authorization, audit, repair/publication architecture. Otherwise prefer the existing OR-Tools-native path."*

The benchmark demonstrates conclusively that:
1. **Constraint Parity & Equipment Coverage**: The OR-Tools native academic compiler (`AcademicConstraintCompiler`) cleanly expresses all domain-specific academic constraints (cohort double-booking, room capacity/equipment eligibility, invigilator skill/role certification, allowed scheduling windows, equipment resource binding, and multi-day major subject gap clearance) directly through the frozen Phase-4 generic constraint catalog (`NO_OVERLAP`, `MINIMUM_GAP`, `TIME_WINDOW`, `ELIGIBILITY`, `LOCK`).
2. **Superior Performance**: OR-Tools showed lower engine and end-to-end latency on the canonical Phase-5 benchmark (1.45x engine solve advantage, 1.49x total wall-clock advantage) with 21.4x lower adapter transformation overhead.
3. **Zero Operational Overhead**: OR-Tools executes in-process within existing `scheduling-worker` processes with zero auxiliary Java services, zero JVM container sidecars, and no external runtime lifecycles.
4. **Superior Explainability**: CP-SAT assumption literals provide deterministic sufficient conflict subsets for automated operator repair diagnostics, whereas UniTime relies on aggregated objective penalty reports and adapter prevalidation.
5. **Architectural Cohesion**: Choosing OR-Tools-native eliminates the need to maintain duplicate constraint logic across two heterogeneous solver ecosystems (Python OR-Tools vs Java UniTime).

## Consequences

### Positive
* Single unified constraint programming engine across general and academic scheduling domains.
* Zero container bloat; no Java/JVM runtime required in production scheduling workers.
* Zero risk of secondary schema drift or external ID synchronization failures.
* Sub-second execution for standard semester timetables and rapid disruption repairs.
* Complete compliance safety under Apache-2.0 without AGPL or third-party notice contamination.

### Negative / Trade-offs
* Extremely massive institutional curriculum timetabling (10,000+ classes with complex student course sectioning) would require advanced decomposition heuristics in CP-SAT compared to UniTime's specialized CourseTT module.
* Maintaining the UniTime reference adapter requires preserving secondary XML serializers for client interoperability.

## Conditions for Reconsideration
* UniTime may be evaluated for standalone deployment only if future enterprise higher-education clients explicitly mandate direct integration with existing on-premises UniTime installations, or if curriculum sectioning problem sizes exceed OR-Tools single-node memory bounds during Phase-8 stress testing.

## References
* [`docs/adr/ADR-003-scheduling-solver-strategy.md`](file:///f:/operations-engine/docs/adr/ADR-003-scheduling-solver-strategy.md)
* [`docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md`](file:///f:/operations-engine/docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md)
* [`tests/fixtures/scheduling/exam/`](file:///f:/operations-engine/tests/fixtures/scheduling/exam/)
* [`docs/phase-5/PHASE_5_BENCHMARK_REPORT.md`](file:///f:/operations-engine/docs/phase-5/PHASE_5_BENCHMARK_REPORT.md)
