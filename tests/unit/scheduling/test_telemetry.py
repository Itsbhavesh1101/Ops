from unittest.mock import MagicMock

from app.core.telemetry.scheduling import (
    VALID_RESULT_STATUSES,
    SchedulingTelemetry,
    get_scheduling_telemetry,
)

FORBIDDEN_METRIC_ATTRIBUTES = frozenset({
    "tenant_id",
    "job_id",
    "schedule_id",
    "activity_id",
    "resource_id",
    "constraint_id",
    "correlation_id",
    "trace_id",
    "span_id",
})


def test_scheduling_telemetry_recording() -> None:
    """Verify recording solve attempts, outcomes, and span context without exceptions."""
    telemetry = get_scheduling_telemetry()
    telemetry.record_attempt()
    telemetry.record_result("FEASIBLE", duration_seconds=1.23, assignment_count=10)
    telemetry.record_result("INFEASIBLE", duration_seconds=0.45)
    telemetry.record_result("BUDGET_EXHAUSTED", duration_seconds=5.0)
    telemetry.record_result("FAILED", duration_seconds=0.10)

    # Context manager test
    with telemetry.span_solve() as span:
        assert span is not None


def test_no_prohibited_labels_in_metrics() -> None:
    """Verify prohibited metric labels are never accepted or recorded in metrics."""
    telemetry = SchedulingTelemetry()

    # Mock the internal instruments to inspect label arguments directly
    mock_results = MagicMock()
    mock_duration = MagicMock()
    mock_attempts = MagicMock()
    mock_assignments = MagicMock()
    mock_infeasibility = MagicMock()

    telemetry._results_counter = mock_results
    telemetry._duration_histogram = mock_duration
    telemetry._attempts_counter = mock_attempts
    telemetry._assignments_counter = mock_assignments
    telemetry._infeasibility_counter = mock_infeasibility

    # Record attempts and outcomes across all valid statuses
    telemetry.record_attempt()
    for status in ("FEASIBLE", "INFEASIBLE", "BUDGET_EXHAUSTED", "FAILED", "ARBITRARY_STATUS"):
        telemetry.record_result(status=status, duration_seconds=0.5, assignment_count=5)

    # Validate results_counter call attributes
    for call in mock_results.add.call_args_list:
        args, kwargs = call
        attributes = args[1] if len(args) > 1 else kwargs.get("attributes", {})
        # Must only contain result_status
        assert set(attributes.keys()) <= {"result_status"}
        # Must not contain any forbidden high-cardinality attributes
        assert not any(attr in attributes for attr in FORBIDDEN_METRIC_ATTRIBUTES)
        assert attributes["result_status"] in VALID_RESULT_STATUSES

    # Validate duration_histogram call attributes
    for call in mock_duration.record.call_args_list:
        args, kwargs = call
        attributes = args[1] if len(args) > 1 else kwargs.get("attributes", {})
        assert set(attributes.keys()) <= {"result_status"}
        assert not any(attr in attributes for attr in FORBIDDEN_METRIC_ATTRIBUTES)
        assert attributes["result_status"] in VALID_RESULT_STATUSES
