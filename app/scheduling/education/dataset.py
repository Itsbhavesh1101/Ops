"""Canonical Education Benchmark Dataset Loader and Manifest.

Part of Phase 5 Education Scheduling PoC.
Composes the approved Phase-0 education fixtures and resource catalog
into a single, normalized, deterministic benchmark package.
Guarantees identical canonical inputs across all evaluated solvers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.resources.schemas import ResourceType
from app.scheduling.education.dtos import (
    AcademicSubjectDTO,
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
    EducationSessionDTO,
    ExamGapPolicy,
    GapScope,
    SoftPreferenceSpec,
    StudentCohortDTO,
    TimeWindowDTO,
)
from app.scheduling.schemas import SchedulingResourceSnapshot

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PHASE5_FIXTURES_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "scheduling" / "education" / "phase5"
)
RESOURCES_FILE = PHASE5_FIXTURES_DIR / "PHASE5-RS-EDU-001.json"
EXAM_FEASIBLE_FILE = PHASE5_FIXTURES_DIR / "PHASE5-SC-EXAM-FEASIBLE-001.json"
EXAM_INFEASIBLE_FILE = PHASE5_FIXTURES_DIR / "PHASE5-SC-EXAM-INFEASIBLE-001.json"
EXAM_REPAIR_FILE = PHASE5_FIXTURES_DIR / "PHASE5-SC-EXAM-REPAIR-001.json"


def compute_file_sha256(path: Path) -> str:
    """Compute deterministic SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class EducationBenchmarkManifest:
    """Deterministic manifest metadata for the Phase 5 benchmark package."""

    manifest_id: str
    manifest_version: str
    dataset_hashes: dict[str, str]
    combined_canonical_benchmark_hash: str
    entity_counts: dict[str, int]
    constraint_counts: dict[str, int]
    time_horizon_start: str
    time_horizon_end: str
    timezone: str
    hard_policies: list[str]
    soft_policies: list[dict[str, Any]]
    disruption_cases: list[str]


def get_canonical_resources() -> list[SchedulingResourceSnapshot]:
    """Extract relevant educational room and invigilator resources from RS-SYNTH-001."""
    data = _load_json(RESOURCES_FILE)
    education_resource_ids = {
        "res-room-001",
        "res-room-002",
        "res-room-003",
        "res-inv-001",
        "res-inv-002",
        "res-inv-003",
        "res-inv-004",
        "res-eq-004",
    }
    snapshots: list[SchedulingResourceSnapshot] = []
    for r in data["resources"]:
        if r["id"] in education_resource_ids:
            # Map room attributes (equipment, room_type) into skills for capability matching
            attrs = r.get("attributes", {})
            combined_skills = list(r.get("skills", []))
            if "equipment" in attrs and isinstance(attrs["equipment"], list):
                combined_skills.extend(attrs["equipment"])
            if "room_type" in attrs:
                combined_skills.append(attrs["room_type"])
            if "role" in attrs:
                combined_skills.append(attrs["role"])

            snapshots.append(
                SchedulingResourceSnapshot(
                    id=r["id"],
                    type=ResourceType(r["type"]),
                    display_label=r["display_label"],
                    capacity=r.get("capacity", 1),
                    skills=combined_skills,
                )
            )
    return snapshots


def load_education_dataset(
    fixture_choice: str = "FEASIBLE",
) -> tuple[EducationBenchmarkDataset, list[SchedulingResourceSnapshot]]:
    """Load normalized canonical education dataset for specified scenario.

    Choices: 'FEASIBLE', 'INFEASIBLE', 'REPAIR'.
    """
    if fixture_choice == "FEASIBLE":
        fixture_path = EXAM_FEASIBLE_FILE
    elif fixture_choice == "INFEASIBLE":
        fixture_path = EXAM_INFEASIBLE_FILE
    elif fixture_choice == "REPAIR":
        fixture_path = EXAM_REPAIR_FILE
    else:
        raise ValueError(f"Unknown fixture_choice: {fixture_choice}")

    data = _load_json(fixture_path)
    resources = get_canonical_resources()

    cohorts = [
        StudentCohortDTO(
            id=c["id"],
            display_label=c["display_label"],
            student_count=c["student_count"],
        )
        for c in data["cohorts"]
    ]

    exams = [
        AcademicSubjectDTO(
            id=e["id"],
            title=e["title"],
            duration_minutes=e["duration_minutes"],
            major_subject=e.get("major_subject", False),
            required_invigilator_skills=e.get("required_invigilator_skills", []),
            preferred_invigilator_skills=e.get("preferred_invigilator_skills", []),
            required_equipment=e.get("required_equipment", []),
        )
        for e in data["exams"]
    ]

    sessions = [
        EducationSessionDTO(
            id=s["id"],
            exam_id=s["exam_id"],
            cohort_id=s["cohort_id"],
            minimum_room_capacity=s["minimum_room_capacity"],
            allowed_windows=[
                TimeWindowDTO(
                    start_at=datetime.fromisoformat(w["start_at"]),
                    end_at=datetime.fromisoformat(w["end_at"]),
                )
                for w in s["allowed_windows"]
            ],
            preferred_room_ids=s.get("preferred_room_ids", []),
            preferred_invigilator_ids=s.get("preferred_invigilator_ids", []),
            required_equipment=s.get("required_equipment", []),
        )
        for s in data["exam_sessions"]
    ]

    gap_data = data.get("major_subject_gap_policy", {})
    gap_policy = ExamGapPolicy(
        scope=GapScope(gap_data.get("scope", "PER_COHORT")),
        minimum_clear_calendar_days_between=gap_data.get("minimum_clear_calendar_days_between", 1),
        is_hard=True,
    )

    soft_prefs = [
        SoftPreferenceSpec(kind=sp["kind"], weight=sp.get("weight", 1))
        for sp in data.get("soft_constraint_expectations", [])
    ]

    published_baseline: list[CanonicalEducationAssignment] = []
    locked_ids: list[str] = []
    if "published_baseline" in data:
        for b in data["published_baseline"]:
            is_locked = b.get("locked", False)
            if is_locked:
                locked_ids.append(b["session_id"])
            published_baseline.append(
                CanonicalEducationAssignment(
                    assignment_id=b["assignment_id"],
                    session_id=b["session_id"],
                    room_resource_id=b["room_resource_id"],
                    invigilator_resource_id=b["invigilator_resource_id"],
                    equipment_resource_id=b.get("equipment_resource_id"),
                    start_at=datetime.fromisoformat(b["start_at"]),
                    end_at=datetime.fromisoformat(b["end_at"]),
                    locked=is_locked,
                )
            )

    dataset = EducationBenchmarkDataset(
        dataset_id=data["dataset_id"],
        dataset_version=data["dataset_version"],
        tenant_id=data["tenant_id"],
        timezone=data["timezone"],
        horizon_start=datetime.fromisoformat(data["horizon"]["start_at"]),
        horizon_end=datetime.fromisoformat(data["horizon"]["end_at"]),
        cohorts=cohorts,
        exams=exams,
        sessions=sessions,
        gap_policy=gap_policy,
        soft_preferences=soft_prefs,
        published_baseline=published_baseline,
        locked_session_ids=locked_ids,
    )

    return dataset, resources


