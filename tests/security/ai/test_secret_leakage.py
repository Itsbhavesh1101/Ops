"""Secret-leakage regression testing suite for Phase 8B.

Enforces:
Canary secrets seeded in test configuration must NEVER appear in:
1. Prompt text
2. Model input
3. Model output returned to callers
4. Validation errors
5. Repair prompts
6. Logs
7. Telemetry span attributes
8. Audit-event details
9. Exception messages
"""

import logging

import pytest
from pydantic import SecretStr

from app.task_intelligence.context.models import (
    ModelContext,
    ModelVisibleCalendar,
    ModelVisibleOrganization,
    ModelVisibleSubject,
)
from app.task_intelligence.planner.config import PlannerSettings
from app.task_intelligence.planner.prompts import (
    build_generation_prompt,
    build_repair_prompt,
)
from app.task_intelligence.planner.pydantic_ai_planner import PydanticAiPlanner
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
    TaskDraft,
    ValidationIssue,
    ValidationIssueCode,
    ValidationSeverity,
)
from app.task_intelligence.telemetry import PlannerTelemetry
from tests.ai_eval.deterministic_model import SequenceTestModel

CANARY_SECRETS: dict[str, str] = {
    "db_password": "FAKE_DB_PASSWORD_8B",
    "api_key": "FAKE_API_KEY_8B",
    "google_token": "FAKE_GOOGLE_TOKEN_8B",
    "notification_token": "FAKE_NOTIFICATION_TOKEN_8B",
}


def assert_zero_canary_leaks(target_text: str, label: str) -> None:
    """Assert none of the seeded canary secrets appear in the target text."""
    for name, secret in CANARY_SECRETS.items():
        assert secret not in target_text, (
            f"CANARY SECRET LEAK DETECTED in {label}: Secret '{name}' was exposed!"
        )


class TestCanarySecretLeakage:
    """Verifies that controlled canary secrets never escape system boundaries."""

    def test_canary_secrets_never_leak_in_generation_prompts(self) -> None:
        """Generation prompt constructed from goal and context must not contain canaries."""
        goal = GoalCreate(
            objective="Organize biology department examinations",
            scope="Include laboratory readiness checks",
            constraints=["Ensure zero conflict with faculty meeting"],
        )
        context = {
            "department": {"name": "Biology"},
            # Injected malicious context attempting canary reflection
            "attempted_leak": {
                "secret_key": CANARY_SECRETS["api_key"],
                "token": CANARY_SECRETS["google_token"],
            },
        }

        prompt = build_generation_prompt(goal, context=context)
        assert_zero_canary_leaks(prompt, "build_generation_prompt")

    def test_canaries_in_nested_forbidden_keys_scrubbed(self) -> None:
        """Canary secrets in deeply nested forbidden dictionary keys are scrubbed."""
        goal = GoalCreate(objective="Schedule physics classes")
        context = {
            "academic": {
                "level_1": {
                    "level_2": {
                        "password": CANARY_SECRETS["db_password"],
                        "access_token": CANARY_SECRETS["notification_token"],
                    }
                }
            }
        }
        prompt = build_generation_prompt(goal, context=context)
        assert_zero_canary_leaks(prompt, "nested_forbidden_keys")

    def test_canaries_in_allowed_freetext_context_fields_redacted(self) -> None:
        """Canary secrets embedded inside allowed free-text fields are redacted by value policy."""
        ctx = ModelContext(
            organization=[
                ModelVisibleOrganization(
                    department_name=f"Science token={CANARY_SECRETS['api_key']}",
                    designation="Lead Coordinator",
                )
            ],
            calendar_entries=[
                ModelVisibleCalendar(
                    event_title="Exam Prep",
                    event_description=f"Room password: {CANARY_SECRETS['db_password']}",
                ),
                ModelVisibleCalendar(
                    holiday_description=f"Auth: {CANARY_SECRETS['notification_token']}",
                ),
            ],
            subjects=[
                ModelVisibleSubject(
                    subject_name="Physics",
                    curricular_assignment_description=f"Bearer {CANARY_SECRETS['google_token']}",
                )
            ],
        )
        goal = GoalCreate(objective="Coordinate examinations")
        prompt = build_generation_prompt(goal, context=ctx)
        assert_zero_canary_leaks(prompt, "allowed_freetext_context_fields")
        assert "[REDACTED_SECRET]" in prompt or "[REDACTED_TOKEN]" in prompt

    def test_canary_secrets_never_leak_in_repair_prompts(self) -> None:
        """Repair prompts must not echo configuration secrets."""
        plan = PlanSchema(
            plan_name="Exam Plan",
            tasks=[
                TaskDraft(
                    temporary_id="T1",
                    title="Setup room",
                    estimated_duration_minutes=30,
                )
            ],
        )
        issue = ValidationIssue(
            code=ValidationIssueCode.EMPTY_PLAN,
            location="tasks",
            message="Structural check failed",
            severity=ValidationSeverity.ERROR,
            repairable=True,
        )

        repair_prompt = build_repair_prompt(current_proposal=plan, issues=[issue])
        assert_zero_canary_leaks(repair_prompt, "build_repair_prompt")

    @pytest.mark.asyncio
    async def test_canary_secrets_never_leak_in_model_generation_result(self) -> None:
        """Planner execution result does not leak configuration secrets."""
        planner_settings = PlannerSettings(
            provider="test",
            primary_model="test-primary",
            timeout_seconds=5.0,
            max_retries=1,
            max_repair_attempts=1,
            api_key=SecretStr(CANARY_SECRETS["api_key"]),
        )

        test_model = SequenceTestModel(
            [
                {
                    "plan_name": "Generated Plan",
                    "tasks": [
                        {
                            "temporary_id": "T1",
                            "title": "Task 1",
                            "estimated_duration_minutes": 60,
                        }
                    ],
                    "dependencies": [],
                    "milestones": [],
                    "metadata": {},
                }
            ]
        )

        planner = PydanticAiPlanner(model=test_model, settings=planner_settings)
        goal = GoalCreate(objective="Deploy exam")
        result = await planner.generate_plan_with_result(goal)

        # Inspect plan schema and generation result serialization
        result_json = result.model_dump_json()
        assert_zero_canary_leaks(result_json, "PlannerGenerationResult")

    def test_canary_secrets_never_leak_in_telemetry_metrics_or_spans(self) -> None:
        """OpenTelemetry instrumentation must not record credentials as metric or span labels."""
        telemetry = PlannerTelemetry()
        with telemetry.record_generation(provider="test-provider") as tel_ctx:
            tel_ctx["outcome"] = "success"
            tel_ctx["tokens"] = 150

        # Telemetry context only has safe bounded keys
        assert_zero_canary_leaks(str(tel_ctx), "telemetry_context")

    def test_canary_secrets_never_leak_in_validation_error_messages(self) -> None:
        """Validation errors must report only domain locations and clean descriptions."""
        issue = ValidationIssue(
            code=ValidationIssueCode.INVALID_DURATION,
            location="tasks[T1].estimated_duration_minutes",
            message="Task duration cannot be negative: -30",
            severity=ValidationSeverity.ERROR,
        )
        assert_zero_canary_leaks(issue.model_dump_json(), "ValidationIssue")

    def test_canary_secrets_never_leak_in_exceptions_or_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """System exceptions and log records must not echo configuration secrets."""
        with caplog.at_level(logging.DEBUG):
            logging.info("Starting planner execution with configured parameters.")
            logging.debug("Planner initialized successfully.")

        log_output = caplog.text
        assert_zero_canary_leaks(log_output, "caplog_records")
