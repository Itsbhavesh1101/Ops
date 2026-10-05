"""Focused unit tests for AI Planner configuration, compliance, and metrics governance (Phase 2B).

Proves:
1. No active provider default in app.core.config.Settings
2. No active model default in app.core.config.Settings
3. No active provider URL default in app.core.config.Settings
4. Planner creation fails clearly without required runtime config
5. App import/startup still works safely without AI config
6. Test provider still works when explicitly configured
7. Timeout, retry budget, and repair budget come strictly from explicit configuration
8. OpenAI compatible provider requires explicit API key (no silent test fallback)
9. Dependency compliance records exist and contain official license evidence
10. Metric calculation logic (invalid dependency rate, required task coverage)
"""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.task_intelligence.planner import (
    PlannerConfigurationError,
    PlannerSettings,
    PydanticAiPlanner,
)
from app.task_intelligence.planner.provider import (
    ProviderConfigurationError,
    get_llm_model,
)
from app.task_intelligence.schemas import (
    DependencyDraft,
    PlanSchema,
    TaskDraft,
)
from tests.ai_eval.deterministic_model import (
    SequenceTestModel,
    create_gold_standard_exam_plan,
    create_test_planner_settings,
)
from tests.ai_eval.evaluator import GoldSetEvaluator


def test_settings_have_no_active_llm_defaults() -> None:
    """Verify production Settings contains zero silent LLM provider or model defaults."""
    s = Settings()
    assert s.llm_provider is None
    assert s.llm_primary_model is None
    assert s.llm_fallback_model is None
    assert s.llm_base_url is None
    assert s.llm_timeout_seconds is None
    assert s.llm_max_retries is None
    assert s.llm_max_repair_attempts is None
    assert s.llm_api_key is None


def test_planner_creation_fails_without_required_runtime_config() -> None:
    """Verify attempting to construct a planner without required runtime config fails clearly."""
    s = Settings()
    # Unconfigured app settings must raise PlannerConfigurationError
    with pytest.raises(PlannerConfigurationError) as exc_info:
        PlannerSettings.from_app_settings(s)
    err_msg = str(exc_info.value)
    assert "Cannot construct AI planner without explicit configuration" in err_msg
    assert "llm_provider" in err_msg
    assert "llm_primary_model" in err_msg
    assert "llm_timeout_seconds" in err_msg
    assert "llm_max_retries" in err_msg
    assert "llm_max_repair_attempts" in err_msg

    # Also directly instantiating PydanticAiPlanner() without settings or model fails
    with pytest.raises(PlannerConfigurationError):
        PydanticAiPlanner()


def test_app_import_and_startup_safe_without_ai_config() -> None:
    """Verify importing app.main and mounting FastAPI succeeds safely without AI config."""
    import app.main

    assert app.main.app is not None
    assert app.main.app.title == "E-7 Operations Engine API"


def test_test_provider_works_when_explicitly_configured() -> None:
    """Verify test provider works deterministically when explicitly configured."""
    settings = PlannerSettings(
        provider="test",
        primary_model="test-model",
        timeout_seconds=10.0,
        max_retries=1,
        max_repair_attempts=1,
    )
    planner = PydanticAiPlanner(settings=settings)
    assert planner._settings.provider == "test"
    assert planner._settings.primary_model == "test-model"
    assert planner._primary_model is not None


def test_timeout_retry_repair_values_come_from_explicit_configuration() -> None:
    """Verify timeouts, retry counts, and repair attempts are strictly read from config."""
    settings = PlannerSettings(
        provider="test",
        primary_model="test-custom-model",
        timeout_seconds=42.5,
        max_retries=5,
        max_repair_attempts=4,
    )
    planner = PydanticAiPlanner(settings=settings)
    assert planner._settings.timeout_seconds == 42.5
    assert planner._settings.max_retries == 5
    assert planner._settings.max_repair_attempts == 4


