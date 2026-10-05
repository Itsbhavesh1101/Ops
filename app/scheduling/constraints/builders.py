"""Modular CP-SAT hard constraint builders and variable registries.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Interval variables for all scheduled activities.
- Boolean assignment variables per (activity, eligible resource).
- NO_OVERLAP (exclusive resources).
- CAPACITY (cumulative resources).
- ELIGIBILITY (skills and allowlists).
- PRECEDENCE (finish-to-start ordering with min gap).
- TIME_WINDOW (scheduling windows and blackout avoidance).
- MINIMUM_GAP (separation between pairs).
- LOCK (fixed time and/or resource pins).
- Assumption literal attachment for conflict extraction.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ortools.sat.python import cp_model

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import (
    CapacityConstraint,
    ConstraintType,
    LockConstraint,
    MinimumGapConstraint,
    NoOverlapConstraint,
    PrecedenceConstraint,
    TimeWindowConstraint,
)
from app.scheduling.profiles.discretization import (
    datetime_to_tick,
    minutes_to_ticks,
    ticks_to_minutes,
)
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)


@dataclass
class ActivityVariables:
    """Internal CP-SAT variables associated with a single activity."""

    activity_id: str
    start_var: cp_model.IntVar
    end_var: cp_model.IntVar
    duration_ticks: int
    interval_var: cp_model.IntervalVar
    # Map resource_id -> (boolean assignment var, optional interval var)
    resource_assignments: dict[str, tuple[cp_model.IntVar, cp_model.IntervalVar]] = field(
        default_factory=dict
    )


class ModelBuildContext:
    """State and variable registry for CP-SAT model construction."""

    def __init__(self, request: SchedulingRequest, model: cp_model.CpModel) -> None:
        self.request = request
        self.model = model
        self.granularity = request.profile.time_granularity_minutes
        self.horizon_start = request.horizon_start
        self.horizon_end = request.horizon_end

        # Calculate horizon ticks
        self.horizon_ticks = datetime_to_tick(
            self.horizon_end, self.horizon_start, self.horizon_end, self.granularity
        )

        self.activities_by_id: dict[str, SchedulingActivity] = {
            a.id: a for a in request.activities
        }
        self.resources_by_id: dict[str, SchedulingResourceSnapshot] = {
            r.id: r for r in request.resources
        }

        # Variable registries
        self.act_vars: dict[str, ActivityVariables] = {}
        # Resource -> list of optional intervals (and demands)
        self.resource_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
        self.resource_demands: dict[str, list[int]] = defaultdict(list)

        # Assumptions mapping: assumption_lit_name -> metadata dict
        self.assumption_literals: dict[int, dict[str, Any]] = {}
        self.assumptions: list[cp_model.IntVar] = []

        # Tracking structural eligibility failures detected before/during build
        self.pre_solve_infeasibilities: list[dict[str, Any]] = []

    def get_tick(self, dt: datetime) -> int:
        """Convert datetime to tick within context horizon."""
        return datetime_to_tick(dt, self.horizon_start, self.horizon_end, self.granularity)

    def create_assumption(
        self, constraint_id: str, constraint_type: str, details: dict[str, Any]
    ) -> cp_model.IntVar:
        """Register an assumption literal to isolate conflicts during infeasibility."""
        asmp_name = f"asmp_{constraint_type}_{constraint_id}_{len(self.assumptions)}"
        lit = self.model.new_bool_var(asmp_name)
        self.assumptions.append(lit)
        self.assumption_literals[lit.Index()] = {
            "constraint_id": constraint_id,
            "constraint_type": constraint_type,
            "details": details,
        }
        return lit


def build_activity_variables(ctx: ModelBuildContext) -> None:
    """Create start, end, and duration interval variables for every activity."""
    for act in ctx.request.activities:
        duration_ticks = minutes_to_ticks(act.duration_minutes, ctx.granularity)
        max_start = max(0, ctx.horizon_ticks)
        max_end = max(duration_ticks, ctx.horizon_ticks + duration_ticks)

        start_var = ctx.model.new_int_var(0, max_start, f"start_{act.id}")
        end_var = ctx.model.new_int_var(duration_ticks, max_end, f"end_{act.id}")
        interval_var = ctx.model.new_interval_var(
            start_var, duration_ticks, end_var, f"interval_{act.id}"
        )

        # Enforce that activity must finish within the horizon under an assumption
        horizon_asmp = ctx.create_assumption(
            constraint_id=f"horizon_{act.id}",
            constraint_type=ConstraintType.TIME_WINDOW,
            details={
                "activity_id": act.id,
                "duration_minutes": act.duration_minutes,
                "horizon_minutes": ticks_to_minutes(ctx.horizon_ticks, ctx.granularity),
            },
        )
        ctx.model.add(end_var <= ctx.horizon_ticks).only_enforce_if(horizon_asmp)

        ctx.act_vars[act.id] = ActivityVariables(
            activity_id=act.id,
            start_var=start_var,
            end_var=end_var,
            duration_ticks=duration_ticks,
            interval_var=interval_var,
        )


def build_resource_assignments_and_eligibility(ctx: ModelBuildContext) -> None:
    """Determine eligible resources per category, build assignment variables."""
    # Group resources by type
    resources_by_type: dict[ResourceType, list[SchedulingResourceSnapshot]] = defaultdict(list)
    for r in ctx.request.resources:
        resources_by_type[r.type].append(r)

    for act in ctx.request.activities:
        act_var = ctx.act_vars[act.id]

        # Check if any required skill is completely unrepresented across all resources
        missing_skills = [
            skill for skill in act.required_skills
            if not any(skill in r.skills for r in ctx.request.resources)
        ]
        if missing_skills:
            ctx.pre_solve_infeasibilities.append({
                "root_cause_type": "PERSON_CAPABILITY_COMBINATION_UNSATISFIABLE",
                "activity_id": act.id,
                "resource_type": "PERSON",
                "required_skills": act.required_skills,
                "explanation": (
                    f"Activity '{act.title}' ({act.id}) requires capabilities "
                    f"{missing_skills} which do not exist on any available resource."
                ),
            })
            continue

        # Determine required resource types for this activity
        needed_types: set[ResourceType] = set()
        if act.required_resource_ids:
            for rid in act.required_resource_ids:
                if rid in ctx.resources_by_id:
                    needed_types.add(ctx.resources_by_id[rid].type)

        if act.required_skills:
            # Check which resource types provide these skills
            for skill in act.required_skills:
                for r in ctx.request.resources:
                    if skill in r.skills:
                        needed_types.add(r.type)

        # Default: if no specific type required, assume PERSON if persons exist
        if not needed_types and resources_by_type[ResourceType.PERSON]:
            needed_types.add(ResourceType.PERSON)

        # For each needed resource type, find eligible candidates
        for r_type in sorted(needed_types, key=lambda t: str(t)):
            eligible_for_type: list[SchedulingResourceSnapshot] = []
            candidates = resources_by_type[r_type]

            # Skills required for this specific resource type
            type_skills = [
                s for s in act.required_skills
                if any(s in res.skills for res in candidates)
            ]

            for r in candidates:
                # Skill matching
                skills_satisfied = all(s in r.skills for s in type_skills)
                # Explicit ID matching if specified for this type
                id_satisfied = True
                if act.required_resource_ids:
                    type_specific_req_ids = [
                        rid for rid in act.required_resource_ids
                        if rid in ctx.resources_by_id and ctx.resources_by_id[rid].type == r_type
                    ]
                    if type_specific_req_ids:
                        id_satisfied = r.id in type_specific_req_ids

                if skills_satisfied and id_satisfied:
                    eligible_for_type.append(r)

            if not eligible_for_type:
                # No eligible resource exists for this activity in this category!
                ctx.pre_solve_infeasibilities.append({
                    "root_cause_type": (
                        "PERSON_CAPABILITY_COMBINATION_UNSATISFIABLE"
                        if r_type == ResourceType.PERSON
                        else "RESOURCE_ELIGIBILITY_UNSATISFIABLE"
                    ),
                    "activity_id": act.id,
                    "resource_type": str(r_type),
                    "required_skills": act.required_skills,
                    "explanation": (
                        f"Activity '{act.title}' ({act.id}) requires capabilities "
                        f"{act.required_skills} but no eligible {r_type} resource exists."
                    ),
                })
                # Add a dummy infeasible constraint with an assumption
                asmp = ctx.create_assumption(
                    constraint_id=f"eligibility_{act.id}_{r_type}",
                    constraint_type=ConstraintType.ELIGIBILITY,
                    details={
                        "activity_id": act.id,
                        "resource_type": str(r_type),
                        "required_skills": act.required_skills,
                    },
                )
                ctx.model.add_bool_or([]).only_enforce_if(asmp)
                continue

            # Create assignment booleans and optional intervals for eligible resources
            assigned_booleans: list[cp_model.IntVar] = []
            for r in eligible_for_type:
                x_var = ctx.model.new_bool_var(f"x_{act.id}_{r.id}")
                opt_interval = ctx.model.new_optional_interval_var(
                    act_var.start_var,
                    act_var.duration_ticks,
                    act_var.end_var,
                    x_var,
                    f"opt_int_{act.id}_{r.id}",
                )
                act_var.resource_assignments[r.id] = (x_var, opt_interval)
                ctx.resource_intervals[r.id].append(opt_interval)
                ctx.resource_demands[r.id].append(1)
                assigned_booleans.append(x_var)

            # Exactly one resource of this type must be assigned
            asmp = ctx.create_assumption(
                constraint_id=f"assign_{act.id}_{r_type}",
                constraint_type=ConstraintType.ELIGIBILITY,
                details={"activity_id": act.id, "resource_type": str(r_type)},
            )
            ctx.model.add(sum(assigned_booleans) == 1).only_enforce_if(asmp)


def build_resource_availability_and_no_overlap(ctx: ModelBuildContext) -> None:
    """Enforce capacity limits on resources and incorporate blackout intervals."""
    for r in ctx.request.resources:
        intervals = list(ctx.resource_intervals.get(r.id, []))
        demands = list(ctx.resource_demands.get(r.id, []))

        # Add fixed intervals for resource unavailability/blackout windows
        for idx, unavail in enumerate(r.unavailability_windows):
            start_tick = ctx.get_tick(unavail.start_at)
            end_tick = ctx.get_tick(unavail.end_at)
            dur = max(1, end_tick - start_tick)
            fixed_int = ctx.model.new_fixed_size_interval_var(
                start_tick, dur, f"blackout_{r.id}_{idx}"
            )
            intervals.append(fixed_int)
            demands.append(r.capacity if r.capacity > 0 else 1)

        if not intervals:
            continue

        if r.capacity <= 1:
            # Capacity 1 -> strict NoOverlap
            asmp = ctx.create_assumption(
                constraint_id=f"no_overlap_{r.id}",
                constraint_type=ConstraintType.NO_OVERLAP,
                details={"resource_id": r.id},
            )
            ctx.model.add_no_overlap(intervals).only_enforce_if(asmp)
        else:
            # Capacity > 1 -> Cumulative
            asmp = ctx.create_assumption(
                constraint_id=f"capacity_{r.id}",
                constraint_type=ConstraintType.CAPACITY,
                details={"resource_id": r.id, "capacity": r.capacity},
            )
            ctx.model.add_cumulative(intervals, demands, r.capacity).only_enforce_if(asmp)


def build_precedence_constraints(ctx: ModelBuildContext) -> None:
    """Enforce activity ordering predecessors (A finishes before B starts)."""
    # From activity schema predecessors
    for act in ctx.request.activities:
        act_var = ctx.act_vars[act.id]
        for pred_id in act.predecessors:
            if pred_id in ctx.act_vars:
                pred_var = ctx.act_vars[pred_id]
                asmp = ctx.create_assumption(
                    constraint_id=f"prec_{pred_id}_{act.id}",
                    constraint_type=ConstraintType.PRECEDENCE,
                    details={"predecessor_id": pred_id, "successor_id": act.id},
                )
                ctx.model.add(pred_var.end_var <= act_var.start_var).only_enforce_if(asmp)


def build_time_window_constraints(ctx: ModelBuildContext) -> None:
    """Enforce activity scheduling strictly inside allowed windows."""
    for act in ctx.request.activities:
        if not act.allowed_windows:
            continue
        act_var = ctx.act_vars[act.id]

        if len(act.allowed_windows) == 1:
            win = act.allowed_windows[0]
            start_tick = ctx.get_tick(win.start_at)
            end_tick = ctx.get_tick(win.end_at)
            details = {
                "activity_id": act.id,
                "start_at": win.start_at.isoformat(),
                "end_at": win.end_at.isoformat(),
            }
            asmp = ctx.create_assumption(
                constraint_id=f"win_{act.id}",
                constraint_type=ConstraintType.TIME_WINDOW,
                details=details,
            )
            ctx.model.add(act_var.start_var >= start_tick).only_enforce_if(asmp)
            ctx.model.add(act_var.end_var <= end_tick).only_enforce_if(asmp)
        else:
            # Multiple allowed windows: disjunction
            win_booleans: list[cp_model.IntVar] = []
            for idx, win in enumerate(act.allowed_windows):
                b = ctx.model.new_bool_var(f"win_choice_{act.id}_{idx}")
                win_booleans.append(b)
                start_tick = ctx.get_tick(win.start_at)
                end_tick = ctx.get_tick(win.end_at)
                ctx.model.add(act_var.start_var >= start_tick).only_enforce_if(b)
                ctx.model.add(act_var.end_var <= end_tick).only_enforce_if(b)

            asmp = ctx.create_assumption(
                constraint_id=f"multi_win_{act.id}",
                constraint_type=ConstraintType.TIME_WINDOW,
                details={"activity_id": act.id, "window_count": len(act.allowed_windows)},
            )
            ctx.model.add(sum(win_booleans) == 1).only_enforce_if(asmp)


def build_lock_constraints(ctx: ModelBuildContext) -> None:
    """Enforce fixed assignment locks on activities."""
    for act in ctx.request.activities:
        if not act.locked_assignment:
            continue
        lock = act.locked_assignment
        act_var = ctx.act_vars[act.id]

        if lock.start_at is not None:
            start_tick = ctx.get_tick(lock.start_at)
            asmp = ctx.create_assumption(
                constraint_id=f"lock_start_{act.id}",
                constraint_type=ConstraintType.LOCK,
                details={"activity_id": act.id, "locked_start_at": lock.start_at.isoformat()},
            )
            ctx.model.add(act_var.start_var == start_tick).only_enforce_if(asmp)

        if lock.end_at is not None:
            end_tick = ctx.get_tick(lock.end_at)
            asmp = ctx.create_assumption(
                constraint_id=f"lock_end_{act.id}",
                constraint_type=ConstraintType.LOCK,
                details={"activity_id": act.id, "locked_end_at": lock.end_at.isoformat()},
            )
            ctx.model.add(act_var.end_var == end_tick).only_enforce_if(asmp)

        for rid in lock.resource_ids:
            if rid in act_var.resource_assignments:
                x_var, _ = act_var.resource_assignments[rid]
                asmp = ctx.create_assumption(
                    constraint_id=f"lock_res_{act.id}_{rid}",
                    constraint_type=ConstraintType.LOCK,
                    details={"activity_id": act.id, "locked_resource_id": rid},
                )
                ctx.model.add(x_var == 1).only_enforce_if(asmp)


def build_explicit_constraints(ctx: ModelBuildContext) -> None:
    """Translate user/profile-defined SchedulingConstraint items into CP-SAT constraints."""
    for c in ctx.request.constraints:
        asmp = ctx.create_assumption(
            constraint_id=c.id,
            constraint_type=c.constraint_type,
            details={"name": c.name, "description": c.description},
        )

        if isinstance(c, NoOverlapConstraint):
            intervals: list[cp_model.IntervalVar] = []
            for rid in c.resource_ids:
                intervals.extend(ctx.resource_intervals.get(rid, []))
            if len(intervals) > 1:
                ctx.model.add_no_overlap(intervals).only_enforce_if(asmp)

        elif isinstance(c, CapacityConstraint):
            intervals = list(ctx.resource_intervals.get(c.resource_id, []))
            demands = [c.demand_per_activity.get(act_id, 1) for act_id in ctx.act_vars]
            if intervals:
                ctx.model.add_cumulative(
                    intervals, demands[: len(intervals)], c.max_capacity
                ).only_enforce_if(asmp)

        elif isinstance(c, PrecedenceConstraint):
            if c.predecessor_id in ctx.act_vars and c.successor_id in ctx.act_vars:
                pred_var = ctx.act_vars[c.predecessor_id]
                succ_var = ctx.act_vars[c.successor_id]
                gap_ticks = minutes_to_ticks(c.min_gap_minutes, ctx.granularity)
                ctx.model.add(
                    pred_var.end_var + gap_ticks <= succ_var.start_var
                ).only_enforce_if(asmp)

        elif isinstance(c, TimeWindowConstraint):
            if c.activity_id in ctx.act_vars:
                act_var = ctx.act_vars[c.activity_id]
                for win in c.allowed_windows:
                    s_tick = ctx.get_tick(win.start_at)
                    e_tick = ctx.get_tick(win.end_at)
                    ctx.model.add(act_var.start_var >= s_tick).only_enforce_if(asmp)
                    ctx.model.add(act_var.end_var <= e_tick).only_enforce_if(asmp)

        elif isinstance(c, MinimumGapConstraint):
            gap_ticks = minutes_to_ticks(c.min_gap_minutes, ctx.granularity)
            acts = [ctx.act_vars[aid] for aid in c.activity_ids if aid in ctx.act_vars]
            for i in range(len(acts)):
                for j in range(i + 1, len(acts)):
                    b_name = f"mingap_{acts[i].activity_id}_{acts[j].activity_id}"
                    b = ctx.model.new_bool_var(b_name)
                    ctx.model.add(
                        acts[i].end_var + gap_ticks <= acts[j].start_var
                    ).only_enforce_if([asmp, b])
                    ctx.model.add(
                        acts[j].end_var + gap_ticks <= acts[i].start_var
                    ).only_enforce_if([asmp, ~b])

        elif isinstance(c, LockConstraint):
            if c.activity_id in ctx.act_vars:
                act_var = ctx.act_vars[c.activity_id]
                if c.locked_start_at:
                    s_tick = ctx.get_tick(c.locked_start_at)
                    ctx.model.add(act_var.start_var == s_tick).only_enforce_if(asmp)
                if c.locked_end_at:
                    e_tick = ctx.get_tick(c.locked_end_at)
                    ctx.model.add(act_var.end_var == e_tick).only_enforce_if(asmp)
                for rid in c.locked_resource_ids:
                    if rid in act_var.resource_assignments:
                        x_var, _ = act_var.resource_assignments[rid]
                        ctx.model.add(x_var == 1).only_enforce_if(asmp)
