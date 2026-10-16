"""Telemetry and logging security test suite for Phase 8B.

Enforces:
1. Low-cardinality safe metadata only in metrics and spans:
   - provider, outcome, latency, token count, repair count
2. Strict prohibition against recording:
   - raw prompts
   - raw authorized context
   - raw model responses
   - Authorization headers
   - API keys
   - high-cardinality tenant or entity IDs in metric labels
"""


from app.task_intelligence.telemetry import (
    VALID_OUTCOMES,
    PlannerTelemetry,
)


class TestTelemetrySecurity:
    """Verifies privacy and low cardinality in AI telemetry instrumentation."""

    def test_telemetry_outcomes_are_strictly_closed_categories(self) -> None:
        """Outcome attributes must be one of the closed set."""
        expected = frozenset({"success", "failure", "repaired", "exhausted"})
        assert VALID_OUTCOMES == expected

    def test_record_generation_emits_no_sensitive_metadata(self) -> None:
        """The telemetry context dictionary contains only bounded operational metrics."""
        telemetry = PlannerTelemetry()
        with telemetry.record_generation(provider="openai_compatible") as ctx:
            ctx["outcome"] = "success"
            ctx["repair_count"] = 1
            ctx["tokens"] = 320

        # Keys allowed in telemetry context
        allowed_keys = {"outcome", "fallback_used", "repair_count", "tokens"}
        assert set(ctx.keys()) == allowed_keys

        # Ensure no raw prompt or response keys exist
        assert "prompt" not in ctx
        assert "context" not in ctx
        assert "response" not in ctx
        assert "api_key" not in ctx
        assert "authorization" not in ctx
        assert "tenant_id" not in ctx
