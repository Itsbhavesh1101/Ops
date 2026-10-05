# Phase 4A Infeasibility Diagnostics & Conflict Explanations

---

## 1. Governance Principles

1. **INFEASIBLE is Normal**: An infeasible scheduling request is an expected business outcome when constraints conflict. It must **never** be reported as `FAILED`, HTTP 500, or an empty success schedule.
2. **Deterministic Explanations**: Diagnostic explanations and conflict sets are generated via deterministic algorithms. LLMs are never used for constraint enforcement or conflict determination.
3. **Non-Authoritative Relaxations**: Suggested relaxation candidates are presented strictly for human operator review. The solver engine never applies relaxations autonomously.

---

## 2. Assumption-Based Conflict Extraction

OR-Tools CP-SAT provides assumption-based sufficient conflict set analysis:
- Each candidate hard constraint $c$ is guarded by a boolean literal $a_c$.
- All assumptions are passed to `model.AddAssumptions(...)`.
- When the solver returns `cp_model.INFEASIBLE`, it extracts:
  $$\mathcal{C}_{\text{conflict}} = \text{solver.SufficientAssumptionsForInfeasibility}()$$
- This identifies a sufficient conflict set of constraints that cannot be simultaneously satisfied.

---

## 3. Canonical Diagnostic Schema

The `InfeasibilityDiagnostic` maps solver internals back to canonical entities:
```json
{
  "root_cause_type": "CAPACITY_OR_OVERLAP_CONFLICT",
  "human_readable_explanation": "Conflict detected involving constraints ['c-overlap', 'c-cap'] for resources ['tech-1'] and activities ['act-1', 'act-2'].",
  "conflicting_constraint_ids": ["c-overlap", "c-cap"],
  "affected_activity_ids": ["act-1", "act-2"],
  "affected_resource_ids": ["tech-1"],
  "relaxation_candidates": [
    "consider adding alternate eligible resources",
    "expand activity scheduling time windows",
    "increase capacity limit"
  ]
}
```

---

## 4. Pre-Solve Analysis

Certain conflicts are mathematically unsatisfiable prior to CP-SAT instantiation:
- **`PERSON_CAPABILITY_COMBINATION_UNSATISFIABLE`**: When an activity requires skills that exist on zero resources in the catalog.
- Detected immediately during model construction, generating a diagnostic without solver crashes.