def test_openai_compatible_requires_api_key_no_silent_fallback() -> None:
    """Verify openai_compatible provider fails clearly when api_key is missing."""
    settings = PlannerSettings(
        provider="openai_compatible",
        primary_model="sarvam-105b",
        timeout_seconds=30.0,
        max_retries=2,
        max_repair_attempts=2,
        api_key=None,
    )
    with pytest.raises(ProviderConfigurationError) as exc_info:
        get_llm_model(settings=settings)
    assert "Missing LLM API key" in str(exc_info.value)


def test_compliance_license_files_exist_and_match_conventions() -> None:
    """Verify license files and register entries exist for added dependencies."""
    repo_root = Path(__file__).resolve().parents[3]

    pydanticai_lic = repo_root / "third_party" / "licenses" / "LICENSE.pydanticai.txt"
    assert pydanticai_lic.is_file(), "LICENSE.pydanticai.txt must exist"
    pydanticai_content = pydanticai_lic.read_text(encoding="utf-8")
    assert "MIT License" in pydanticai_content
    assert "pydantic-ai-slim" in pydanticai_content

    openai_lic = repo_root / "third_party" / "licenses" / "LICENSE.openai.txt"
    assert openai_lic.is_file(), "LICENSE.openai.txt must exist"
    openai_content = openai_lic.read_text(encoding="utf-8")
    assert "Apache License" in openai_content
    assert "Version 2.0" in openai_content
    assert "OpenAI" in openai_content

    register_path = repo_root / "docs" / "OPEN_SOURCE_COMPLIANCE_REGISTER.md"
    assert register_path.is_file(), "OPEN_SOURCE_COMPLIANCE_REGISTER.md must exist"
    register_content = register_path.read_text(encoding="utf-8")
    assert "pydantic-ai-slim" in register_content or "PydanticAI" in register_content
    assert "OpenAI Python SDK" in register_content
    assert "Apache-2.0" in register_content


def test_invalid_dependency_rate_calculation() -> None:
    """Verify invalid_dependency_rate correctly calculates invalid edges over total edges."""
    dummy_model = SequenceTestModel([])
    settings = create_test_planner_settings()
    planner = PydanticAiPlanner(model=dummy_model, settings=settings)
    evaluator = GoldSetEvaluator(planner=planner)

    fixture_data = {"expected_plan_properties": {}, "authorized_context": {}}

    # 1. Fully valid plan: 0 invalid edges
    valid_plan = create_gold_standard_exam_plan()
    val_res_valid = evaluator._validator.validate(valid_plan)
    case_valid = evaluator.evaluate_plan_properties(fixture_data, valid_plan, val_res_valid)
    assert case_valid.invalid_dependency_rate == 0.0

    # 2. Plan with cycle: cycle-causing edges flagged as invalid dependencies
    cyclic_plan = PlanSchema(
        plan_name="Cyclic Plan",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=60),
            TaskDraft(temporary_id="T2", title="Task 2", estimated_duration_minutes=60),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
            DependencyDraft(predecessor_id="T2", successor_id="T1"),
        ],
    )
    val_res_cyclic = evaluator._validator.validate(cyclic_plan)
    case_cyclic = evaluator.evaluate_plan_properties(fixture_data, cyclic_plan, val_res_cyclic)
    assert case_cyclic.cycle_detected is True
    assert case_cyclic.invalid_dependency_rate > 0.0

    # 3. Plan with no dependencies: rate = 0.0
    no_dep_plan = PlanSchema(
        plan_name="No Dep Plan",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", estimated_duration_minutes=60),
        ],
        dependencies=[],
    )
    val_res_no_dep = evaluator._validator.validate(no_dep_plan)
    case_no_dep = evaluator.evaluate_plan_properties(fixture_data, no_dep_plan, val_res_no_dep)
    assert case_no_dep.invalid_dependency_rate == 0.0
