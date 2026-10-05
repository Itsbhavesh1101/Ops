"""Context Builder package for Task Intelligence (Phase 2B)."""

from app.task_intelligence.context.adapter import (
    AdapterAccessDeniedError,
    ContextSourceAdapter,
    DatabaseAccessBlockedError,
    ProductionMySQLAdapter,
    SyntheticContextAdapter,
)
from app.task_intelligence.context.allowlist import (
    FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS,
    is_field_allowed_for_planner,
)
from app.task_intelligence.context.builder import ContextBuilder
from app.task_intelligence.context.models import (
    AssemblyStatus,
    ContextAssemblyRequest,
    ContextAssemblyResult,
    ContextCategory,
    ModelContext,
    ValidationContext,
)

__all__ = [
    "AdapterAccessDeniedError",
    "AssemblyStatus",
    "ContextAssemblyRequest",
    "ContextAssemblyResult",
    "ContextBuilder",
    "ContextCategory",
    "ContextSourceAdapter",
    "DatabaseAccessBlockedError",
    "FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS",
    "ModelContext",
    "ProductionMySQLAdapter",
    "SyntheticContextAdapter",
    "ValidationContext",
    "is_field_allowed_for_planner",
]