def build_benchmark_manifest() -> EducationBenchmarkManifest:
    """Generate deterministic manifest describing the canonical Phase-5 education dataset."""
    hashes = {
        "PHASE5-RS-EDU-001": compute_file_sha256(RESOURCES_FILE),
        "PHASE5-SC-EXAM-FEASIBLE-001": compute_file_sha256(EXAM_FEASIBLE_FILE),
        "PHASE5-SC-EXAM-INFEASIBLE-001": compute_file_sha256(EXAM_INFEASIBLE_FILE),
        "PHASE5-SC-EXAM-REPAIR-001": compute_file_sha256(EXAM_REPAIR_FILE),
    }

    combined_hasher = hashlib.sha256()
    for h in hashes.values():
        combined_hasher.update(h.encode())
    combined_hash = combined_hasher.hexdigest()

    dataset, resources = load_education_dataset("FEASIBLE")

    return EducationBenchmarkManifest(
        manifest_id="E7-PHASE5-EDUCATION-BENCHMARK-MANIFEST",
        manifest_version="2.0.0",
        dataset_hashes=hashes,
        combined_canonical_benchmark_hash=combined_hash,
        entity_counts={
            "cohorts": len(dataset.cohorts),
            "exams": len(dataset.exams),
            "sessions": len(dataset.sessions),
            "rooms": len([r for r in resources if r.type == ResourceType.ROOM]),
            "invigilators": len([r for r in resources if r.type == ResourceType.PERSON]),
            "equipment": len([r for r in resources if r.type == ResourceType.EQUIPMENT]),
        },
        constraint_counts={
            "hard_no_overlap_cohort": len(dataset.cohorts),
            "hard_no_overlap_room": len([r for r in resources if r.type == ResourceType.ROOM]),
            "hard_no_overlap_invigilator": len(
                [r for r in resources if r.type == ResourceType.PERSON]
            ),
            "hard_no_overlap_equipment": len(
                [r for r in resources if r.type == ResourceType.EQUIPMENT]
            ),
            "hard_room_capacity": len(dataset.sessions),
            "hard_major_subject_gaps": len(dataset.cohorts),
            "hard_equipment_requirement": len(
                [s for s in dataset.sessions if s.required_equipment]
            ),
            "soft_preferences": len(dataset.soft_preferences),
        },
        time_horizon_start=dataset.horizon_start.isoformat(),
        time_horizon_end=dataset.horizon_end.isoformat(),
        timezone=dataset.timezone,
        hard_policies=[
            "NO_COHORT_DOUBLE_BOOKING",
            "NO_ROOM_DOUBLE_BOOKING",
            "NO_INVIGILATOR_DOUBLE_BOOKING",
            "NO_EQUIPMENT_DOUBLE_BOOKING",
            "ROOM_CAPACITY",
            "INVIGILATOR_ROLE_ELIGIBILITY",
            "REQUIRED_EQUIPMENT_AVAILABLE",
            "DURATION_WITHIN_WINDOW",
            "MAJOR_SUBJECT_MIN_GAP",
            "LOCK_PRESERVATION",
        ],
        soft_policies=[{"kind": sp.kind, "weight": sp.weight} for sp in dataset.soft_preferences],
        disruption_cases=[
            "FACULTY_UNAVAILABILITY",
            "ROOM_OUTAGE",
            "EXAM_GAP_EDGE",
            "FIXED_EVENT",
            "LOCKED_ASSIGNMENT_MUTATION_FORBIDDEN",
            "PARTIAL_REPAIR",
        ],
    )
