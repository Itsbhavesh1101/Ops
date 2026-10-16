"""Deterministic Phase-6 Planner Fixture for Grade 10 Semester Examination Operations.

Adheres strictly to Phase-6 Stage C requirements:
- Implements PlannerProtocol.
- Produces a valid, typed PlanSchema for Grade 10 semester exams.
- Strictly isolated to Phase-6 vertical-slice demo/testing without resolving
  Phase-2 provider choice.
"""

from typing import Any

from app.task_intelligence.planner.protocol import PlannerProtocol
from app.task_intelligence.schemas import (
    DependencyDraft,
    DependencyType,
    GoalCreate,
    MilestoneDraft,
    PlanSchema,
    TaskDraft,
    TaskPriority,
)


class SemesterExamPlanner(PlannerProtocol):
    """Deterministic planner generating Grade 10 semester examination PlanSchema."""

    async def generate_plan(
        self,
        goal: GoalCreate,
        context: dict[str, Any] | None = None,
    ) -> PlanSchema:
        """Generate structured PlanSchema for Grade 10 semester examination operations."""
        tasks: list[TaskDraft] = [
            TaskDraft(
                temporary_id="T1_EXAM_PREP",
                title="Exam syllabus and cohort enrollment finalization",
                description="Verify enrolled cohorts (Grade 10A, Grade 10B) and subjects.",
                work_category="PREPARATION",
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=120,
                milestone_reference="M1_READINESS",
                acceptance_criteria=[
                    "Cohort rolls verified",
                    "Subject syllabi frozen",
                ],
            ),
            TaskDraft(
                temporary_id="T2_QP_READINESS",
                title="Question paper preparation and moderation",
                description="Draft and moderate question papers for 5 examination subjects.",
                work_category="READINESS",
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=180,
                milestone_reference="M1_READINESS",
                acceptance_criteria=[
                    "5 subject papers drafted",
                    "Security moderation complete",
                ],
            ),
            TaskDraft(
                temporary_id="T3_DEPT_APPROVAL",
                title="Department parallel reviews and approvals",
                description="Obtain parallel approvals from Science and Humanities chairs.",
                work_category="APPROVAL",
                priority=TaskPriority.CRITICAL,
                estimated_duration_minutes=60,
                milestone_reference="M1_READINESS",
                acceptance_criteria=[
                    "Science chair sign-off",
                    "Humanities chair sign-off",
                ],
            ),
            TaskDraft(
                temporary_id="T4_RESOURCE_READINESS",
                title="Room, invigilator & equipment readiness verification",
                description=(
                    "Verify availability of 3 exam rooms, 4 invigilators, and mobile lab kit."
                ),
                work_category="RESOURCES",
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=60,
                milestone_reference="M1_READINESS",
                acceptance_criteria=[
                    "Room seating capacity confirmed",
                    "Proctor assignments eligible",
                    "LAB_KIT equipment verified",
                ],
            ),
            TaskDraft(
                temporary_id="T5_SCHEDULE_GEN",
                title="Exam timetable generation (OR-Tools native)",
                description="Compile academic constraints and solve 10-session timetable.",
                work_category="SCHEDULING",
                priority=TaskPriority.CRITICAL,
                estimated_duration_minutes=60,
                milestone_reference="M2_SCHEDULE",
                acceptance_criteria=[
                    "0 hard constraint violations",
                    "100% equipment satisfaction",
                ],
            ),
            TaskDraft(
                temporary_id="T6_SCHEDULE_REVIEW",
                title="Timetable review and authorized publication",
                description="Authorized scheduler review and formal schedule publication.",
                work_category="PUBLISH",
                priority=TaskPriority.CRITICAL,
                estimated_duration_minutes=30,
                milestone_reference="M2_SCHEDULE",
                acceptance_criteria=[
                    "Review checklist verified",
                    "Schedule state transition to PUBLISHED",
                ],
            ),
            TaskDraft(
                temporary_id="T7_NOTIFICATIONS",
                title="Publish notifications to students and invigilators",
                description="Emit canonical notification commands for published schedule.",
                work_category="ENGAGEMENT",
                priority=TaskPriority.MEDIUM,
                estimated_duration_minutes=30,
                milestone_reference="M2_SCHEDULE",
                acceptance_criteria=[
                    "Calendar synchronization complete",
                    "Notification commands dispatched",
                ],
            ),
            TaskDraft(
                temporary_id="T8_EXAM_EXECUTION",
                title="Grade 10 examination execution window",
                description="Conduct 10 scheduled examination sessions across the 5-day horizon.",
                work_category="EXECUTION",
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=1200,
                milestone_reference="M3_COMPLETION",
                acceptance_criteria=[
                    "All sessions administered",
                    "Attendance logs captured",
                ],
            ),
            TaskDraft(
                temporary_id="T9_DISRUPTION_REPAIR",
                title="Incident handling & timetable disruption repair",
                description=(
                    "Manage resource unavailability events with targeted minimal-churn repair."
                ),
                work_category="REPAIR",
                priority=TaskPriority.HIGH,
                estimated_duration_minutes=60,
                milestone_reference="M3_COMPLETION",
                acceptance_criteria=[
                    "Disrupted session reallocated",
                    "Zero locked assignments moved",
                ],
            ),
            TaskDraft(
                temporary_id="T10_CLOSURE",
                title="Post-exam reconciliation and final audit closure",
                description="Compile final examination audit record and conclude operations.",
                work_category="CLOSURE",
                priority=TaskPriority.MEDIUM,
                estimated_duration_minutes=120,
                milestone_reference="M3_COMPLETION",
                acceptance_criteria=[
                    "Correlated audit verified",
                    "Operational goal marked complete",
                ],
            ),
        ]

        dependencies: list[DependencyDraft] = [
            DependencyDraft(
                predecessor_id="T1_EXAM_PREP",
                successor_id="T2_QP_READINESS",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T2_QP_READINESS",
                successor_id="T3_DEPT_APPROVAL",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T3_DEPT_APPROVAL",
                successor_id="T4_RESOURCE_READINESS",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T4_RESOURCE_READINESS",
                successor_id="T5_SCHEDULE_GEN",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T5_SCHEDULE_GEN",
                successor_id="T6_SCHEDULE_REVIEW",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T6_SCHEDULE_REVIEW",
                successor_id="T7_NOTIFICATIONS",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T7_NOTIFICATIONS",
                successor_id="T8_EXAM_EXECUTION",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T8_EXAM_EXECUTION",
                successor_id="T9_DISRUPTION_REPAIR",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
            DependencyDraft(
                predecessor_id="T9_DISRUPTION_REPAIR",
                successor_id="T10_CLOSURE",
                dependency_type=DependencyType.FINISH_TO_START,
            ),
        ]

        milestones: list[MilestoneDraft] = [
            MilestoneDraft(
                temporary_id="M1_READINESS",
                name="Exam Readiness & Department Sign-off",
                description="Syllabi, papers, approvals, and resource readiness completed.",
                associated_task_ids=[
                    "T1_EXAM_PREP",
                    "T2_QP_READINESS",
                    "T3_DEPT_APPROVAL",
                    "T4_RESOURCE_READINESS",
                ],
            ),
            MilestoneDraft(
                temporary_id="M2_SCHEDULE",
                name="Published Schedule & Notifications Emitted",
                description="Timetable solved, reviewed, published, and notifications emitted.",
                associated_task_ids=[
                    "T5_SCHEDULE_GEN",
                    "T6_SCHEDULE_REVIEW",
                    "T7_NOTIFICATIONS",
                ],
            ),
            MilestoneDraft(
                temporary_id="M3_COMPLETION",
                name="Semester Exam Completion & Audit Closure",
                description=(
                    "Execution, disruption handling, and post-exam reconciliation completed."
                ),
                associated_task_ids=[
                    "T8_EXAM_EXECUTION",
                    "T9_DISRUPTION_REPAIR",
                    "T10_CLOSURE",
                ],
            ),
        ]

        return PlanSchema(
            plan_name=f"Operational Plan: {goal.objective}",
            tasks=tasks,
            dependencies=dependencies,
            milestones=milestones,
            metadata={
                "planner": "SemesterExamPlanner",
                "phase": "PHASE_6_VERTICAL_SLICE",
                "reference_vertical": "SEMESTER_EXAMINATION_OPERATIONS",
                "target_grade": 10,
                "workflow_key": "exam-operations-v1",
            },
        )


__all__ = ["SemesterExamPlanner"]
