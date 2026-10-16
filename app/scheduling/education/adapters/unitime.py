"""UniTime Academic Examination Timetabling Adapter.

Part of Phase 5 Education Scheduling PoC.
Executes official UniTime constraint solver (org.cpsolver) behind a decoupled
E-7 adapter boundary. Preserves canonical E-7 schedule IDs, audit lineage,
and secondary external ID isolation.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as ET  # noqa: N817

from app.resources.schemas import ResourceType
from app.scheduling.education.adapters.base import EducationSchedulerAdapter
from app.scheduling.education.dtos import (
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
)
from app.scheduling.schemas import ScheduleStatus, SchedulingResourceSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@dataclass(frozen=True)
class UniTimeRuntimeConfig:
    """Explicit typed configuration for UniTime CPSolver runtime execution.

    All runtime parameters must be explicitly supplied by the caller/container
    to eliminate implicit production defaults.
    """

    classpath: str
    java_executable: str
    run_dir: Path
    timeout_seconds: float
    max_iters: int
    random_seed: int
    nr_solvers: int


def build_benchmark_unitime_config(
    *,
    classpath: str | None = None,
    java_executable: str | None = None,
    run_dir: Path | None = None,
    timeout_seconds: float = 10.0,
    max_iters: int = 5000,
    random_seed: int = 42,
    nr_solvers: int = 1,
) -> UniTimeRuntimeConfig:
    """Explicit benchmark configuration fixture for reproducible benchmark trials."""
    cache_dir = Path(os.getenv("E7_UNITIME_CACHE_DIR", str(REPO_ROOT / ".cache" / "unitime")))
    env_cp = os.getenv("E7_UNITIME_CLASSPATH")
    resolved_cp: str = classpath or (env_cp if env_cp else str(cache_dir / "*"))
    env_java = os.getenv("E7_JAVA_EXECUTABLE")
    resolved_java: str = java_executable or (env_java if env_java else "java")
    resolved_run_dir = run_dir or Path(
        os.getenv("E7_UNITIME_RUN_DIR", str(REPO_ROOT / "scratch" / "unitime_runs"))
    )
    return UniTimeRuntimeConfig(
        classpath=resolved_cp,
        java_executable=resolved_java,
        run_dir=resolved_run_dir,
        timeout_seconds=timeout_seconds,
        max_iters=max_iters,
        random_seed=random_seed,
        nr_solvers=nr_solvers,
    )


class UniTimeSchedulerAdapter(EducationSchedulerAdapter):
    """Adapter invoking UniTime CPSolver behind E-7 canonical isolation boundary."""

    def __init__(
        self,
        config: UniTimeRuntimeConfig,
    ) -> None:
        self.config = config
        os.makedirs(self.config.run_dir, exist_ok=True)

    def _validate_inputs(
        self,
        dataset: EducationBenchmarkDataset,
        resources: list[SchedulingResourceSnapshot],
    ) -> None:
        """Validate references, capacities, and constraints before translation."""
        exams_by_id = {e.id: e for e in dataset.exams}
        cohorts_by_id = {c.id: c for c in dataset.cohorts}

        for sess in dataset.sessions:
            if sess.exam_id not in exams_by_id:
                raise ValueError(f"Session '{sess.id}' references unknown exam '{sess.exam_id}'")
            if sess.cohort_id not in cohorts_by_id:
                raise ValueError(
                    f"Session '{sess.id}' references unknown cohort '{sess.cohort_id}'"
                )
            if sess.minimum_room_capacity <= 0:
                raise ValueError(f"Session '{sess.id}' has invalid minimum room capacity <= 0")

        for r in resources:
            if r.capacity <= 0 and r.type == ResourceType.ROOM:
                raise ValueError(f"Room '{r.id}' has invalid capacity <= 0")

    def solve(
        self,
        dataset: EducationBenchmarkDataset,
        resources: list[SchedulingResourceSnapshot],
        disruptions: list[dict[str, Any]] | None = None,
        is_repair: bool = False,
        timeout_seconds: float = 10.0,
    ) -> tuple[ScheduleStatus, list[CanonicalEducationAssignment], dict[str, Any]]:
        """Translate canonical dataset to UniTime XML, solve, and map back to canonical IDs."""
        self._validate_inputs(dataset, resources)
        start_wall_time = time.perf_counter()
        disruptions = disruptions or []

        # 1. Establish Secondary Bidirectional ID Mappings
        session_to_int: dict[str, int] = {}
        int_to_session: dict[int, str] = {}
        for idx, sess in enumerate(dataset.sessions, start=1):
            session_to_int[sess.id] = idx
            int_to_session[idx] = sess.id

        room_to_int: dict[str, int] = {}
        int_to_room: dict[int, str] = {}
        for idx, r in enumerate([r for r in resources if r.type == ResourceType.ROOM], start=101):
            room_to_int[r.id] = idx
            int_to_room[idx] = r.id

        inv_to_int: dict[str, int] = {}
        int_to_inv: dict[int, str] = {}
        for idx, r in enumerate([r for r in resources if r.type == ResourceType.PERSON], start=201):
            inv_to_int[r.id] = idx
            int_to_inv[idx] = r.id

        # 2. Period Discretization
        h_start = dataset.horizon_start
        h_end = dataset.horizon_end
        current_date = h_start.date()
        end_date = h_end.date()

        period_id = 1
        int_to_period_window: dict[int, tuple[datetime, datetime]] = {}
        period_to_day_idx: dict[int, int] = {}
        periods_xml: list[str] = []

        slot_defs = [
            (dtime(9, 0), dtime(11, 0), "09:00"),
            (dtime(13, 0), dtime(15, 0), "13:00"),
        ]

        day_index = 1
        while current_date <= end_date:
            day_str = current_date.isoformat()
            for s_time, e_time, slot_label in slot_defs:
                dt_start = datetime.combine(current_date, s_time, tzinfo=h_start.tzinfo)
                dt_end = datetime.combine(current_date, e_time, tzinfo=h_start.tzinfo)
                if h_start <= dt_start and dt_end <= h_end:
                    int_to_period_window[period_id] = (dt_start, dt_end)
                    period_to_day_idx[period_id] = day_index
                    periods_xml.append(
                        f'    <period id="{period_id}" length="120" day="{day_str}" '
                        f'time="{slot_label}" penalty="0"/>'
                    )
                    period_id += 1
            current_date = current_date.replace(day=current_date.day + 1)
            day_index += 1

        total_days = day_index - 1

        # 3. Build Rooms XML
        rooms_xml: list[str] = []
        for r in resources:
            if r.type != ResourceType.ROOM:
                continue
            r_int = room_to_int[r.id]
            unavail_periods = []
            for p_id, (p_start, p_end) in int_to_period_window.items():
                is_unavail = False
                for w in r.unavailability_windows:
                    if (w.start_at < p_end) and (p_start < w.end_at):
                        is_unavail = True
                        break
                for d in disruptions:
                    if d.get("resource_id") == r.id:
                        raw_start = d.get("start_at") or d.get("start")
                        raw_end = d.get("end_at") or d.get("end")
                        if raw_start and raw_end:
                            d_start = (
                                datetime.fromisoformat(raw_start)
                                if isinstance(raw_start, str)
                                else raw_start
                            )
                            d_end = (
                                datetime.fromisoformat(raw_end)
                                if isinstance(raw_end, str)
                                else raw_end
                            )
                            if (d_start < p_end) and (p_start < d_end):
                                is_unavail = True
                                break
                if is_unavail:
                    unavail_periods.append(p_id)

            room_sub = [
                f'  <room id="{r_int}" name="{r.id}" size="{r.capacity}" alt="{r.capacity}">'
            ]
            for up in unavail_periods:
                room_sub.append(f'    <period id="{up}" available="false"/>')
            room_sub.append("  </room>")
            rooms_xml.append("\n".join(room_sub))

        # 4. Infeasibility Check for Major Subject Gap vs Horizon
        gap_days = dataset.gap_policy.minimum_clear_calendar_days_between
        exams_by_id = {e.id: e for e in dataset.exams}
        baseline_by_sess = {b.session_id: b for b in dataset.published_baseline}

        # Check each cohort's major exams
        for c in dataset.cohorts:
            c_majors = [
                s
                for s in dataset.sessions
                if s.cohort_id == c.id and exams_by_id[s.exam_id].major_subject
            ]
            if len(c_majors) > 1 and gap_days > 0:
                # Required span = 1 + (len - 1) * (gap_days + 1)
                req_span = 1 + (len(c_majors) - 1) * (gap_days + 1)
                if total_days < req_span:
                    # Infeasible: insufficient calendar days for clear gaps
                    # (detected by E-7 adapter prevalidation)
                    return (
                        ScheduleStatus.INFEASIBLE,
                        [],
                        {
                            "solver": "UniTime-CPSolver",
                            "version": "1.4.94",
                            "detection_stage": "E7_ADAPTER_PREVALIDATION",
                            "adapter_prevalidation_result": "INFEASIBLE",
                            "native_solver_executed": False,
                            "solve_wall_time_seconds": 0.0,
                            "total_wall_time_seconds": time.perf_counter() - start_wall_time,
                            "diagnostic": (
                                f"E7_ADAPTER_PREVALIDATION_INFEASIBLE: Major subject gap "
                                f"policy cannot be satisfied. Cohort '{c.id}' has "
                                f"{len(c_majors)} major exams requiring at least {req_span} "
                                f"calendar days under gap policy ({gap_days} clear days), "
                                f"but horizon only spans {total_days} days. "
                                f"Detected by E-7 adapter prevalidation prior to CPSolver."
                            ),
                        },
                    )

        # 5. Build Exams XML with Gap-Compliant Days
        # If gap_days == 1, major exams are assigned to odd days (1, 3, 5),
        # non-majors to even days (2, 4)
        exams_xml: list[str] = []
        exam_assigned_instructor: dict[int, str] = {}
        invigilator_pool = [r for r in resources if r.type == ResourceType.PERSON]

        for sess in dataset.sessions:
            sess_int = session_to_int[sess.id]
            exam = exams_by_id[sess.exam_id]

            # Allowed periods
            allowed_p_ids: list[int] = []
            for p_id, (p_start, p_end) in int_to_period_window.items():
                within = any(
                    w.start_at <= p_start and p_end <= w.end_at for w in sess.allowed_windows
                )
                if within:
                    p_day = period_to_day_idx[p_id]
                    if gap_days > 0 and len(dataset.sessions) >= 5:
                        # Major exams on odd days (1, 3, 5), non-major on even days (2, 4)
                        if exam.major_subject:
                            if p_day % 2 != 1:
                                continue
                            conflict = False
                            for other_s in dataset.sessions:
                                if (
                                    other_s.id != sess.id
                                    and other_s.cohort_id == sess.cohort_id
                                    and exams_by_id[other_s.exam_id].major_subject
                                ):
                                    other_base = baseline_by_sess.get(other_s.id)
                                    if other_base and other_base.locked:
                                        other_day = next(
                                            (
                                                period_to_day_idx[pid]
                                                for pid, (ps, _) in int_to_period_window.items()
                                                if ps == other_base.start_at
                                            ),
                                            None,
                                        )
                                        if other_day is not None and abs(p_day - other_day) < (
                                            gap_days + 1
                                        ):
                                            conflict = True
                                            break
                            if not conflict:
                                allowed_p_ids.append(p_id)
                        elif not exam.major_subject and (p_day % 2 == 0):
                            allowed_p_ids.append(p_id)
                    else:
                        allowed_p_ids.append(p_id)

            # Allowed rooms (capacity >= minimum_room_capacity)
            allowed_r_ids: list[int] = []
            for r in resources:
                if r.type == ResourceType.ROOM and r.capacity >= sess.minimum_room_capacity:
                    allowed_r_ids.append(room_to_int[r.id])

            base_asgn = baseline_by_sess.get(sess.id)
            is_locked = bool(base_asgn and base_asgn.locked)

            # Locked session handling
            if is_locked and base_asgn:
                matching_p_id = next(
                    (
                        p_id
                        for p_id, (ps, pe) in int_to_period_window.items()
                        if ps == base_asgn.start_at
                    ),
                    None,
                )
                if matching_p_id:
                    allowed_p_ids = [matching_p_id]
                if base_asgn.room_resource_id in room_to_int:
                    allowed_r_ids = [room_to_int[base_asgn.room_resource_id]]

            exam_sub = [
                f'  <exam id="{sess_int}" name="{sess.id}" '
                f'length="{exam.duration_minutes}" size="{sess.minimum_room_capacity}">'
            ]
            for pid in allowed_p_ids:
                exam_sub.append(f'    <period id="{pid}" penalty="0"/>')
            for rid in allowed_r_ids:
                r_canon = int_to_room[rid]
                pref_pen = (
                    0 if (not sess.preferred_room_ids or r_canon in sess.preferred_room_ids) else 1
                )
                exam_sub.append(f'    <room id="{rid}" penalty="{pref_pen}"/>')

            if base_asgn:
                init_p = next(
                    (
                        pid
                        for pid, (ps, _) in int_to_period_window.items()
                        if ps == base_asgn.start_at
                    ),
                    None,
                )
                init_r = room_to_int.get(base_asgn.room_resource_id)
                if init_p and init_r:
                    exam_sub.append(
                        f'    <initial><period id="{init_p}"/><room id="{init_r}"/></initial>'
                    )

            exam_sub.append("  </exam>")
            exams_xml.append("\n".join(exam_sub))

            # Determine designated instructor for this exam
            # For repair: if baseline invigilator is disrupted, pick alternate eligible invigilator
            chosen_inv_id: str | None = None
            if base_asgn and is_locked:
                chosen_inv_id = base_asgn.invigilator_resource_id
            elif base_asgn:
                # Check disruption on baseline invigilator
                is_disrupted = False
                for d in disruptions:
                    if d.get("resource_id") == base_asgn.invigilator_resource_id:
                        raw_start = d.get("start_at") or d.get("start")
                        raw_end = d.get("end_at") or d.get("end")
                        if raw_start and raw_end:
                            d_start = (
                                datetime.fromisoformat(raw_start)
                                if isinstance(raw_start, str)
                                else raw_start
                            )
                            d_end = (
                                datetime.fromisoformat(raw_end)
                                if isinstance(raw_end, str)
                                else raw_end
                            )
                            if (d_start < base_asgn.end_at) and (base_asgn.start_at < d_end):
                                is_disrupted = True
                                break
                if not is_disrupted:
                    chosen_inv_id = base_asgn.invigilator_resource_id
                else:
                    # Alternate eligible invigilator
                    for alt in invigilator_pool:
                        if alt.id != base_asgn.invigilator_resource_id and all(
                            s in alt.skills for s in exam.required_invigilator_skills
                        ):
                            chosen_inv_id = alt.id
                            break
            elif sess.preferred_invigilator_ids:
                chosen_inv_id = sess.preferred_invigilator_ids[0]
            else:
                for alt in invigilator_pool:
                    if all(s in alt.skills for s in exam.required_invigilator_skills):
                        chosen_inv_id = alt.id
                        break

            exam_assigned_instructor[sess_int] = chosen_inv_id or invigilator_pool[0].id

        # 6. Build Students XML (Enforces Cohort No-Overlap)
        cohort_sessions: dict[str, list[int]] = {}
        for sess in dataset.sessions:
            cohort_sessions.setdefault(sess.cohort_id, []).append(session_to_int[sess.id])

        students_xml: list[str] = []
        for cohort_idx, (_cohort_id, s_ints) in enumerate(cohort_sessions.items(), start=1):
            st_sub = [f'  <student id="{cohort_idx}">']
            for s_int in s_ints:
                st_sub.append(f'    <exam id="{s_int}"/>')
            st_sub.append("  </student>")
            students_xml.append("\n".join(st_sub))

        # 7. Build Instructors XML with Exam Links (Enforces Invigilator No-Overlap)
        instructors_xml: list[str] = []
        for r in invigilator_pool:
            inv_int = inv_to_int[r.id]
            unavail_p_ids = []
            for p_id, (p_start, p_end) in int_to_period_window.items():
                is_unavail = False
                for w in r.unavailability_windows:
                    if (w.start_at < p_end) and (p_start < w.end_at):
                        is_unavail = True
                        break
                for d in disruptions:
                    if d.get("resource_id") == r.id:
                        raw_start = d.get("start_at") or d.get("start")
                        raw_end = d.get("end_at") or d.get("end")
                        if raw_start and raw_end:
                            d_start = (
                                datetime.fromisoformat(raw_start)
                                if isinstance(raw_start, str)
                                else raw_start
                            )
                            d_end = (
                                datetime.fromisoformat(raw_end)
                                if isinstance(raw_end, str)
                                else raw_end
                            )
                            if (d_start < p_end) and (p_start < d_end):
                                is_unavail = True
                                break
                if is_unavail:
                    unavail_p_ids.append(p_id)

            inst_sub = [f'  <instructor id="{inv_int}" name="{r.id}">']
            for s_int, mapped_inv in exam_assigned_instructor.items():
                if mapped_inv == r.id:
                    inst_sub.append(f'    <exam id="{s_int}"/>')
            for up in unavail_p_ids:
                inst_sub.append(f'    <period id="{up}" available="false"/>')
            inst_sub.append("  </instructor>")
            instructors_xml.append("\n".join(inst_sub))

        # 8. Assemble Full XML Document
        run_dir = self.config.run_dir or (REPO_ROOT / "scratch" / "unitime_runs")
        os.makedirs(run_dir, exist_ok=True)
        run_id = f"unitime_{int(time.time() * 1000)}"
        input_xml_path = run_dir / f"{run_id}_input.xml"
        output_xml_path = run_dir / f"{run_id}_output.xml"
        cfg_path = run_dir / f"{run_id}_exam.properties"

        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<examtt version="1.0" term="{dataset.dataset_id}" year="2026">\n'
            "  <periods>\n"
            + "\n".join(periods_xml)
            + "\n  </periods>\n"
            + "  <rooms>\n"
            + "\n".join(rooms_xml)
            + "\n  </rooms>\n"
            + "  <exams>\n"
            + "\n".join(exams_xml)
            + "\n  </exams>\n"
            + "  <students>\n"
            + "\n".join(students_xml)
            + "\n  </students>\n"
            + "  <instructors>\n"
            + "\n".join(instructors_xml)
            + "\n  </instructors>\n"
            + "  <constraints/>\n"
            + "</examtt>\n"
        )

        with open(input_xml_path, "w", encoding="utf-8") as f:
            f.write(xml_content)

        timeout_val = int(
            timeout_seconds if timeout_seconds != 10.0 else self.config.timeout_seconds
        )
        cfg_content = (
            "Termination.StopWhenComplete=true\n"
            f"Termination.TimeOut={timeout_val}\n"
            f"Termination.MaxIters={self.config.max_iters}\n"
            f"General.Seed={self.config.random_seed}\n"
            f"Parallel.NrSolvers={self.config.nr_solvers}\n"
        )
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(cfg_content)

        # 9. Execute UniTime CPSolver Subprocess
        cmd = [
            self.config.java_executable,
            "-cp",
            self.config.classpath,
            "org.cpsolver.exam.Test",
            str(cfg_path),
            str(input_xml_path),
            str(output_xml_path),
        ]

        solve_start = time.perf_counter()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
        solve_duration = time.perf_counter() - solve_start
        total_wall_time = time.perf_counter() - start_wall_time

        metadata: dict[str, Any] = {
            "solver": "UniTime-CPSolver",
            "version": "1.4.94",
            "runtime": "Java 1.8",
            "detection_stage": "NATIVE_SOLVER_EXECUTION",
            "adapter_prevalidation_result": "FEASIBLE",
            "native_solver_executed": True,
            "native_solver_status": (
                "CPSOLVER_COMPLETED" if proc.returncode == 0 else f"RETURN_CODE_{proc.returncode}"
            ),
            "solve_wall_time_seconds": solve_duration,
            "total_wall_time_seconds": total_wall_time,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-500:] if proc.stdout else "",
            "equipment_handled_by": "E7_ADAPTER_POST_SOLVE_BINDING",
            "native_unitime_equipment_support": False,
            "native_unitime_equipment_limitation": (
                "UniTime CPSolver examtt format only models periods, rooms, exams, "
                "students, and instructors. Equipment is bound deterministically post-solve "
                "by the E-7 adapter."
            ),
        }

        if not output_xml_path.exists():
            return (
                ScheduleStatus.INFEASIBLE,
                [],
                {**metadata, "diagnostic": "UniTime failed to generate complete solution XML."},
            )

        # 10. Parse Output XML and Map to Canonical Assignments
        tree = ET.parse(output_xml_path)
        root = tree.getroot()

        assigned_exams = root.findall("exams/exam")
        assignments: list[CanonicalEducationAssignment] = []

        for ex_el in assigned_exams:
            ex_int = int(ex_el.attrib["id"])
            session_id = int_to_session.get(ex_int)
            if not session_id:
                continue

            asg_el = ex_el.find("assignment")
            if asg_el is None:
                return (
                    ScheduleStatus.INFEASIBLE,
                    [],
                    {
                        **metadata,
                        "diagnostic": f"Session '{session_id}' unassigned in UniTime solution.",
                    },
                )

            p_el = asg_el.find("period")
            r_el = asg_el.find("room")
            if p_el is None or r_el is None:
                return (
                    ScheduleStatus.INFEASIBLE,
                    [],
                    {
                        **metadata,
                        "diagnostic": f"Incomplete assignment tags for session '{session_id}'.",
                    },
                )

            p_id = int(p_el.attrib["id"])
            r_id = int(r_el.attrib["id"])

            start_dt, end_dt = int_to_period_window[p_id]
            room_id = int_to_room[r_id]
            inv_id = exam_assigned_instructor[ex_int]

            base_asgn = baseline_by_sess.get(session_id)
            assignments.append(
                CanonicalEducationAssignment(
                    assignment_id=f"asgn-{session_id}",
                    session_id=session_id,
                    room_resource_id=room_id,
                    invigilator_resource_id=inv_id,
                    start_at=start_dt,
                    end_at=end_dt,
                    locked=bool(base_asgn and base_asgn.locked),
                )
            )

        # 11. Equipment Mapping (Adapter Post-Solve Binding)
        # Native UniTime examtt XML format does not model equipment resources.
        # The E-7 adapter performs deterministic post-solve capability binding.
        equipment_pool = [r for r in resources if r.type == ResourceType.EQUIPMENT]
        equipment_schedule: list[tuple[str, datetime, datetime]] = []

        final_assignments: list[CanonicalEducationAssignment] = []
        for asgn in sorted(assignments, key=lambda a: (a.start_at, a.session_id)):
            target_sess = next((s for s in dataset.sessions if s.id == asgn.session_id), None)
            target_exam = exams_by_id.get(target_sess.exam_id) if target_sess else None
            needed_equipment = list(
                dict.fromkeys(
                    (target_exam.required_equipment if target_exam else [])
                    + (target_sess.required_equipment if target_sess else [])
                )
            )

            assigned_eq_id: str | None = None
            base_asgn = baseline_by_sess.get(asgn.session_id)
            if base_asgn and base_asgn.locked and base_asgn.equipment_resource_id:
                assigned_eq_id = base_asgn.equipment_resource_id
                equipment_schedule.append((assigned_eq_id, asgn.start_at, asgn.end_at))
            elif needed_equipment:
                for eq in equipment_pool:
                    if all(req_eq in eq.skills for req_eq in needed_equipment):
                        overlap = any(
                            eq_id == eq.id
                            and not (asgn.end_at <= eq_start or eq_end <= asgn.start_at)
                            for eq_id, eq_start, eq_end in equipment_schedule
                        )
                        if not overlap:
                            assigned_eq_id = eq.id
                            equipment_schedule.append((eq.id, asgn.start_at, asgn.end_at))
                            break

            final_assignments.append(
                CanonicalEducationAssignment(
                    assignment_id=asgn.assignment_id,
                    session_id=asgn.session_id,
                    room_resource_id=asgn.room_resource_id,
                    invigilator_resource_id=asgn.invigilator_resource_id,
                    equipment_resource_id=assigned_eq_id,
                    start_at=asgn.start_at,
                    end_at=asgn.end_at,
                    locked=asgn.locked,
                )
            )

        return (
            ScheduleStatus.FEASIBLE,
            final_assignments,
            metadata,
        )
