"""Low-level RabbitMQ broker readiness probe primitive.

Executes a bounded connection check against RabbitMQ without exposing credentials.
"""

import asyncio
import os

import structlog

logger = structlog.get_logger(__name__)


async def check_broker_ready(
    broker_url: str | None = None,
    timeout_seconds: float = 2.0,
) -> bool:
    """Execute a low-level RabbitMQ readiness probe with bounded timeout.

    Args:
        broker_url: Optional explicit RabbitMQ connection string.
        timeout_seconds: Maximum time allowed for connection. Defaults to 2.0s.

    Returns:
        bool: True if connection succeeds, False otherwise.
        Never raises exceptions and never leaks credentials.
    """
    url = (
        broker_url
        or os.getenv("E7_RABBITMQ_URL")
        or os.getenv("RABBITMQ_URL")
        or ""
    ).strip()
    if not url:
        return False

    try:
        import aio_pika

        async with asyncio.timeout(timeout_seconds):
            conn = await aio_pika.connect_robust(url=url, timeout=timeout_seconds)
            await conn.close()
            return True
    except TimeoutError:
        logger.warning("broker_readiness_probe_timed_out", timeout_seconds=timeout_seconds)
        return False
    except Exception as exc:
        logger.warning("broker_readiness_probe_failed", error_type=type(exc).__name__)
        return False

