"""Request correlation tracking module for the IR Infotech E-7 Operations Engine.

Manages request-scoped correlation identifiers via Python contextvars, providing
strict input sanitization to prevent log injection and header manipulation attacks.
"""

import re
import uuid
from contextvars import ContextVar

# Allowed characters: alphanumeric, hyphens, underscores, dots, colons (strict end-of-string \\Z)
CORRELATION_ID_REGEX = re.compile(r"^[A-Za-z0-9_.:-]+\Z")
MAX_CORRELATION_ID_LENGTH = 128

# Request-scoped context variable holding the current correlation ID
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def validate_correlation_id(raw_id: str | None) -> str:
    """Validate and sanitize an incoming correlation ID.

    If the provided identifier is absent, whitespace-only, exceeds 128 characters,
    or contains characters outside the allowed safe set [A-Za-z0-9_.:-], a new UUID4
    identifier is generated to prevent log/header injection.
    """
    if not raw_id:
        return str(uuid.uuid4())

    if len(raw_id) > MAX_CORRELATION_ID_LENGTH:
        return str(uuid.uuid4())

    if not CORRELATION_ID_REGEX.match(raw_id):
        return str(uuid.uuid4())

    return raw_id


def get_correlation_id() -> str | None:
    """Retrieve the current active correlation ID from context."""
    return correlation_id_ctx.get()


def set_correlation_id(correlation_id: str) -> None:
    """Explicitly set the correlation ID in context."""
    correlation_id_ctx.set(correlation_id)
