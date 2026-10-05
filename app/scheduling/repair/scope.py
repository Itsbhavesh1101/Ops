"""Deterministic repair scope builder for targeted incremental schedule repair.

Part of Phase 4B Generic Scheduling.
Implements:
- Direct impact identification from disrupted resources and time intervals.
- Selective constraint propagation (precedence, minimum gap, shared capacity).
- Strict partition of activities into affected vs unaffected (frozen baseline).
- Preservation of explicit baseline locks.
"""

from datetime import datetime

from app.db.models.scheduling import ScheduleAssignmentRecord
from app.scheduling.constraints.models import MinimumGapConstraint, SchedulingConstraint
from app.scheduling.repair.models import RepairScope, ScheduleRepairRequest
from app.scheduling.schemas import SchedulingActivity


def _intervals_overlap(
    start1: datetime, end1: datetime, start2: datetime, end2: datetime
) -> bool:
    """Return True if two half-open intervals [start1, end1) and [start2, end2) overlap."""
    return start1 < end2 and end1 > start2


class RepairScopeBuilder:
    """Constructs deterministic repair scopes for a published baseline schedule."""

    def build_scope(
        self,
        *,
        activities: list[SchedulingActivity],
        baseline_assignments: list[ScheduleAssignmentRecord],
        constraints: list[SchedulingConstraint],
        repair_request: ScheduleRepairRequest,
    ) -> RepairScope:
        """Compute the deterministic partition of affected, unaffected, and locked activities.

        Rules:
        1. Direct impact:
           - Activity explicitly named in repair_request.affected_activity_ids.
           - Assignment in baseline assigned to a disrupted resource whose time window overlaps
             the disruption interval [disrupted_interval_start, disrupted_interval_end).
        2. Propagation:
           - Expand along tight precedence or minimum-gap constraints only when the directly
             impacted activity cannot move within its remaining slack without displacing neighbors.
        3. Baseline locks:
           - Any assignment marked is_locked=True in the baseline is recorded. If disrupted,
             it is kept locked so the solver can prove INFEASIBILITY rather than silently moving it.
        4. Unaffected work:
           - Any activity not affected is marked unaffected and will be frozen during repair solve.
        """
        directly_impacted: set[str] = set(repair_request.affected_activity_ids)
        locked_in_baseline: set[str] = set()

        disrupted_res = set(repair_request.disrupted_resource_ids)
        disp_start = repair_request.disrupted_interval_start
        disp_end = repair_request.disrupted_interval_end

        activity_map: dict[str, SchedulingActivity] = {act.id: act for act in activities}
        assignment_map: dict[str, ScheduleAssignmentRecord] = {
            asgn.activity_id: asgn for asgn in baseline_assignments
        }

        for asgn in baseline_assignments:
            if asgn.is_locked:
                locked_in_baseline.add(asgn.activity_id)

            if asgn.resource_id in disrupted_res:
                if disp_start is not None and disp_end is not None:
                    if _intervals_overlap(asgn.start_at, asgn.end_at, disp_start, disp_end):
                        directly_impacted.add(asgn.activity_id)
                else:
                    directly_impacted.add(asgn.activity_id)

        # 2. Targeted propagation: check precedence, minimum gap, and resource coupling
        propagated: set[str] = set()
        propagation_reasons: dict[str, str] = {}
        to_evaluate = set(directly_impacted)

        # Build forward successor mapping from activity definitions and PrecedenceConstraints
        successors_map: dict[str, set[str]] = {act.id: set() for act in activities}
        for act in activities:
            for pred_id in act.predecessors:
                if pred_id in successors_map:
                    successors_map[pred_id].add(act.id)

        for c in constraints:
            if hasattr(c, "predecessor_id") and hasattr(c, "successor_id"):
                pred_id = c.predecessor_id
                succ_id = c.successor_id
                if pred_id in successors_map:
                    successors_map[pred_id].add(succ_id)

        while to_evaluate:
            curr_act_id = to_evaluate.pop()
            curr_act = activity_map.get(curr_act_id)
            curr_asgn = assignment_map.get(curr_act_id)
            if not curr_act or not curr_asgn:
                continue

            # A. Successor propagation: if curr_act moves, tightly chained successors must move
            for succ_id in successors_map.get(curr_act_id, set()):
                if succ_id not in directly_impacted and succ_id not in propagated:
                    succ_asgn = assignment_map.get(succ_id)
                    if succ_asgn and succ_asgn.start_at <= curr_asgn.end_at:
                        propagated.add(succ_id)
                        propagation_reasons[succ_id] = (
                            f"PRECEDENCE_SUCCESSOR: downstream dependent of {curr_act_id}"
                        )
                        to_evaluate.add(succ_id)

            # B. Predecessor propagation: tightly chained predecessors
            for pred_id in curr_act.predecessors:
                if pred_id not in directly_impacted and pred_id not in propagated:
                    pred_asgn = assignment_map.get(pred_id)
                    if pred_asgn and pred_asgn.end_at >= curr_asgn.start_at:
                        propagated.add(pred_id)
                        propagation_reasons[pred_id] = (
                            f"PRECEDENCE_PREDECESSOR: upstream predecessor of {curr_act_id}"
                        )
                        to_evaluate.add(pred_id)

            # C. Minimum gap constraints
            for c in constraints:
                if isinstance(c, MinimumGapConstraint):
                    acts_in_c = c.activity_ids
                    if curr_act_id in acts_in_c:
                        for other_id in acts_in_c:
                            if (
                                other_id != curr_act_id
                                and other_id not in directly_impacted
                                and other_id not in propagated
                            ):
                                other_asgn = assignment_map.get(other_id)
                                if other_asgn:
                                    gap_seconds = c.min_gap_minutes * 60
                                    diff = abs(
                                        (other_asgn.start_at - curr_asgn.end_at).total_seconds()
                                    )
                                    if diff < gap_seconds:
                                        propagated.add(other_id)
                                        propagation_reasons[other_id] = (
                                            f"MINIMUM_GAP: coupled with {curr_act_id} "
                                            f"via constraint {c.id}"
                                        )
                                        to_evaluate.add(other_id)

            # D. Shared resource contention coupling
            for other_asgn in baseline_assignments:
                other_id = other_asgn.activity_id
                if (
                    other_id != curr_act_id
                    and other_id not in directly_impacted
                    and other_id not in propagated
                    and other_asgn.resource_id == curr_asgn.resource_id
                ):
                    if _intervals_overlap(
                        curr_asgn.start_at,
                        curr_asgn.end_at,
                        other_asgn.start_at,
                        other_asgn.end_at,
                    ):
                        propagated.add(other_id)
                        propagation_reasons[other_id] = (
                            f"SHARED_RESOURCE: conflicting overlap on resource "
                            f"{curr_asgn.resource_id} with {curr_act_id}"
                        )
                        to_evaluate.add(other_id)

        affected = directly_impacted | propagated
        all_activity_ids = {act.id for act in activities}
        unaffected = all_activity_ids - affected

        return RepairScope(
            directly_impacted_activity_ids=directly_impacted,
            propagated_activity_ids=propagated,
            affected_activity_ids=affected,
            unaffected_activity_ids=unaffected,
            locked_baseline_activity_ids=locked_in_baseline,
            propagation_reasons=propagation_reasons,
        )
