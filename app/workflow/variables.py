"""Workflow variable minimization and mandatory allowlist filtering boundary (Phase 3A).

Enforces:
- Restricting persisted workflow variables to an explicit mandatory allowlist.
- Fail-closed behavior: absence of an allowlist contract strictly drops all variables.
- Defense-in-depth: rejection of internal metadata (_*), credential/token scrubbing.
- Strict size bounding (max string length 2000, max payload 32KB).
- Zero raw Flowable variable dumps copied into E-7 PostgreSQL canonical tables.
"""

import json
from collections.abc import Set
from dataclasses import dataclass
from typing import Any

WORKFLOW_VARIABLE_POLICY: str = "MANDATORY_ALLOWLIST"

MAX_VARIABLE_STRING_LENGTH: int = 2000
MAX_VARIABLES_PAYLOAD_BYTES: int = 32 * 1024  # 32 KB

SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "auth",
    "private_key",
    "api_key",
)

# Standard allowlist for Phase-3A sequential fixture operations (budget removed)
PHASE_3A_FIXTURE_ALLOWED_VARIABLES: frozenset[str] = frozenset(
    {"priority", "decision", "comment", "approved", "reason"}
)

# Standard allowlist for Phase-6 semester examination readiness operations & DMN rules
PHASE_6_ALLOWED_VARIABLES: frozenset[str] = frozenset(
    {
        "science_approved",
        "humanities_approved",
        "curriculum_coverage",
        "readiness_status",
        "recommendation",
        "sla_warning_at",
        "sla_breach_at",
        "moderation_approved",
    }
)


class WorkflowVariableValidationError(ValueError):
    """Raised when workflow variable payload violates size or serialization constraints."""


@dataclass(frozen=True, slots=True)
class WorkflowVariablePolicy:
    """Explicit mandatory allowlist policy for workflow variables."""

    allowed_keys: frozenset[str]

    def filter(self, variables: dict[str, Any] | None) -> dict[str, Any]:
        """Apply mandatory allowlist policy to variable dictionary."""
        return filter_workflow_variables(variables, self.allowed_keys)


def filter_workflow_variables(
    variables: dict[str, Any] | None,
    allowed_keys: Set[str],
) -> dict[str, Any]:
    """Filter and sanitize workflow variables before persistence in canonical SoR tables.

    Args:
        variables: Raw input or engine task variables.
        allowed_keys: Explicit required set of permitted business keys.
                      If empty, fails closed and returns {}.

    Returns:
        dict[str, Any]: Minimized dictionary safe for canonical persistence.

    Raises:
        WorkflowVariableValidationError: If payload exceeds size limits.
    """
    if not variables or not allowed_keys:
        return {}

    filtered: dict[str, Any] = {}

    for key, value in variables.items():
        if not isinstance(key, str):
            continue

        key_lower = key.lower()

        # 1. Mandatory Positive Allowlist: key must be explicitly permitted
        if key not in allowed_keys:
            continue

        # 2. Defense-in-depth: Reject internal metadata variables (_*, :*)
        if key.startswith("_") or ":" in key:
            continue

        # 3. Defense-in-depth: Reject sensitive credentials, tokens, and keys
        if any(sens in key_lower for sens in SENSITIVE_KEY_SUBSTRINGS):
            continue

        # 4. Value type and size bounding
        if isinstance(value, str):
            if len(value) > MAX_VARIABLE_STRING_LENGTH:
                value = value[:MAX_VARIABLE_STRING_LENGTH]
            filtered[key] = value
        elif isinstance(value, (bool, int, float)) or value is None:
            filtered[key] = value
        elif isinstance(value, (dict, list)):
            try:
                serialized = json.dumps(value)
                if len(serialized) > MAX_VARIABLE_STRING_LENGTH:
                    continue  # Drop oversized nested structures
                filtered[key] = json.loads(serialized)
            except (TypeError, ValueError):
                continue
        else:
            # Drop unhandled complex or non-serializable objects
            continue

    # 5. Check overall payload size limit
    try:
        total_bytes = len(json.dumps(filtered).encode("utf-8"))
        if total_bytes > MAX_VARIABLES_PAYLOAD_BYTES:
            msg = (
                "Filtered workflow variables exceed max size of "
                f"{MAX_VARIABLES_PAYLOAD_BYTES} bytes."
            )
            raise WorkflowVariableValidationError(msg)
    except (TypeError, ValueError) as exc:
        raise WorkflowVariableValidationError(
            f"Failed to serialize filtered workflow variables: {exc}"
        ) from exc

    return filtered
