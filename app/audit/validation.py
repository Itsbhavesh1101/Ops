"""Defensive validation and sanitization for audit event details.

Enforces:
- Root must be a mapping/dict with string keys.
- Recursive denial of suspicious authentication/secret keys (case-insensitive, separator-agnostic).
- Bounded depth, serialized size, and string length limits.
- Supported JSON-compatible primitive types only (rejecting exceptions, ORM instances, tokens).
- Zero raw credential leakage.
"""

import json
import math
from typing import Any

# Defensive technical bounds for audit details
MAX_AUDIT_DETAILS_BYTES: int = 65536  # 64 KiB serialized JSON payload
MAX_AUDIT_DETAILS_DEPTH: int = 8  # Maximum allowed nested dictionary/list depth
MAX_AUDIT_STRING_LENGTH: int = 4096  # Maximum characters per individual string value

# Denylist of normalized key tokens (lowercase, stripped of '_', '-', ' ')
DENIED_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "authorization",
        "cookie",
    }
)


def normalize_audit_key(key: str) -> str:
    """Normalize key string by lowercasing and stripping separators (underscore, hyphen, space)."""
    return key.lower().replace("_", "").replace("-", "").replace(" ", "")


def _validate_details_node(node: Any, current_depth: int) -> None:
    """Recursively validate a node in the audit details structure."""
    if current_depth > MAX_AUDIT_DETAILS_DEPTH:
        raise ValueError(
            f"Audit details nesting depth exceeds maximum limit of {MAX_AUDIT_DETAILS_DEPTH}"
        )

    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                raise ValueError("Audit details dictionary keys must be strings")

            normalized_key = normalize_audit_key(k)
            if normalized_key in DENIED_KEY_TOKENS:
                raise ValueError(f"Suspicious sensitive key '{k}' is prohibited in audit details")

            _validate_details_node(v, current_depth + 1)

    elif isinstance(node, (list, tuple)):
        for item in node:
            _validate_details_node(item, current_depth + 1)

    elif isinstance(node, str):
        if len(node) > MAX_AUDIT_STRING_LENGTH:
            raise ValueError(
                f"Audit details string value exceeds maximum length of {MAX_AUDIT_STRING_LENGTH}"
            )

    elif isinstance(node, bool) or node is None:
        return

    elif isinstance(node, int):
        return

    elif isinstance(node, float):
        if not math.isfinite(node):
            raise ValueError(f"Non-finite float value '{node}' is prohibited in audit details")
        return

    else:
        raise ValueError(
            f"Unsupported value type '{type(node).__name__}' in audit details. "
            "Only JSON-compatible primitives (null, bool, int, float, str, list, dict) are allowed."
        )


def validate_audit_details(details: Any) -> dict[str, Any]:
    """Validate audit details structure, enforcing security and size invariants.

    Returns the validated details dictionary.

    Raises:
        ValueError: If details violate root structure, key denylist, bounds, or JSON-compatibility.
    """
    if details is None:
        return {}

    if not isinstance(details, dict):
        raise ValueError("Audit details root must be a dictionary/mapping")

    # Recursive structure and key validation
    _validate_details_node(details, current_depth=1)

    # Serialized payload size check with strict non-standard float rejection
    try:
        serialized = json.dumps(details, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Audit details cannot be serialized to JSON: {err}") from err

    byte_size = len(serialized.encode("utf-8"))
    if byte_size > MAX_AUDIT_DETAILS_BYTES:
        raise ValueError(
            f"Serialized audit details size ({byte_size} bytes) exceeds limit "
            f"of {MAX_AUDIT_DETAILS_BYTES} bytes"
        )

    return details
