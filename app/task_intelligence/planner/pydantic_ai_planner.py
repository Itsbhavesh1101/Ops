"""Production PydanticAI Planner implementation (Phase 2B).

Satisfies PlannerProtocol:
- Bounded retry and timeout
- Configurable fallback provider
- Bounded targeted repair driven by deterministic validation issues
- Safe low-cardinality AI telemetry
- Structured PlanSchema output enforcement
- Model proposal is strictly non-authoritative
"""

import asyncio
import os
import time
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model

# Suppress banner in production / tests
os.environ["PYDANTIC_AI_NO_BANNER"] = "1"

from app.core.config import Settings, get_settings
from app.task_intelligence.planner.config import (
    PlannerConfigurationError,
    PlannerSettings,
)
from app.task_intelligence.planner.generation_result import PlannerGenerationResult
from app.task_intelligence.planner.prompts import (
    PLANNER_SYSTEM_INSTRUCTION,
    build_generation_prompt,
    build_repair_prompt,
)
from app.task_intelligence.planner.protocol import PlannerProtocol
from app.task_intelligence.planner.provider import get_llm_model
from app.task_intelligence.schemas import (
    GoalCreate,
    PlanSchema,
    ValidationResult,
)
from app.task_intelligence.telemetry import get_planner_telemetry
from app.task_intelligence.validators import PlanValidator


class PlannerError(Exception):
    """Base exception for planner errors."""


class PlannerInferenceError(PlannerError):
    """Raised when model inference fails after retries and fallback."""


class PlannerTimeoutError(PlannerInferenceError):
    """Raised when inference exceeds configured timeout."""


