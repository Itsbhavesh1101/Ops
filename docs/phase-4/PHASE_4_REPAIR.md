# Phase 4: Targeted Incremental Schedule Repair

## 1. Disruption Model & Repair Scope Builder
When operational disruptions occur (e.g., machine failure, technician illness, urgent unavailability), the `RepairScopeBuilder` deterministically partitions all activities into two disjoint sets:
1. **Disrupted Activities**:
   - Direct overlap: Activities whose baseline interval intersects with the disruption interval AND whose assigned resource matches the unavailable resource.
   - Connected propagation: Successor and predecessor activities linked via `PRECEDENCE` constraints to directly disrupted activities.
   - Resource collision: Activities sharing the disrupted resource that can no longer fit in their assigned slots.
2. **Unaffected Activities**:
   - All activities outside the causal disruption cone.
   - Hard-locked in the repair solver model: fixed start time and fixed resource assignment matching the published baseline.

## 2. Minimal Churn Objective Formulation
To prevent unnecessary schedule reshuffling ("churn"), the repair optimization model incorporates an explicit movement penalty:
```
Objective = Standard Soft Penalties + sum_{a in Disrupted} (Movement Penalty Weight * |start_a - baseline_start_a|)
            + sum_{a in Disrupted} (Resource Change Penalty * [resource_a != baseline_resource_a])
```
- **Unaffected work**: Constrained by `HardLockConstraint` (`LOCK`), guaranteeing 0 movement.
- **Disrupted work**: Free to move within the scheduling horizon, but solver minimizes deviation from the original baseline assignment.
- **Movement Weight**: Configured per repair request (`movement_weight`, default 10).

## 3. Immutability & Version Lineage
- **Baseline Preserved**: The existing `PUBLISHED` schedule is never updated in place.
- **New Candidate Schedule**: The repair job generates a new candidate schedule record with:
  - `version = baseline.version + 1`
  - `supersedes_id = baseline.id`
  - `status = REVIEW`
  - `repair_reason = payload.reason`
  - `repair_scope = scope.model_dump()`
- **Promotion to Active**: The repaired schedule only replaces the baseline when an authorized operator calls `/publish`.

## 4. Conflict Explanation for Infeasible Repairs
If the disruption cannot be resolved within the available horizon and constraints (e.g., conflicting hard locks or insufficient remaining capacity):
- The solver returns `status = INFEASIBLE`.
- No candidate schedule is created.
- Detailed conflict diagnostics identifying the exact conflicting constraint IDs and affected resources are persisted to `schedule_jobs.diagnostics`.
- The published baseline remains active and untouched.
