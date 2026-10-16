"""Canonical Independent Education Schedule Evaluator.

Part of Phase 5 Education Scheduling PoC.
Acts as the single authoritative validator for both OR-Tools and UniTime outputs.
Zero self-certification: every solver's output is translated into canonical E-7
assignments and independently verified against all hard and soft requirements.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from app.scheduling.education.dtos import (
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
    EducationSessionDTO,
)
from app.scheduling.schemas import SchedulingResourceSnapshot


@dataclass
class EvaluationResult:
    """Independent evaluation metrics and validation status."""

    is_valid: bool
    hard_violation_count: int
    hard_violations: list[str] = field(default_factory=list)
    soft_metrics: dict[str, Any] = field(default_factory=dict)
    unscheduled_sessions: list[str] = field(default_factory=list)


class EducationScheduleEvaluator:
    """Authoritative canonical evaluator for education schedules."""

    def __init__(
        self,
        dataset: EducationBenchmarkDataset,
        resources: list[SchedulingResourceSnapshot],
    ) -> None:
        self.dataset = dataset
        self.resources_by_id = {r.id: r for r in resources}
        self.sessions_by_id = {s.id: s for s in dataset.sessions}
        self.exams_by_id = {e.id: e for e in dataset.exams}
        self.cohorts_by_id = {c.id: c for c in dataset.cohorts}
        self.baseline_by_session_id = {b.session_id: b for b in dataset.published_baseline}

    def evaluate(
        self,
        assignments: list[CanonicalEducationAssignment],
        is_repair_scenario: bool = False,
    ) -> EvaluationResult:
        """Independently evaluate hard constraint satisfaction and soft objective metrics."""
        hard_violations: list[str] = []
        assigned_by_session: dict[str, CanonicalEducationAssignment] = {}

        # 1. Unscheduled Sessions Check
        assigned_session_ids = {a.session_id for a in assignments}
        unscheduled: list[str] = []
        for s_id in self.sessions_by_id:
            if s_id not in assigned_session_ids:
                unscheduled.append(s_id)
                hard_violations.append(f"UNSCHEDULED_SESSION: Session '{s_id}' was not scheduled.")

        for asgn in assignments:
            if asgn.session_id in assigned_by_session:
                hard_violations.append(
                    f"DUPLICATE_SESSION_ASSIGNMENT: Session '{asgn.session_id}' "
                    "assigned multiple times."
                )
            assigned_by_session[asgn.session_id] = asgn

            session = self.sessions_by_id.get(asgn.session_id)
            if not session:
                hard_violations.append(
                    f"UNKNOWN_SESSION: Assignment references unknown session '{asgn.session_id}'."
                )
                continue

            exam = self.exams_by_id.get(session.exam_id)
            if not exam:
                hard_violations.append(
                    f"UNKNOWN_EXAM: Session '{asgn.session_id}' references unknown exam "
                    f"'{session.exam_id}'."
                )
                continue

            # 2. Duration and Allowed Window Check
            duration_minutes = int((asgn.end_at - asgn.start_at).total_seconds() / 60)
            if duration_minutes != exam.duration_minutes:
                hard_violations.append(
                    f"DURATION_MISMATCH: Session '{asgn.session_id}' duration is "
                    f"{duration_minutes}m, expected {exam.duration_minutes}m."
                )

            within_window = any(
                w.start_at <= asgn.start_at and asgn.end_at <= w.end_at
                for w in session.allowed_windows
            )
            if not within_window:
                hard_violations.append(
                    f"OUTSIDE_ALLOWED_WINDOW: Session '{asgn.session_id}' "
                    f"[{asgn.start_at} - {asgn.end_at}] outside configured windows."
                )

            # 3. Room Capacity and Eligibility Check
            room = self.resources_by_id.get(asgn.room_resource_id)
            if not room:
                hard_violations.append(
                    f"UNKNOWN_ROOM: Session '{asgn.session_id}' assigned to unknown "
                    f"room '{asgn.room_resource_id}'."
                )
            else:
                if room.capacity < session.minimum_room_capacity:
                    hard_violations.append(
                        f"ROOM_CAPACITY_VIOLATION: Room '{room.id}' capacity ({room.capacity}) "
                        f"< required ({session.minimum_room_capacity}) "
                        f"for session '{asgn.session_id}'."
                    )

            # 4. Invigilator Skills and Eligibility Check
            invigilator = self.resources_by_id.get(asgn.invigilator_resource_id)
            if not invigilator:
                hard_violations.append(
                    f"UNKNOWN_INVIGILATOR: Session '{asgn.session_id}' assigned to unknown "
                    f"invigilator '{asgn.invigilator_resource_id}'."
                )
            else:
                for req_skill in exam.required_invigilator_skills:
                    if req_skill not in invigilator.skills:
                        hard_violations.append(
                            f"INVIGILATOR_SKILL_DEFICIT: Invigilator '{invigilator.id}' "
                            f"lacks required skill '{req_skill}' for exam '{exam.id}' "
                            f"(session '{asgn.session_id}')."
                        )

            # 4b. Equipment Availability & Capability Check
            needed_equipment = list(
                dict.fromkeys(exam.required_equipment + session.required_equipment)
            )
            if needed_equipment:
                eq_id = asgn.equipment_resource_id
                if not eq_id:
                    room_has_it = room and all(eq in room.skills for eq in needed_equipment)
                    if not room_has_it:
                        hard_violations.append(
                            f"REQUIRED_EQUIPMENT_MISSING: Session '{asgn.session_id}' requires "
                            f"equipment {needed_equipment} but no valid equipment was assigned."
                        )
                else:
                    eq_res = self.resources_by_id.get(eq_id)
                    if not eq_res:
                        hard_violations.append(
                            f"UNKNOWN_EQUIPMENT: Session '{asgn.session_id}' assigned to unknown "
                            f"equipment '{eq_id}'."
                        )
                    else:
                        for req_eq in needed_equipment:
                            if req_eq not in eq_res.skills:
                                hard_violations.append(
                                    f"EQUIPMENT_CAPABILITY_DEFICIT: Equipment '{eq_res.id}' "
                                    f"lacks capability '{req_eq}' for session '{asgn.session_id}'."
                                )

        # 5. Overlap Checks (Cohort, Room, Invigilator, Equipment)
        for i in range(len(assignments)):
            a1 = assignments[i]
            s1 = self.sessions_by_id.get(a1.session_id)
            if not s1:
                continue
            for j in range(i + 1, len(assignments)):
                a2 = assignments[j]
                s2 = self.sessions_by_id.get(a2.session_id)
                if not s2:
                    continue

                # Overlap test: [start1, end1) intersects [start2, end2)
                overlap = (a1.start_at < a2.end_at) and (a2.start_at < a1.end_at)
                if overlap:
                    # Cohort conflict
                    if s1.cohort_id == s2.cohort_id:
                        hard_violations.append(
                            f"COHORT_DOUBLE_BOOKING: Cohort '{s1.cohort_id}' scheduled "
                            f"simultaneously for '{s1.id}' and '{s2.id}'."
                        )
                    # Room conflict
                    if a1.room_resource_id == a2.room_resource_id:
                        hard_violations.append(
                            f"ROOM_DOUBLE_BOOKING: Room '{a1.room_resource_id}' scheduled "
                            f"simultaneously for '{s1.id}' and '{s2.id}'."
                        )
                    # Invigilator conflict
                    if a1.invigilator_resource_id == a2.invigilator_resource_id:
                        hard_violations.append(
                            f"INVIGILATOR_DOUBLE_BOOKING: Invigilator "
                            f"'{a1.invigilator_resource_id}' scheduled simultaneously for "
                            f"'{s1.id}' and '{s2.id}'."
                        )
                    # Equipment conflict
                    if (
                        a1.equipment_resource_id
                        and a2.equipment_resource_id
                        and a1.equipment_resource_id == a2.equipment_resource_id
                    ):
                        hard_violations.append(
                            f"EQUIPMENT_DOUBLE_BOOKING: Equipment '{a1.equipment_resource_id}' "
                            f"scheduled simultaneously for '{s1.id}' and '{s2.id}'."
                        )

        # 6. Major Subject Spacing / Gap Policy Check
        gap_days = self.dataset.gap_policy.minimum_clear_calendar_days_between
        if gap_days > 0:
            # Group assignments by cohort
            cohort_major_assignments: dict[
                str, list[tuple[CanonicalEducationAssignment, EducationSessionDTO]]
            ] = {}
            for asgn in assignments:
                sess = self.sessions_by_id.get(asgn.session_id)
                if not sess:
                    continue
                ex = self.exams_by_id.get(sess.exam_id)
                if ex and ex.major_subject:
                    cohort_major_assignments.setdefault(sess.cohort_id, []).append((asgn, sess))

            for cohort_id, major_list in cohort_major_assignments.items():
                sorted_majors = sorted(major_list, key=lambda x: x[0].start_at)
                for k in range(len(sorted_majors) - 1):
                    first_asgn, first_sess = sorted_majors[k]
                    second_asgn, second_sess = sorted_majors[k + 1]
                    d1 = first_asgn.start_at.date()
                    d2 = second_asgn.start_at.date()
                    calendar_day_diff = (d2 - d1).days
                    # If gap_days is 1, calendar_day_diff must be >= 2 (1 clear calendar day)
                    required_day_diff = gap_days + 1
                    if calendar_day_diff < required_day_diff:
                        hard_violations.append(
                            f"MAJOR_SUBJECT_GAP_VIOLATION: Cohort '{cohort_id}' major exams "
                            f"'{first_sess.id}' ({d1}) and '{second_sess.id}' ({d2}) are "
                            f"separated by {calendar_day_diff - 1} clear calendar days "
                            f"(required at least {gap_days})."
                        )

        # 7. Lock Invariant Preservation (For Repair Scenarios)
        locked_moved_count = 0
        if is_repair_scenario:
            for locked_id in self.dataset.locked_session_ids:
                base_asgn = self.baseline_by_session_id.get(locked_id)
                new_asgn = assigned_by_session.get(locked_id)
                if base_asgn and new_asgn:
                    mutated = (
                        base_asgn.start_at != new_asgn.start_at
                        or base_asgn.end_at != new_asgn.end_at
                        or base_asgn.room_resource_id != new_asgn.room_resource_id
                        or base_asgn.invigilator_resource_id != new_asgn.invigilator_resource_id
                        or base_asgn.equipment_resource_id != new_asgn.equipment_resource_id
                    )
                    if mutated:
                        locked_moved_count += 1
                        hard_violations.append(
                            f"LOCKED_ASSIGNMENT_MUTATION: Locked assignment for session "
                            f"'{locked_id}' was modified during repair. "
                            f"Baseline: [{base_asgn.start_at}, room={base_asgn.room_resource_id}, "
                            f"inv={base_asgn.invigilator_resource_id}, "
                            f"eq={base_asgn.equipment_resource_id}], "
                            f"Repaired: [{new_asgn.start_at}, room={new_asgn.room_resource_id}, "
                            f"inv={new_asgn.invigilator_resource_id}, "
                            f"eq={new_asgn.equipment_resource_id}]."
                        )

        # 8. Compute Soft Optimization Metrics
        preferred_room_hits = 0
        preferred_invigilator_hits = 0
        for asgn in assignments:
            sess = self.sessions_by_id.get(asgn.session_id)
            if sess:
                if sess.preferred_room_ids:
                    if asgn.room_resource_id in sess.preferred_room_ids:
                        preferred_room_hits += 1
                if sess.preferred_invigilator_ids:
                    if asgn.invigilator_resource_id in sess.preferred_invigilator_ids:
                        preferred_invigilator_hits += 1

        # Invigilator workload fairness
        inv_session_counts: dict[str, int] = {}
        for asgn in assignments:
            inv_session_counts[asgn.invigilator_resource_id] = (
                inv_session_counts.get(asgn.invigilator_resource_id, 0) + 1
            )
        counts = list(inv_session_counts.values()) or [0]
        workload_variance = statistics.variance(counts) if len(counts) > 1 else 0.0

        # Equipment satisfaction rate
        sessions_needing_eq = [
            s
            for s in self.sessions_by_id.values()
            if s.required_equipment
            or (
                self.exams_by_id.get(s.exam_id)
                and self.exams_by_id[s.exam_id].required_equipment
            )
        ]
        satisfied_eq_count = 0
        for s in sessions_needing_eq:
            target_asgn = assigned_by_session.get(s.id)
            if target_asgn:
                needed = list(
                    dict.fromkeys(
                        s.required_equipment
                        + (
                            self.exams_by_id[s.exam_id].required_equipment
                            if s.exam_id in self.exams_by_id
                            else []
                        )
                    )
                )
                if target_asgn.equipment_resource_id:
                    eq_res = self.resources_by_id.get(target_asgn.equipment_resource_id)
                    if eq_res and all(eq in eq_res.skills for eq in needed):
                        satisfied_eq_count += 1
                elif target_asgn.room_resource_id:
                    r_res = self.resources_by_id.get(target_asgn.room_resource_id)
                    if r_res and all(eq in r_res.skills for eq in needed):
                        satisfied_eq_count += 1

        eq_satisfaction_rate = (
            round(satisfied_eq_count / len(sessions_needing_eq), 4)
            if sessions_needing_eq
            else 1.0
        )

        # Repair movement metrics
        repair_moved_count = 0
        repair_time_shift_minutes = 0
        repair_room_changes = 0
        repair_inv_changes = 0
        if is_repair_scenario:
            for s_id, new_asgn in assigned_by_session.items():
                base_asgn = self.baseline_by_session_id.get(s_id)
                if base_asgn:
                    time_diff = abs(
                        int((new_asgn.start_at - base_asgn.start_at).total_seconds() / 60)
                    )
                    room_diff = new_asgn.room_resource_id != base_asgn.room_resource_id
                    inv_diff = new_asgn.invigilator_resource_id != base_asgn.invigilator_resource_id
                    eq_diff = new_asgn.equipment_resource_id != base_asgn.equipment_resource_id
                    if time_diff > 0 or room_diff or inv_diff or eq_diff:
                        repair_moved_count += 1
                        repair_time_shift_minutes += time_diff
                        if room_diff:
                            repair_room_changes += 1
                        if inv_diff:
                            repair_inv_changes += 1

        soft_metrics = {
            "preferred_room_satisfaction_rate": (
                round(preferred_room_hits / len(assignments), 4) if assignments else 0.0
            ),
            "preferred_invigilator_satisfaction_rate": (
                round(preferred_invigilator_hits / len(assignments), 4) if assignments else 0.0
            ),
            "equipment_satisfaction_rate": eq_satisfaction_rate,
            "invigilator_workload_variance": round(workload_variance, 4),
            "repair_moved_assignments_count": repair_moved_count,
            "repair_time_shift_minutes": repair_time_shift_minutes,
            "repair_room_reassignments": repair_room_changes,
            "repair_invigilator_reassignments": repair_inv_changes,
            "locked_assignments_moved": locked_moved_count,
        }

        is_valid = len(hard_violations) == 0 and len(unscheduled) == 0

        return EvaluationResult(
            is_valid=is_valid,
            hard_violation_count=len(hard_violations),
            hard_violations=hard_violations,
            soft_metrics=soft_metrics,
            unscheduled_sessions=unscheduled,
        )
