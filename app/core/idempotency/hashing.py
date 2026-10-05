"""Deterministic cryptographic hashing and validation helpers for idempotency.

Part of Phase 1 Foundation Task 1H.1.
Provides:
- validate_raw_idempotency_key: validates incoming client key before hashing.
- digest_idempotency_key: computes deterministic SHA-256 digest of client key.
- fingerprint_bytes: computes deterministic SHA-256 fingerprint of payload bytes.

Invariants:
- Uses hashlib.sha256 strictly (no MD5, no SHA-1, no Python hash(), no salt).
- Input bytes only for fingerprints; zero dict/JSON serialization utilities.
- Empty payload bytes (b"") are valid and hash deterministically.
"""

import hashlib
from typing import Any

from app.core.idempotency.contracts import (
    IdempotencyKeyDigest,
    RequestFingerprint,
)

MAX_RAW_KEY_LENGTH: int = 255


def validate_raw_idempotency_key(raw_key: Any) -> str:
    """Validate raw incoming client idempotency key before hashing.

    Invariants:
    - Must be a string.
    - Must not be empty.
    - Must not contain leading or trailing whitespace.
    - Length bounded to <= 255 characters.
    - Must not contain ASCII control characters.
    - Opaque: does not enforce UUID, ULID, or numeric formats.
    - Does not modify or silently normalize the input string.
    """
    if not isinstance(raw_key, str):
        raise TypeError(f"Raw idempotency key must be a string, got '{type(raw_key).__name__}'")
    if not raw_key:
        raise ValueError("Raw idempotency key must not be empty")
    for ch in raw_key:
        code = ord(ch)
        if code < 32 or code == 127:
            raise ValueError(
                f"Raw idempotency key contains forbidden control character (ASCII {code})"
            )
    if raw_key.strip() != raw_key:
        raise ValueError("Raw idempotency key must not contain leading or trailing whitespace")
    if len(raw_key) > MAX_RAW_KEY_LENGTH:
        raise ValueError(
            f"Raw idempotency key exceeds maximum length of {MAX_RAW_KEY_LENGTH} characters "
            f"(got {len(raw_key)})"
        )
    return raw_key


def digest_idempotency_key(raw_key: str) -> IdempotencyKeyDigest:
    """Compute deterministic SHA-256 digest of raw idempotency key.

    Invariants:
    - Validates raw key first.
    - Encodes raw key strictly as UTF-8.
    - Computes hashlib.sha256 digest without salt.
    - Returns fixed-width 64-character lowercase hex IdempotencyKeyDigest.
    """
    validated_key = validate_raw_idempotency_key(raw_key)
    digest_hex = hashlib.sha256(validated_key.encode("utf-8")).hexdigest().lower()
    return IdempotencyKeyDigest(digest_hex)


def fingerprint_bytes(data: bytes) -> RequestFingerprint:
    """Compute deterministic SHA-256 fingerprint from exact payload bytes.

    Invariants:
    - Accepts bytes only (rejects str, dict, and other types).
    - Empty bytes (b"") are valid and produce canonical SHA-256 of empty input.
    - Computes hashlib.sha256 digest without salt.
    - Returns fixed-width 64-character lowercase hex RequestFingerprint.
    """
    if not isinstance(data, bytes):
        raise TypeError(f"Payload data must be bytes, got '{type(data).__name__}'")
    digest_hex = hashlib.sha256(data).hexdigest().lower()
    return RequestFingerprint(digest_hex)
