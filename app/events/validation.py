"""Defensive validation, sanitization, and recursive immutability for event payloads.

Part of Phase 1 Foundation Task 1G.1 / Task 1G.1A.
Enforces:
- Root must be a mapping/dict with string keys.
- Recursive denial of suspicious authentication/secret keys (case-insensitive, separator-agnostic).
- Bounded depth, serialized size, and string length limits.
- Supported JSON-compatible primitive types only (null, bool, int, finite float, str, list, dict).
- Rejection of NaN, +Infinity, -Infinity without silent coercion.
- Recursive immutable representation (MappingProxyType for mappings, tuple for sequences).
- Zero raw credential leakage.
"""

import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

# Defensive technical bounds for event payloads
MAX_EVENT_PAYLOAD_BYTES: int = 262144  # 256 KiB serialized JSON payload
MAX_EVENT_PAYLOAD_DEPTH: int = 12  # Maximum allowed nested dictionary/list depth
MAX_EVENT_STRING_LENGTH: int = 16384  # Maximum characters per individual string value

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
        "privatekey",
    }
)


def normalize_event_key(key: str) -> str:
    """Normalize key string by lowercasing and stripping separators (underscore, hyphen, space)."""
    return key.lower().replace("_", "").replace("-", "").replace(" ", "")


def _validate_payload_node(node: Any, current_depth: int) -> None:
    """Recursively validate a node in the event payload structure."""
    if current_depth > MAX_EVENT_PAYLOAD_DEPTH:
        raise ValueError(
            f"Event payload nesting depth exceeds maximum limit of {MAX_EVENT_PAYLOAD_DEPTH}"
        )

    if isinstance(node, Mapping):
        for k, v in node.items():
            if not isinstance(k, str):
                raise ValueError("Event payload dictionary keys must be strings")

            normalized_key = normalize_event_key(k)
            if normalized_key in DENIED_KEY_TOKENS:
                raise ValueError(f"Suspicious sensitive key '{k}' is prohibited in event payload")

            _validate_payload_node(v, current_depth + 1)

    elif isinstance(node, (list, tuple)):
        for item in node:
            _validate_payload_node(item, current_depth + 1)

    elif isinstance(node, str):
        if len(node) > MAX_EVENT_STRING_LENGTH:
            raise ValueError(
                f"Event payload string value exceeds maximum length of {MAX_EVENT_STRING_LENGTH}"
            )

    elif isinstance(node, bool) or node is None:
        return

    elif isinstance(node, int):
        return

    elif isinstance(node, float):
        if not math.isfinite(node):
            raise ValueError(f"Non-finite float value '{node}' is prohibited in event payload")
        return

    else:
        raise ValueError(
            f"Unsupported value type '{type(node).__name__}' in event payload. "
            "Only JSON-compatible primitives (null, bool, int, float, str, list, dict) are allowed."
        )


def freeze_payload(val: Any) -> Any:
    """Recursively convert validated payload to immutable MappingProxyType and tuple nodes."""
    if isinstance(val, Mapping):
        return MappingProxyType({k: freeze_payload(v) for k, v in val.items()})
    if isinstance(val, (list, tuple)):
        return tuple(freeze_payload(item) for item in val)
    return val


def thaw_payload(val: Any) -> Any:
    """Recursively convert immutable payload representation to JSON-native dict/list structures."""
    if isinstance(val, Mapping):
        return {k: thaw_payload(v) for k, v in val.items()}
    if isinstance(val, (tuple, list)):
        return [thaw_payload(item) for item in val]
    return val


def validate_event_payload(payload: Any) -> MappingProxyType[str, Any]:
    """Validate event payload structure, enforcing security, bounds, and immutability.

    Validation Pipeline:
    1. Untrusted caller payload root check (must be a mapping/dict).
    2. Recursive JSON compatibility validation, finite float check, and sensitive key denylist.
    3. Serialized UTF-8 JSON size bound check.
    4. Recursive immutable conversion (MappingProxyType for mappings, tuple for sequences).

    Returns:
        MappingProxyType[str, Any]: Recursively frozen, immutable payload.

    Raises:
        ValueError: If payload violates root structure, key denylist, bounds, or JSON-compatibility.
    """
    if payload is None:
        return MappingProxyType({})

    if not isinstance(payload, Mapping):
        raise ValueError("Event payload root must be a dictionary/mapping")

    # Step 1 & 2: Recursive structure and key validation
    _validate_payload_node(payload, current_depth=1)

    # Step 3: Serialized payload size check with strict non-standard float rejection
    raw_json_obj = thaw_payload(payload)
    try:
        serialized = json.dumps(raw_json_obj, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Event payload cannot be serialized to JSON: {err}") from err

    byte_size = len(serialized.encode("utf-8"))
    if byte_size > MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError(
            f"Serialized event payload size ({byte_size} bytes) exceeds limit "
            f"of {MAX_EVENT_PAYLOAD_BYTES} bytes"
        )

    # Step 4: Recursive immutable conversion
    frozen = freeze_payload(payload)
    assert isinstance(frozen, MappingProxyType)
    return frozen