class PydanticAiPlanner(PlannerProtocol):
    """PydanticAI-powered operational plan generator conforming to PlannerProtocol."""

    def __init__(
        self,
        model: Model | None = None,
        fallback_model: Model | None = None,
        validator: PlanValidator | None = None,
        settings: PlannerSettings | Settings | None = None,
    ) -> None:
        if isinstance(settings, PlannerSettings):
            self._settings = settings
        elif isinstance(settings, Settings):
            self._settings = PlannerSettings.from_app_settings(settings)
        elif settings is None:
            self._settings = PlannerSettings.from_app_settings(get_settings())
        else:
            raise PlannerConfigurationError(f"Unsupported settings type: {type(settings).__name__}")

        self._primary_model = model or get_llm_model(settings=self._settings)

        self._fallback_model = fallback_model
        if self._fallback_model is None and self._settings.fallback_model:
            try:
                self._fallback_model = get_llm_model(fallback=True, settings=self._settings)
            except Exception:
                self._fallback_model = None

        self._validator = validator or PlanValidator()
        self._telemetry = get_planner_telemetry()

    def _create_agent(self, model: Model) -> Agent[None, PlanSchema]:
        """Create PydanticAI Agent enforcing typed PlanSchema output."""
        return Agent(
            model=model,
            output_type=PlanSchema,
            system_prompt=PLANNER_SYSTEM_INSTRUCTION,
        )

    async def _execute_with_retry(
        self,
        agent: Agent[None, PlanSchema],
        prompt: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> tuple[PlanSchema, Any]:
        """Execute agent inference with bounded retry and timeout."""
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    agent.run(prompt),
                    timeout=timeout_seconds,
                )
                return result.output, result.usage
            except TimeoutError:
                last_error = PlannerTimeoutError(
                    f"Inference request timed out after {timeout_seconds} seconds."
                )
            except Exception as exc:
                last_error = exc
                # Short backoff between retries (avoid spinning)
                if attempt < max_retries:
                    await asyncio.sleep(0.1 * (attempt + 1))

        if isinstance(last_error, PlannerTimeoutError):
            raise last_error
        raise PlannerInferenceError(
            f"Planner inference failed after {max_retries + 1} attempts."
        ) from last_error

    async def generate_plan_with_result(
        self,
        goal: GoalCreate,
        context: dict[str, Any] | None = None,
    ) -> PlannerGenerationResult:
        """Decompose a goal into a PlanSchema proposal with auditable generation metadata.

        Runs:
        1. Primary model call (with timeout and bounded retries)
        2. Fallback model call if primary fails and fallback is configured
        3. Deterministic PlanValidator checks
        4. Bounded targeted repair loop if deterministic issues are detected
        5. Safe telemetry recording
        """
        start_time = time.perf_counter()
        timeout = self._settings.timeout_seconds
        max_retries = self._settings.max_retries
        max_repairs = self._settings.max_repair_attempts
        provider_name = self._settings.provider

        attempts = 0
        repair_attempts = 0
        fallback_used = False
        resolved_model_name = self._settings.primary_model
        primary_agent = self._create_agent(self._primary_model)

        prompt = build_generation_prompt(goal, context)
        raw_usage: Any = None

        with self._telemetry.record_generation(provider=provider_name) as tel_ctx:
            # 1. Primary Inference (or Fallback if Primary fails)
            try:
                attempts += 1
                proposal, raw_usage = await self._execute_with_retry(
                    agent=primary_agent,
                    prompt=prompt,
                    timeout_seconds=timeout,
                    max_retries=max_retries,
                )
            except Exception as primary_exc:
                if self._fallback_model is not None:
                    fallback_used = True
                    tel_ctx["fallback_used"] = True
                    resolved_model_name = self._settings.fallback_model or "configured_fallback"
                    fallback_agent = self._create_agent(self._fallback_model)
                    attempts += 1
                    try:
                        proposal, raw_usage = await self._execute_with_retry(
                            agent=fallback_agent,
                            prompt=prompt,
                            timeout_seconds=timeout,
                            max_retries=max_retries,
                        )
                    except Exception as fallback_exc:
                        tel_ctx["outcome"] = "failure"
                        raise PlannerInferenceError(
                            "Both primary and fallback models failed inference."
                        ) from fallback_exc
                else:
                    tel_ctx["outcome"] = "failure"
                    raise primary_exc

            # 2. Deterministic Validation Gate
            validation_res: ValidationResult = self._validator.validate(proposal)

            # 3. Bounded Targeted Repair Loop
            active_agent = (
                self._create_agent(self._fallback_model)
                if fallback_used and self._fallback_model
                else primary_agent
            )

            while not validation_res.is_valid and repair_attempts < max_repairs:
                repair_attempts += 1
                repair_prompt = build_repair_prompt(
                    current_proposal=proposal,
                    issues=validation_res.issues,
                    context=context,
                )
                try:
                    repaired_proposal, repair_usage = await self._execute_with_retry(
                        agent=active_agent,
                        prompt=repair_prompt,
                        timeout_seconds=timeout,
                        max_retries=max_retries,
                    )
                    # Re-validate the entire proposal deterministically
                    re_validation = self._validator.validate(repaired_proposal)
                    proposal = repaired_proposal
                    validation_res = re_validation
                    raw_usage = repair_usage

                    if validation_res.is_valid:
                        break
                except Exception:
                    # If repair inference fails, break and keep current best proposal
                    break

            # 4. Telemetry Finalization
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            tel_ctx["repair_count"] = repair_attempts
            if validation_res.is_valid:
                tel_ctx["outcome"] = "repaired" if repair_attempts > 0 else "success"
            else:
                tel_ctx["outcome"] = "exhausted" if repair_attempts >= max_repairs else "failure"

            safe_usage: dict[str, Any] = {}
            if raw_usage is not None:
                total_tok = getattr(raw_usage, "total_tokens", None)
                cost_val = getattr(raw_usage, "cost", None)
                safe_usage = {
                    "total_tokens": total_tok,
                    "cost": "unavailable" if cost_val is None else str(cost_val),
                }
                tel_ctx["tokens"] = total_tok

            return PlannerGenerationResult(
                plan=proposal,
                model_name=resolved_model_name,
                provider=provider_name,
                attempts=attempts,
                repair_attempts=repair_attempts,
                fallback_used=fallback_used,
                structured_output_success=True,
                latency_ms=elapsed_ms,
                usage=safe_usage,
                validation_result=validation_res,
            )

    async def generate_plan(
        self,
        goal: GoalCreate,
        context: dict[str, Any] | None = None,
    ) -> PlanSchema:
        """Decompose a high-level goal into a typed PlanSchema proposal.

        Conforms strictly to PlannerProtocol.
        """
        result = await self.generate_plan_with_result(goal=goal, context=context)
        return result.plan
