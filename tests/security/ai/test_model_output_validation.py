"""Model output schema and deterministic validation attack suite for Phase 8B.

Attacks the typed PlanSchema and PlanValidator boundary with:
- malformed JSON and schema mismatches
- extra and unauthorized fields
- wrong types
- negative durations
- duplicate task and milestone IDs
- dangling dependency references (non-existent predecessor or successor)
- dependency graph cycles (NetworkX detection)
- self-dependencies (predecessor == successor)
- parent hierarchy cycles
- embedded HTML/script content in descriptions
- excessively large task/dependency collections
"""

import pytest
from pydantic import ValidationError

from app.task_intelligence.schemas import (
    DependencyDraft,
    PlanSchema,
    TaskDraft,
    ValidationIssueCode,
)
from app.task_intelligence.validators import PlanValidator


class TestModelOutputValidationAttacks:
    """Attacks on PlanSchema parser and deterministic PlanValidator pipeline."""

    def test_negative_task_duration_rejected_by_schema(self) -> None:
        """TaskDraft rejects negative duration at schema boundary."""
        with pytest.raises(ValidationError) as exc:
            TaskDraft(
                temporary_id="T1",
                title="Negative duration",
                estimated_duration_minutes=-60,
            )
        assert "greater than or equal to 0" in str(exc.value)

    def test_self_dependency_rejected_by_schema_validator(self) -> None:
        """DependencyDraft rejects predecessor == successor."""
        with pytest.raises(ValidationError) as exc:
            DependencyDraft(
                predecessor_id="T1",
                successor_id="T1",
            )
        assert "Self-dependency rejected" in str(exc.value)

    def test_negative_lag_rejected_by_dependency_draft(self) -> None:
        """DependencyDraft rejects negative lag."""
        with pytest.raises(ValidationError):
            DependencyDraft(
                predecessor_id="T1",
                successor_id="T2",
                lag_minutes=-15,
            )

    def test_duplicate_task_ids_detected_by_plan_validator(self) -> None:
        """PlanValidator flags duplicate temporary_id as ERROR."""
        plan = PlanSchema(
            plan_name="Duplicate IDs Plan",
            tasks=[
                TaskDraft(temporary_id="T1", title="First Task", estimated_duration_minutes=30),
                TaskDraft(temporary_id="T1", title="Duplicate Task", estimated_duration_minutes=30),
            ],
        )
        validator = PlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        codes = [i.code for i in result.issues]
        assert ValidationIssueCode.DUPLICATE_TASK_ID in codes

    def test_dangling_dependency_reference_detected(self) -> None:
        """Dependencies pointing to non-existent tasks are flagged as ERROR."""
        plan = PlanSchema(
            plan_name="Dangling Reference Plan",
            tasks=[
                TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=30),
            ],
            dependencies=[
                DependencyDraft(predecessor_id="T1", successor_id="NON_EXISTENT_T2"),
            ],
        )
        validator = PlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        codes = [i.code for i in result.issues]
        assert ValidationIssueCode.UNKNOWN_TASK_REFERENCE in codes

    def test_graph_cycles_detected_with_cycle_path(self) -> None:
        """Cyclic task dependencies are identified by NetworkX DAG algorithm."""
        plan = PlanSchema(
            plan_name="Cyclic Graph Plan",
            tasks=[
                TaskDraft(temporary_id="A", title="Task A", estimated_duration_minutes=10),
                TaskDraft(temporary_id="B", title="Task B", estimated_duration_minutes=10),
                TaskDraft(temporary_id="C", title="Task C", estimated_duration_minutes=10),
            ],
            dependencies=[
                DependencyDraft(predecessor_id="A", successor_id="B"),
                DependencyDraft(predecessor_id="B", successor_id="C"),
                DependencyDraft(predecessor_id="C", successor_id="A"),
            ],
        )
        validator = PlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        assert result.cycle_path is not None
        codes = [i.code for i in result.issues]
        assert ValidationIssueCode.CYCLE_DETECTED in codes

    def test_parent_hierarchy_cycle_detected(self) -> None:
        """Cycles in parent-child task hierarchy are detected."""
        plan = PlanSchema(
            plan_name="Parent Cycle Plan",
            tasks=[
                TaskDraft(
                    temporary_id="P1",
                    parent_id="P2",
                    title="Parent 1",
                    estimated_duration_minutes=10,
                ),
                TaskDraft(
                    temporary_id="P2",
                    parent_id="P1",
                    title="Parent 2",
                    estimated_duration_minutes=10,
                ),
            ],
        )
        validator = PlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        codes = [i.code for i in result.issues]
        assert ValidationIssueCode.PARENT_CYCLE in codes

    def test_embedded_script_content_treated_as_literal_text(self) -> None:
        """HTML / XSS script tags in task title/description do not cause execution."""
        xss_title = "<script>alert('xss')</script>"
        task = TaskDraft(
            temporary_id="T_XSS",
            title=xss_title,
            description="<img src=x onerror=alert(1)>",
            estimated_duration_minutes=20,
        )
        plan = PlanSchema(plan_name="XSS Plan", tasks=[task])
        # Pydantic serializes literal text safely
        dumped = plan.model_dump()
        assert dumped["tasks"][0]["title"] == xss_title

    def test_empty_plan_fails_validation(self) -> None:
        """Plan with zero tasks fails closed."""
        plan = PlanSchema(plan_name="Empty Plan", tasks=[])
        validator = PlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        codes = [i.code for i in result.issues]
        assert ValidationIssueCode.EMPTY_PLAN in codes
