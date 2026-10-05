"""Low-level database readiness probe primitive.

Part of Phase 1 Foundation Task 1D.
Executes a minimal SELECT 1 probe against PostgreSQL with bounded timeout,
ensuring zero business table dependencies and zero credential leakage.
"""

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)


async def check_database_ready(
    engine: AsyncEngine,
    timeout_seconds: float = 3.0,
) -> bool:
    """Execute a low-level database readiness probe using SELECT 1.

    Args:
        engine: Active SQLAlchemy AsyncEngine instance.
        timeout_seconds: Maximum time allowed for connection and query execution.

    Returns:
        bool: True if connection succeeds and SELECT 1 returns 1, False otherwise.
        Never raises exceptions and never exposes database connection strings or errors to callers.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                val = result.scalar()
                return val == 1
    except TimeoutError:
        logger.warning(
            "database_readiness_probe_timed_out",
            timeout_seconds=timeout_seconds,
        )
        return False
    except Exception as exc:
        logger.warning(
            "database_readiness_probe_failed",
            error_type=type(exc).__name__,
        )
        return False
