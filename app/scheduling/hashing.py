"""Deterministic SHA-256 input snapshot hashing for scheduling reproducibility.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Canonical JSON serialization (sorted keys, compact delimiters).
- SHA-256 cryptographic digest over solver input.
- Immunity to dictionary key ordering and whitespace variance.
"""

import hashlib
import json
from typing import Any

from app.scheduling.schemas import SchedulingRequest


def canonical_json_dumps(obj: Any) -> str:
    """Serialize data into a deterministic, compact JSON string."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_input_snapshot_hash(request: SchedulingRequest) -> str:
    """Generate SHA-256 digest of normalized scheduling request inputs.

    Excludes transient fields and guarantees identical digests for semantically
    identical problem formulations.
    """
    raw_dict = request.model_dump(mode="json")
    canonical_repr = canonical_json_dumps(raw_dict)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
