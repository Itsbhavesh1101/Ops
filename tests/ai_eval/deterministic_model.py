"""Deterministic test models and gold plan builders for Phase 2B testing.

Enables reproducible, offline evaluation and tests without external API dependencies.
"""

from typing import Any

from pydantic_ai.models.test import TestModel, _WrappedToolOutput

from app.task_intelligence.schemas import (
    DependencyDraft,
    DependencyType,
    EffortEstimateDraft,
    MilestoneDraft,
    PlanSchema,
    TaskDraft,
    TaskPriority,
)


class SequenceTestModel(TestModel):
    """Test model delivering a sequence of predetermined outputs across successive requests."""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        super().__init__()
        self._outputs = outputs
        self._idx = 0

    def _get_output(self, model_request_parameters: Any) -> Any:
        if not self._outputs:
            return super()._get_output(model_request_parameters)

        val = self._outputs[self._idx] if self._idx < len(self._outputs) else self._outputs[-1]
        self._idx += 1

        output_tool = model_request_parameters.output_tools[0]
        if k := output_tool.outer_typed_dict_key:
            return _WrappedToolOutput({k: val})
        return _WrappedToolOutput(val)


def create_gold_standard_exam_plan(
    scenario_id: str = "E7-REF-EXAM-001",
    allowed_resources: list[str] | None = None,
) -> PlanSchema:
    """Build a compliant, gold-standard examination PlanSchema satisfying all property tests."""
    resources = allowed_resources or ["res-inv-001", "res-room-001"]
    primary_inv = resources[0] if resources else "res-inv-001"
    primary_room = resources[-1] if resources else "res-room-001"

    tasks = [
        TaskDraft(
            temporary_id="T1_PLANNING",
            title="Define examination schedule and guidelines",
            description="Establish overall examination operational parameters and dates.",
            work_category="PLANNING",
            estimated_duration_minutes=120,
            effort_estimate=EffortEstimateDraft(value=2.0, confidence=0.9, basis="SOP standard"),
            acceptance_criteria=["Schedule approved by Academic Council"],
            owner_role="Superintendent",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
        TaskDraft(
            temporary_id="T2_QP_DRAFT",
            title="Draft and moderate question papers",
            description="Prepare question papers for all core subjects.",
            work_category="QUESTION_PAPER_READINESS",
            estimated_duration_minutes=180,
            effort_estimate=EffortEstimateDraft(
                value=3.0, confidence=0.9, basis="Curriculum committee"
            ),
            acceptance_criteria=["Papers moderated and sealed"],
            owner_role="Subject Lead",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
        TaskDraft(
            temporary_id="T3_APPROVALS",
            title="Review and approve examination papers and roster",
            description="Formal institutional signoff on question papers and staff roster.",
            work_category="APPROVALS",
            estimated_duration_minutes=90,
            effort_estimate=EffortEstimateDraft(
                value=1.5, confidence=0.85, basis="Administrative approval"
            ),
            acceptance_criteria=["Signed approval matrix on file"],
            owner_role="Principal",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
        TaskDraft(
            temporary_id="T4_ROOM_PREP",
            title="Prepare and inspect examination rooms",
            description="Arrange desks, seating plans, and verification of room facilities.",
            work_category="ROOM_PREPARATION",
            estimated_duration_minutes=120,
            effort_estimate=EffortEstimateDraft(
                value=2.0, confidence=0.8, basis="Room inspection checklist"
            ),
            acceptance_criteria=["Seating chart displayed outside each room"],
            owner_role="Operations Staff",
            owner_reference=primary_room,
            priority=TaskPriority.MEDIUM,
        ),
        TaskDraft(
            temporary_id="T5_INVIGILATOR_PREP",
            title="Brief invigilators and issue examination kits",
            description="Conduct duty briefing and distribute student rosters and stationery.",
            work_category="INVIGILATOR_PREPARATION",
            estimated_duration_minutes=60,
            effort_estimate=EffortEstimateDraft(
                value=1.0, confidence=0.9, basis="Staff attendance log"
            ),
            acceptance_criteria=["All invigilators signed acknowledgment"],
            owner_role="Chief Invigilator",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
        TaskDraft(
            temporary_id="T6_SCHEDULING",
            title="Publish final student examination timetable and seat numbers",
            description="Distribute hall tickets and notify cohorts.",
            work_category="SCHEDULING",
            estimated_duration_minutes=60,
            effort_estimate=EffortEstimateDraft(
                value=1.0, confidence=0.9, basis="Notice board publication"
            ),
            acceptance_criteria=["Hall tickets downloaded by students"],
            owner_role="Exam Cell Coordinator",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
        TaskDraft(
            temporary_id="T7_EXAM_EXECUTION",
            title="Execute semester examination sessions",
            description="Conduct examination under strict invigilation guidelines.",
            work_category="EXAM_EXECUTION",
            estimated_duration_minutes=240,
            effort_estimate=EffortEstimateDraft(
                value=4.0, confidence=0.95, basis="Timetable duration"
            ),
            acceptance_criteria=["Answer booklets sealed and counted"],
            owner_role="Invigilation Team",
            owner_reference=primary_inv,
            priority=TaskPriority.CRITICAL,
        ),
        TaskDraft(
            temporary_id="T8_INCIDENT_HANDLING",
            title="Monitor operations and resolve student or room contingencies",
            description="Manage medical emergencies, student disputes, or facility issues.",
            work_category="INCIDENT_HANDLING",
            estimated_duration_minutes=60,
            effort_estimate=EffortEstimateDraft(
                value=1.0, confidence=0.75, basis="Incident reporting log"
            ),
            acceptance_criteria=["All incident reports documented and filed"],
            owner_role="Superintendent",
            owner_reference=primary_inv,
            priority=TaskPriority.MEDIUM,
        ),
        TaskDraft(
            temporary_id="T9_CLOSURE",
            title="Reconcile answer scripts and archive examination records",
            description="Final tally of used/unused scripts, secure handover to evaluation cell.",
            work_category="CLOSURE",
            estimated_duration_minutes=120,
            effort_estimate=EffortEstimateDraft(
                value=2.0, confidence=0.9, basis="Dispatch manifest"
            ),
            acceptance_criteria=["Dispatch receipt signed by Evaluation Cell Lead"],
            owner_role="Exam Controller",
            owner_reference=primary_inv,
            priority=TaskPriority.HIGH,
        ),
    ]

    dependencies = [
        DependencyDraft(
            predecessor_id="T1_PLANNING",
            successor_id="T2_QP_DRAFT",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T2_QP_DRAFT",
            successor_id="T3_APPROVALS",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T3_APPROVALS",
            successor_id="T6_SCHEDULING",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T6_SCHEDULING",
            successor_id="T4_ROOM_PREP",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T6_SCHEDULING",
            successor_id="T5_INVIGILATOR_PREP",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T4_ROOM_PREP",
            successor_id="T7_EXAM_EXECUTION",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T5_INVIGILATOR_PREP",
            successor_id="T7_EXAM_EXECUTION",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T7_EXAM_EXECUTION",
            successor_id="T8_INCIDENT_HANDLING",
            dependency_type=DependencyType.START_TO_START,
        ),
        DependencyDraft(
            predecessor_id="T7_EXAM_EXECUTION",
            successor_id="T9_CLOSURE",
            dependency_type=DependencyType.FINISH_TO_START,
        ),
    ]

    milestones = [
        MilestoneDraft(
            temporary_id="M1_READINESS",
            name="Readiness Milestone",
            milestone_type="READINESS",
            target_task_ids=["T3_APPROVALS"],
        ),
        MilestoneDraft(
            temporary_id="M2_SCHEDULE_PUB",
            name="Schedule Publication",
            milestone_type="SCHEDULE_PUBLICATION",
            target_task_ids=["T6_SCHEDULING"],
        ),
        MilestoneDraft(
            temporary_id="M3_EXECUTION",
            name="Exam Execution",
            milestone_type="EXAM_EXECUTION",
            target_task_ids=["T7_EXAM_EXECUTION"],
        ),
        MilestoneDraft(
            temporary_id="M4_CLOSURE",
            name="Exam Operations Closure",
            milestone_type="CLOSURE",
            target_task_ids=["T9_CLOSURE"],
        ),
    ]

    return PlanSchema(
        plan_name=f"Standard Semester Examination Operational Plan ({scenario_id})",
        tasks=tasks,
        dependencies=dependencies,
        milestones=milestones,
    )


def create_test_planner_settings(
    provider: str = "test",
    primary_model: str = "test-model",
    fallback_model: str | None = None,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
    max_repair_attempts: int = 2,
    base_url: str | None = None,
) -> Any:
    """Build an explicit PlannerSettings instance for testing."""
    from app.task_intelligence.planner.config import PlannerSettings

    return PlannerSettings(
        provider=provider,
        primary_model=primary_model,
        fallback_model=fallback_model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_repair_attempts=max_repair_attempts,
        base_url=base_url,
    )
