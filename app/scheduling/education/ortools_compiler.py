"""Academic Constraint Compiler for Google OR-Tools CP-SAT.

Part of Phase 5 Education Scheduling PoC.
Translates canonical education scheduling models into generic Phase-4
constraints (NO_OVERLAP, MINIMUM_GAP, TIME_WINDOW, ELIGIBILITY, LOCK)
without modifying or duplicating the generic solver architecture.
"""

from __future__ import annotations

from typing import Any

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import (
    MinimumGapConstraint,
    SchedulingConstraint,
    TimeWindow,
)
from app.scheduling.education.dtos import (
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
)
from app.scheduling.profiles.models import (
    SoftObjectiveWeights,
    SolverProfile,
)
from app.scheduling.schemas import (
    CandidateAssignment,
    LockedAssignment,
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.scheduling.solvers.models import SolverResult


class AcademicConstraintCompiler:
    """Translates canonical education requirements into generic CP-SAT constraints."""

    def __init__(self, scheduler: CpSatScheduler | None = None) -> None:
        self.scheduler = scheduler or CpSatScheduler()

    def compile_request(
        self,
        dataset: EducationBenchmarkDataset,
        raw_resources: list[SchedulingResourceSnapshot],
        disruptions: list[dict[str, Any]] | None = None,
        profile_override: SolverProfile | None = None,
        is_repair: bool = False,
    ) -> SchedulingRequest:
        """Compile an EducationBenchmarkDataset into a generic SchedulingRequest."""
        exams_by_id = {e.id: e for e in dataset.exams}
        disruptions = disruptions or []

        # 1. Prepare resources
        # For scheduling, rooms and invigilators are exclusive resources (scheduling capacity = 1).
        # Physical seating capacity is enforced via candidate room eligibility filtering.
        resources: list[SchedulingResourceSnapshot] = []
        seating_capacity_by_room: dict[str, int] = {}

        for r in raw_resources:
            unavail = list(r.unavailability_windows)
            for d in disruptions:
                if d.get("resource_id") == r.id:
                    unavail.append(
                        TimeWindow(
                            start_at=d["start_at"],
                            end_at=d["end_at"],
                        )
                    )

            if r.type == ResourceType.ROOM:
                seating_capacity_by_room[r.id] = r.capacity
                resources.append(
                    SchedulingResourceSnapshot(
                        id=r.id,
                        type=r.type,
                        display_label=r.display_label,
                        capacity=1,  # Exclusive room reservation per exam session
                        skills=r.skills,
                        unavailability_windows=unavail,
                    )
                )
            else:
                resources.append(
                    SchedulingResourceSnapshot(
                        id=r.id,
                        type=r.type,
                        display_label=r.display_label,
                        capacity=r.capacity,
                        skills=r.skills,
                        unavailability_windows=unavail,
                    )
                )

        # 2. Build Activities
        baseline_by_sess = {b.session_id: b for b in dataset.published_baseline}
        activities: list[SchedulingActivity] = []
        cohort_sessions: dict[str, list[str]] = {}
        cohort_major_sessions: dict[str, list[str]] = {}

        for sess in dataset.sessions:
            exam = exams_by_id[sess.exam_id]
            cohort_sessions.setdefault(sess.cohort_id, []).append(sess.id)
            if exam.major_subject:
                cohort_major_sessions.setdefault(sess.cohort_id, []).append(sess.id)

            # Candidate rooms: physical capacity must satisfy minimum_room_capacity
            candidate_rooms = [
                rid
                for rid, cap in seating_capacity_by_room.items()
                if cap >= sess.minimum_room_capacity
            ]

            pref_rids = list(sess.preferred_room_ids) + list(sess.preferred_invigilator_ids)

            # Locked / Baseline assignments
            base_asgn = baseline_by_sess.get(sess.id)
            locked: LockedAssignment | None = None
            baseline: LockedAssignment | None = None
            if base_asgn:
                base_rids = [base_asgn.room_resource_id, base_asgn.invigilator_resource_id]
                baseline = LockedAssignment(
                    start_at=base_asgn.start_at,
                    end_at=base_asgn.end_at,
                    resource_ids=base_rids,
                )
                if base_asgn.locked:
                    locked = baseline

            activities.append(
                SchedulingActivity(
                    id=sess.id,
                    title=f"{exam.title} ({sess.cohort_id})",
                    duration_minutes=exam.duration_minutes,
                    allowed_windows=[
                        TimeWindow(start_at=w.start_at, end_at=w.end_at)
                        for w in sess.allowed_windows
                    ],
                    required_skills=list(exam.required_invigilator_skills),
                    required_resource_ids=candidate_rooms,
                    preferred_resource_ids=pref_rids,
                    locked_assignment=locked,
                    baseline_assignment=baseline,
                )
            )

        # 3. Build Constraints
        constraints: list[SchedulingConstraint] = []

        # A. Cohort No-Overlap (min_gap_minutes=1 prevents simultaneous booking)
        for c_id, s_ids in cohort_sessions.items():
            if len(s_ids) > 1:
                constraints.append(
                    MinimumGapConstraint(
                        id=f"cohort_no_overlap_{c_id}",
                        name=f"Cohort No-Overlap {c_id}",
                        activity_ids=s_ids,
                        min_gap_minutes=1,
                    )
                )

        # B. Major Subject Clear Day Gap
        gap_days = dataset.gap_policy.minimum_clear_calendar_days_between
        if gap_days > 0:
            # 1 clear calendar day requires at least 41 hours (2460 min) between operating windows
            min_clear_minutes = (17 + 24 * gap_days) * 60
            for c_id, s_ids in cohort_major_sessions.items():
                if len(s_ids) > 1:
                    constraints.append(
                        MinimumGapConstraint(
                            id=f"major_gap_{c_id}",
                            name=f"Major Subject Clear Day Gap {c_id}",
                            activity_ids=s_ids,
                            min_gap_minutes=min_clear_minutes,
                        )
                    )

        # 4. Solver Profile
        profile = profile_override or SolverProfile(
            profile_key="education-academic-profile",
            name="Academic Timetabling Profile",
            horizon_start=dataset.horizon_start,
            horizon_end=dataset.horizon_end,
            time_granularity_minutes=15,
            max_solve_seconds=10.0,
            random_seed=42,
            num_search_workers=1,
            soft_weights=SoftObjectiveWeights(
                preference_weight=2,
                idle_gap_weight=0,
                fairness_weight=0,
                utilization_weight=1,
                movement_weight=10 if (is_repair or dataset.published_baseline) else 0,
            ),
        )

        return SchedulingRequest(
            tenant_id=dataset.tenant_id,
            schedule_key=f"sched-{dataset.dataset_id.lower()}",
            horizon_start=dataset.horizon_start,
            horizon_end=dataset.horizon_end,
            timezone=dataset.timezone,
            activities=activities,
            resources=resources,
            constraints=constraints,
            profile=profile,
        )

    def solve(
        self,
        dataset: EducationBenchmarkDataset,
        raw_resources: list[SchedulingResourceSnapshot],
        disruptions: list[dict[str, Any]] | None = None,
        profile_override: SolverProfile | None = None,
        is_repair: bool = False,
    ) -> tuple[SolverResult, list[CanonicalEducationAssignment]]:
        """Compile and solve the education benchmark dataset."""
        req = self.compile_request(
            dataset=dataset,
            raw_resources=raw_resources,
            disruptions=disruptions,
            profile_override=profile_override,
            is_repair=is_repair,
        )

        result = self.scheduler.solve(req)
        if result.status != ScheduleStatus.FEASIBLE:
            return result, []

        # Map candidate assignments back to canonical assignments
        by_act: dict[str, list[CandidateAssignment]] = {}
        for a in result.assignments:
            by_act.setdefault(a.activity_id, []).append(a)

        resources_by_id = {r.id: r for r in raw_resources}
        canon_asgns: list[CanonicalEducationAssignment] = []

        for aid, asgns in by_act.items():
            room_id: str | None = None
            inv_id: str | None = None
            for a in asgns:
                res_obj = resources_by_id.get(a.resource_id)
                if res_obj:
                    if res_obj.type == ResourceType.ROOM:
                        room_id = res_obj.id
                    elif res_obj.type == ResourceType.PERSON:
                        inv_id = res_obj.id

            if not room_id or not inv_id:
                continue

            canon_asgns.append(
                CanonicalEducationAssignment(
                    assignment_id=f"asgn-{aid}",
                    session_id=aid,
                    room_resource_id=room_id,
                    invigilator_resource_id=inv_id,
                    start_at=asgns[0].start_at,
                    end_at=asgns[0].end_at,
                    locked=asgns[0].is_locked,
                )
            )

        return result, sorted(canon_asgns, key=lambda a: (a.start_at, a.session_id))
