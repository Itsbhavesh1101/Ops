"""Shared, secret-safe evidence helpers for production-boundary probes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _redact(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, Mapping):
        return {str(key): _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    return value


async def run_bounded_probe(
    blocker_id: str,
    probe: Awaitable[dict[str, Any]],
    *,
    timeout_seconds: float,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Run a probe with a hard deadline and emit a stable, redacted envelope."""
    envelope: dict[str, Any] = {
        "schema_version": "1.0",
        "blocker_id": blocker_id,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    try:
        result = await asyncio.wait_for(probe, timeout=timeout_seconds)
        envelope.update(result)
    except TimeoutError:
        envelope.update({"status": "FAIL", "error_code": "TIMEOUT"})
    except Exception as exc:  # Evidence must never echo credential-bearing exception text.
        envelope.update(
            {
                "status": "FAIL",
                "error_code": "PROBE_ERROR",
                "error_type": type(exc).__name__,
            }
        )
    return _redact(envelope, secret_values)


def write_evidence_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def finish_cli(payload: dict[str, Any], output: Path | None) -> int:
    if output is not None:
        write_evidence_atomic(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") == "PASS" else 1
