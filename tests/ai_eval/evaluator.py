"""AI Gold-Set Property-Based Evaluation Harness (Phase 2B).

Evaluates AI planner proposals against Phase-0 gold-set fixtures (TI-GOLD-001..005)
reconciled to source-required metrics:
1. structured_output_validity: Clean parsing of PlanSchema
2. required_task_coverage: Evaluates coverage of required work categories
   mapped from fixture expected_plan_properties.required_work_categories
   (at least one actionable task per category, exact wording not scored)
3. invalid_dependency_rate: Ratio of invalid dependencies (cycles, unknown IDs,
   self-references, duplicates) to total dependencies
4. hallucinated_entity_rate: Ratio of unauthorized resource references to total
   referenced external entities
5. human_correction_rate: Status 'UNAVAILABLE_NO_HUMAN_REVIEW_LABELS' (zero fake metrics)
6. latency_ms: Measured execution latency in milliseconds
7. usage: Recorded numeric token usage if reported
8. cost: Status 'UNAVAILABLE_FOR_TEST_MODEL' / value 'unavailable'
9. fallback_behavior: Measured fallback invocation and recovery modes
10. acceptance_threshold: Status 'REQUIRES_BUSINESS_OR_ADR_APPROVAL'
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.models.test import TestModel

from app.task_intelligence.planner import (
    PlannerInferenceError,
    PlannerSettings,
    PydanticAiPlanner,
)
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
    ValidationResult,
)
from app.task_intelligence.validators import PlanValidator

FIXTURES_DIR = Path("F:/operations-engine/tests/fixtures/ai_gold_set")


@dataclass
class GoldCaseEvaluation:
    """Evaluation metrics for a single gold-set test case."""

    dataset_id: str
    scenario_id: str
    structured_output_validity: bool = False
    required_task_coverage: float = 0.0
    task_count: int = 0
    dependency_count: int = 0
    cycle_detected: bool = False
    invalid_dependency_count: int = 0
    invalid_dependency_rate: float = 0.0
    hallucinated_entity_count: int = 0
    hallucinated_entity_rate: float = 0.0
    human_correction_rate_status: str = "UNAVAILABLE_NO_HUMAN_REVIEW_LABELS"
    human_correction_rate: float | None = None
    human_correction_required_flag: bool | None = None
    cost_status: str = "UNAVAILABLE_FOR_TEST_MODEL"
    cost: str = "unavailable"
    fallback_behavior: str = "PRIMARY_SUCCESS"
    fallback_used: bool = False
    repair_attempts: int = 0
    latency_ms: float = 0.0
    total_tokens: int = 0
    acceptance_threshold: str = "REQUIRES_BUSINESS_OR_ADR_APPROVAL"
    is_passing: bool = False
    failure_reasons: list[str] = field(default_factory=list)

    @property
    def required_category_coverage(self) -> float:
        """Backward-compatibility alias for required_task_coverage."""
        return self.required_task_coverage


@dataclass
class EvaluationSuiteResult:
    """Aggregated evaluation results across all evaluated gold-set cases."""

    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    average_task_coverage: float = 0.0
    average_invalid_dependency_rate: float = 0.0
    average_hallucinated_entity_rate: float = 0.0
    total_cycles_detected: int = 0
    total_hallucinations: int = 0
    human_correction_rate_status: str = "UNAVAILABLE_NO_HUMAN_REVIEW_LABELS"
    cost_status: str = "UNAVAILABLE_FOR_TEST_MODEL"
    acceptance_threshold_status: str = "REQUIRES_BUSINESS_OR_ADR_APPROVAL"
    case_results: list[GoldCaseEvaluation] = field(default_factory=list)

    @property
    def average_category_coverage(self) -> float:
        """Backward-compatibility alias for average_task_coverage."""
        return self.average_task_coverage


class GoldSetEvaluator:
    """Harness executing property-based evaluation across gold-set fixtures."""

    def __init__(
        self,
        planner: PydanticAiPlanner,
        fixtures_dir: Path | None = None,
    ) -> None:
        self._planner = planner
        self._fixtures_dir = fixtures_dir or FIXTURES_DIR
        self._validator = PlanValidator()

    def discover_fixtures(self) -> list[Path]:
        """Discover and return all Phase-0 gold-set fixture paths."""
        return sorted(self._fixtures_dir.glob("TI-GOLD-*.json"))

    def evaluate_plan_properties(
        self,
        fixture_data: dict[str, Any],
        plan: PlanSchema,
        validation_res: ValidationResult,
        latency_ms: float = 0.0,
        usage: dict[str, Any] | None = None,
        fallback_used: bool = False,
        repair_attempts: int = 0,
    ) -> GoldCaseEvaluation:
        """Evaluate a generated PlanSchema against expected fixture properties."""
        dataset_id = fixture_data.get("dataset_id", "UNKNOWN")
        scenario_id = fixture_data.get("scenario_id", "UNKNOWN")

        expected_props = fixture_data.get("expected_plan_properties", {})
        authorized_ctx = fixture_data.get("authorized_context", {})
        allowed_resources = set(authorized_ctx.get("allowed_resource_ids", []))
        required_categories = set(expected_props.get("required_work_categories", []))
        eval_expectations = fixture_data.get("evaluation_expectations", {})

        failures: list[str] = []

        # 1. Structured output validity
        structured_valid = bool(plan and plan.tasks)
        if not structured_valid:
            failures.append("Empty or malformed plan output.")

        # 2. Required task coverage (mapped from required work categories per fixture note)
        present_categories = {t.work_category for t in plan.tasks if t.work_category}
        coverage = 1.0
        if required_categories:
            matching = present_categories.intersection(required_categories)
            coverage = len(matching) / len(required_categories)
            if coverage < 1.0:
                missing = sorted(required_categories - present_categories)
                failures.append(f"Missing required work categories: {missing}")

        # 3. Dependency quality and invalid dependency rate
        total_dependencies = len(plan.dependencies)
        cycle_detected = validation_res.cycle_path is not None
        if cycle_detected:
            failures.append(f"Dependency cycle detected: {validation_res.cycle_path}")

        invalid_deps = [
            issue
            for issue in validation_res.issues
            if "DEPENDENCY" in str(issue.code) or "CYCLE" in str(issue.code)
        ]
        invalid_dep_count = len(invalid_deps)
        invalid_dep_rate = (
            round(invalid_dep_count / total_dependencies, 3) if total_dependencies > 0 else 0.0
        )

        # 4. Hallucinated entity/resource detection and rate
        referenced_entity_count = sum(1 for t in plan.tasks if t.owner_reference)
        hallucination_count = 0
        if allowed_resources:
            for task in plan.tasks:
                if task.owner_reference:
                    is_opaque_staff = task.owner_reference.startswith("StaffRef_")
                    if task.owner_reference not in allowed_resources and not is_opaque_staff:
                        hallucination_count += 1
                        failures.append(
                            f"Hallucinated resource reference '{task.owner_reference}' "
                            f"in task '{task.temporary_id}'."
                        )
        hallucinated_rate = (
            round(hallucination_count / referenced_entity_count, 3)
            if referenced_entity_count > 0
            else 0.0
        )

        # 5. Usage and cost
        tok = 0
        cost_str = "unavailable"
        cost_status = "UNAVAILABLE_FOR_TEST_MODEL"
        if usage:
            tok = usage.get("total_tokens", 0)
            c_val = usage.get("cost")
            if c_val is not None and str(c_val) != "unavailable":
                cost_str = str(c_val)
                cost_status = "PROVIDER_REPORTED"

        # 6. Fallback behavior
        fallback_behavior = "FALLBACK_SUCCESS" if fallback_used else "PRIMARY_SUCCESS"

        # 7. Human correction metrics (truthful status)
        human_req_flag = eval_expectations.get("human_correction_required")

        is_pass = len(failures) == 0 and validation_res.is_valid

        return GoldCaseEvaluation(
            dataset_id=dataset_id,
            scenario_id=scenario_id,
            structured_output_validity=structured_valid,
            required_task_coverage=round(coverage, 3),
            task_count=len(plan.tasks),
            dependency_count=total_dependencies,
            cycle_detected=cycle_detected,
            invalid_dependency_count=invalid_dep_count,
            invalid_dependency_rate=invalid_dep_rate,
            hallucinated_entity_count=hallucination_count,
            hallucinated_entity_rate=hallucinated_rate,
            human_correction_rate_status="UNAVAILABLE_NO_HUMAN_REVIEW_LABELS",
            human_correction_rate=None,
            human_correction_required_flag=human_req_flag,
            cost_status=cost_status,
            cost=cost_str,
            fallback_behavior=fallback_behavior,
            fallback_used=fallback_used,
            repair_attempts=repair_attempts,
            latency_ms=round(latency_ms, 2),
            total_tokens=tok,
            acceptance_threshold="REQUIRES_BUSINESS_OR_ADR_APPROVAL",
            is_passing=is_pass,
            failure_reasons=failures,
        )

    async def run_evaluation(self) -> EvaluationSuiteResult:
        """Run complete evaluation against all discovered gold-set fixtures."""
        fixtures = self.discover_fixtures()
        case_results: list[GoldCaseEvaluation] = []

        for fpath in fixtures:
            with open(fpath, encoding="utf-8") as f:
                fdata = json.load(f)

            input_case = fdata.get("input_case", {})
            raw_obj = input_case.get("objective") or input_case.get("raw_goal")
            objective = raw_obj or "Operational goal"
            goal = GoalCreate(
                objective=objective,
                scope=input_case.get("scope"),
                constraints=input_case.get("constraints", []),
                resource_scope=input_case.get("resource_scope", []),
            )

            # Generate via planner
            gen_res = await self._planner.generate_plan_with_result(
                goal=goal,
                context=fdata.get("authorized_context"),
            )

            case_eval = self.evaluate_plan_properties(
                fixture_data=fdata,
                plan=gen_res.plan,
                validation_res=gen_res.validation_result or self._validator.validate(gen_res.plan),
                latency_ms=gen_res.latency_ms,
                usage=gen_res.usage,
                fallback_used=gen_res.fallback_used,
                repair_attempts=gen_res.repair_attempts,
            )
            case_results.append(case_eval)

        passed = sum(1 for c in case_results if c.is_passing)
        total = len(case_results)
        avg_cov = sum(c.required_task_coverage for c in case_results) / total if total > 0 else 0.0
        avg_inv_rate = (
            sum(c.invalid_dependency_rate for c in case_results) / total if total > 0 else 0.0
        )
        avg_halluc_rate = (
            sum(c.hallucinated_entity_rate for c in case_results) / total if total > 0 else 0.0
        )
        total_cycles = sum(1 for c in case_results if c.cycle_detected)
        total_hallucinations = sum(c.hallucinated_entity_count for c in case_results)

        return EvaluationSuiteResult(
            total_cases=total,
            passed_cases=passed,
            failed_cases=total - passed,
            average_task_coverage=round(avg_cov, 3),
            average_invalid_dependency_rate=round(avg_inv_rate, 3),
            average_hallucinated_entity_rate=round(avg_halluc_rate, 3),
            total_cycles_detected=total_cycles,
            total_hallucinations=total_hallucinations,
            human_correction_rate_status="UNAVAILABLE_NO_HUMAN_REVIEW_LABELS",
            cost_status="UNAVAILABLE_FOR_TEST_MODEL",
            acceptance_threshold_status="REQUIRES_BUSINESS_OR_ADR_APPROVAL",
            case_results=case_results,
        )

    async def evaluate_fallback_scenarios(
        self,
        valid_plan: PlanSchema,
    ) -> dict[str, str]:
        """Evaluate the 4 source-required fallback behavior scenarios deterministically."""
        outcomes: dict[str, str] = {}
        goal = GoalCreate(objective="Evaluate fallback behavior")
        test_settings = PlannerSettings(
            provider="test",
            primary_model="test-primary",
            fallback_model="test-fallback",
            timeout_seconds=5.0,
            max_retries=1,
            max_repair_attempts=0,
        )

        # 1. Primary Success
        primary_ok_planner = PydanticAiPlanner(
            model=TestModel(custom_output_args=valid_plan.model_dump()),
            settings=test_settings,
        )
        res1 = await primary_ok_planner.generate_plan_with_result(goal)
        outcomes["PRIMARY_SUCCESS"] = (
            "PASSED" if not res1.fallback_used and res1.structured_output_success else "FAILED"
        )

        # 2. Primary Failure + Fallback Success
        from tests.ai_eval.deterministic_model import SequenceTestModel

        class FailingTestModel(SequenceTestModel):
            def __init__(self) -> None:
                super().__init__([])

            def _get_output(self, params: Any) -> Any:
                raise RuntimeError("Primary model transport error")

        fb_success_planner = PydanticAiPlanner(
            model=FailingTestModel(),
            fallback_model=TestModel(custom_output_args=valid_plan.model_dump()),
            settings=test_settings,
        )
        res2 = await fb_success_planner.generate_plan_with_result(goal)
        outcomes["FALLBACK_SUCCESS"] = (
            "PASSED" if res2.fallback_used and res2.structured_output_success else "FAILED"
        )

        # 3. Primary Failure + No Configured Fallback
        no_fb_settings = PlannerSettings(
            provider="test",
            primary_model="test-primary",
            fallback_model=None,
            timeout_seconds=5.0,
            max_retries=0,
            max_repair_attempts=0,
        )
        no_fb_planner = PydanticAiPlanner(
            model=FailingTestModel(),
            settings=no_fb_settings,
        )
        try:
            await no_fb_planner.generate_plan_with_result(goal)
            outcomes["PRIMARY_FAILURE_NO_FALLBACK"] = "FAILED"
        except (PlannerInferenceError, RuntimeError):
            outcomes["PRIMARY_FAILURE_NO_FALLBACK"] = "PASSED"

        # 4. Fallback Failure (both models fail)
        both_fail_planner = PydanticAiPlanner(
            model=FailingTestModel(),
            fallback_model=FailingTestModel(),
            settings=test_settings,
        )
        try:
            await both_fail_planner.generate_plan_with_result(goal)
            outcomes["FALLBACK_FAILURE"] = "FAILED"
        except (PlannerInferenceError, RuntimeError):
            outcomes["FALLBACK_FAILURE"] = "PASSED"

        return outcomes

    def generate_markdown_report(self, result: EvaluationSuiteResult) -> str:
        """Generate concise Markdown evaluation report."""
        lines = [
            "# AI Gold-Set Evaluation Report (Phase 2B Reconciled)",
            "",
            "## Summary",
            f"- **Total Gold Cases Evaluated**: {result.total_cases}",
            f"- **Passed Cases**: {result.passed_cases}",
            f"- **Failed Cases**: {result.failed_cases}",
            f"- **Avg Required Task Coverage**: {result.average_task_coverage * 100:.1f}%",
            f"- **Avg Invalid Dependency Rate**: "
            f"{result.average_invalid_dependency_rate * 100:.1f}%",
            f"- **Avg Hallucinated Entity Rate**: "
            f"{result.average_hallucinated_entity_rate * 100:.1f}%",
            f"- **Total Dependency Cycles Detected**: {result.total_cycles_detected}",
            f"- **Total Hallucinated Resource Entities**: {result.total_hallucinations}",
            f"- **Human Correction Rate**: {result.human_correction_rate_status}",
            f"- **Cost Evaluation**: {result.cost_status}",
            f"- **Acceptance Threshold**: {result.acceptance_threshold_status}",
            "",
            "## Case-Level Results",
            "| ID | Scenario | Pass | TaskCov | Deps | InvDepRate | HallucRate | Rep | Fallback |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for c in result.case_results:
            status = "PASS" if c.is_passing else "FAIL"
            cov_str = f"{c.required_task_coverage * 100:.0f}%"
            inv_str = f"{c.invalid_dependency_rate * 100:.0f}%"
            halluc_str = f"{c.hallucinated_entity_rate * 100:.0f}%"
            lines.append(
                f"| {c.dataset_id} | {c.scenario_id} | {status} | {cov_str} | "
                f"{c.dependency_count} | {inv_str} | {halluc_str} | "
                f"{c.repair_attempts} | {c.fallback_behavior} |"
            )

        if any(not c.is_passing for c in result.case_results):
            lines.extend(["", "## Failure Diagnostics"])
            for c in result.case_results:
                if not c.is_passing:
                    lines.append(f"### {c.dataset_id}")
                    for reason in c.failure_reasons:
                        lines.append(f"- {reason}")

        return "\n".join(lines)

    def generate_json_report(self, result: EvaluationSuiteResult) -> str:
        """Generate machine-readable JSON evaluation report."""
        return json.dumps(asdict(result), indent=2)
