"""Structlog processor module for OpenTelemetry trace-context enrichment.

Injects active trace_id and span_id into structured log event dictionaries
without mutating existing correlation_id or creating new spans.
"""

from typing import Any

import opentelemetry.trace as trace
from structlog.types import EventDict


def add_otel_trace_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor to inject active OpenTelemetry trace and span IDs.

    Behavior:
    - Queries the currently active OpenTelemetry span.
    - If no valid active span exists, leaves event_dict unmodified.
    - If an active valid span exists, injects:
        * `trace_id`: 32-character lowercase hexadecimal string
        * `span_id`: 16-character lowercase hexadecimal string
    - Does NOT create or alter spans.
    - Preserves existing `correlation_id` in event_dict.
    - Never injects headers, payloads, baggage, or span attributes.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()

    if span_context is not None and span_context.is_valid:
        # Zero-pad to standard 32-char (trace) and 16-char (span) lowercase hex
        event_dict["trace_id"] = f"{span_context.trace_id:032x}"
        event_dict["span_id"] = f"{span_context.span_id:016x}"

    return event_dict
