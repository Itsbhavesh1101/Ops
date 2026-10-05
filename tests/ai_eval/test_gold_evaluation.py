"""Integration tests for AI Gold-Set Evaluation Harness (Phase 2B).

Verifies:
1. Gold-set discovery (exactly 5 fixtures found).
2. Property-based evaluation metrics:
   - Structured output validity
   - Required task coverage (mapped from required work categories)
   - Invalid dependency rate & cycle detection
   - Hallucinated entity rate & resource detection
   - Latency, token usage, and cost tracking (transparent unavailable status)
   - Human correction rate (truthful unavailable status)
   - Fallback behavior (evaluating all 4 fallback states)
3. JSON and Markdown report generation.
"""

import json

import pytest

from app.task_intelligence.planner import PydanticAiPlanner
from tests.ai_eval.deterministic_model import (
    SequenceTestModel,
    create_gold_standard_exam_plan,
    create_test_planner_settings,
)
from tests.ai_eval.evaluator import GoldSetEvaluator


@pytest.mark.asyncio
async def test_gold_set_fixture_count_is_exact() -> None:
    """Verify repository has exactly 5 Phase-0 gold-set cases."""
    dummy_model = SequenceTestModel([])
    settings = create_test_planner_settings()
    planner = PydanticAiPlanner(model=dummy_model, settings=settings)
    evaluator = GoldSetEvaluator(planner=planner)
    fixtures = evaluator.discover_fixtures()
    names = [f.name for f in fixtures]
    assert len(fixtures) == 5, f"Expected 5 gold fixtures, found {len(fixtures)}: {names}"


@pytest.mark.asyncio
async def test_gold_set_evaluation_all_pass_with_compliant_model() -> None:
    """Verify that a compliant model proposal passes all 5 gold-set property evaluations."""
    compliant_plan = create_gold_standard_exam_plan()
    model = SequenceTestModel([compliant_plan.model_dump()])
    settings = create_test_planner_settings()
    planner = PydanticAiPlanner(model=model, settings=settings)
    evaluator = GoldSetEvaluator(planner=planner)

    suite_result = await evaluator.run_evaluation()

    assert suite_result.total_cases == 5
    assert suite_result.passed_cases == 5
    assert suite_result.failed_cases == 0
    assert suite_result.average_task_coverage == 1.0
    assert suite_result.average_category_coverage == 1.0
    assert suite_result.average_invalid_dependency_rate == 0.0
    assert suite_result.average_hallucinated_entity_rate == 0.0
    assert suite_result.total_cycles_detected == 0
    assert suite_result.total_hallucinations == 0
    assert suite_result.human_correction_rate_status == "UNAVAILABLE_NO_HUMAN_REVIEW_LABELS"
    assert suite_result.cost_status == "UNAVAILABLE_FOR_TEST_MODEL"
    assert suite_result.acceptance_threshold_status == "REQUIRES_BUSINESS_OR_ADR_APPROVAL"

    # Verify reports format properly
    md_report = evaluator.generate_markdown_report(suite_result)
    assert "# AI Gold-Set Evaluation Report" in md_report
    assert "TI-GOLD-001" in md_report
    assert "TI-GOLD-005" in md_report
    assert "UNAVAILABLE_NO_HUMAN_REVIEW_LABELS" in md_report

    json_report = evaluator.generate_json_report(suite_result)
    parsed = json.loads(json_report)
    assert parsed["total_cases"] == 5
    assert parsed["passed_cases"] == 5
    assert parsed["human_correction_rate_status"] == "UNAVAILABLE_NO_HUMAN_REVIEW_LABELS"


@pytest.mark.asyncio
async def test_hallucinated_resource_detection() -> None:
    """Verify that an unknown resource reference is flagged as a hallucinated entity."""
    plan_with_hallucination = create_gold_standard_exam_plan()
    # Insert an invented/hallucinated resource reference
    plan_with_hallucination.tasks[0].owner_reference = "res-invented-ghost-999"

    model = SequenceTestModel([plan_with_hallucination.model_dump()])
    settings = create_test_planner_settings()
    planner = PydanticAiPlanner(model=model, settings=settings)
    evaluator = GoldSetEvaluator(planner=planner)

    suite_result = await evaluator.run_evaluation()
    # At least cases with allowed_resource_ids must flag this hallucination
    assert suite_result.total_hallucinations > 0
    assert suite_result.average_hallucinated_entity_rate > 0.0
    failing_cases = [c for c in suite_result.case_results if not c.is_passing]
    assert len(failing_cases) > 0
    assert any(
        "Hallucinated resource reference" in r for c in failing_cases for r in c.failure_reasons
    )


@pytest.mark.asyncio
async def test_fallback_behavior_evaluation_scenarios() -> None:
    """Verify evaluator measures all 4 source-required fallback behavior modes."""
    dummy_model = SequenceTestModel([])
    settings = create_test_planner_settings()
    planner = PydanticAiPlanner(model=dummy_model, settings=settings)
    evaluator = GoldSetEvaluator(planner=planner)

    valid_plan = create_gold_standard_exam_plan()
    outcomes = await evaluator.evaluate_fallback_scenarios(valid_plan)

    assert outcomes["PRIMARY_SUCCESS"] == "PASSED"
    assert outcomes["FALLBACK_SUCCESS"] == "PASSED"
    assert outcomes["PRIMARY_FAILURE_NO_FALLBACK"] == "PASSED"
    assert outcomes["FALLBACK_FAILURE"] == "PASSED"
