"""Live broker health, version, and native topology verification.

Part of Phase 1 Foundation Task 1J.2B.
Verifies broker connectivity, versions, and inspects native broker topology via rabbitmqctl.
"""

import subprocess
from typing import Any

import aio_pika
import pytest
from pydantic import SecretStr

from tests.integration.rabbitmq.conftest import (
    INTEGRATION_BINDING_KEYS,
    INTEGRATION_DLQ,
    INTEGRATION_DLQ_ROUTING_KEY,
    INTEGRATION_DLX,
    INTEGRATION_EXCHANGE,
    INTEGRATION_QUEUE,
    RABBITMQ_VHOST,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rabbitmq_broker_health_and_version(
    broker_service_check: dict[str, Any],
) -> None:
    """Verify live RabbitMQ broker container reaches healthy state and report versions."""
    # 1. Broker ping check
    ping_proc = subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmq-diagnostics", "-q", "ping"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Ping succeeded" in ping_proc.stdout, f"Ping failed: {ping_proc.stdout}"

    # 2. Broker version check
    version_proc = subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "version"],
        capture_output=True,
        text=True,
        check=True,
    )
    version = version_proc.stdout.strip()
    assert version.startswith("3."), f"Expected RabbitMQ 3.x, got: {version}"

    # 3. Erlang version check
    erlang_proc = subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmq-diagnostics", "erlang_version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Erlang" in erlang_proc.stdout or "OTP" in erlang_proc.stdout, (
        f"Unexpected Erlang output: {erlang_proc.stdout}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_broker_topology_declaration_and_inspection(
    rabbitmq_url: SecretStr,
    isolated_vhost: str,
) -> None:
    """Declare canonical consumer topology and inspect native RabbitMQ state via rabbitmqctl."""
    # 1. Declare topology using aio_pika
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    try:
        channel = await conn.channel()

        # Declare main exchange
        main_exchange = await channel.declare_exchange(
            name=INTEGRATION_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        # Declare DLX
        dlx = await channel.declare_exchange(
            name=INTEGRATION_DLX,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # Declare DLQ
        dlq = await channel.declare_queue(
            name=INTEGRATION_DLQ,
            durable=True,
            exclusive=False,
            auto_delete=False,
        )
        await dlq.bind(exchange=dlx, routing_key=INTEGRATION_DLQ_ROUTING_KEY)

        # Declare main queue with DLX arguments
        main_queue = await channel.declare_queue(
            name=INTEGRATION_QUEUE,
            durable=True,
            exclusive=False,
            auto_delete=False,
            arguments={
                "x-dead-letter-exchange": INTEGRATION_DLX,
                "x-dead-letter-routing-key": INTEGRATION_DLQ_ROUTING_KEY,
            },
        )
        for key in INTEGRATION_BINDING_KEYS:
            await main_queue.bind(exchange=main_exchange, routing_key=key)

    finally:
        await conn.close()

    # 2. Inspect native broker state using rabbitmqctl inside container
    exchanges_proc = subprocess.run(
        [
            "docker",
            "exec",
            "e7-rabbitmq",
            "rabbitmqctl",
            "list_exchanges",
            "-p",
            RABBITMQ_VHOST,
            "name",
            "type",
            "durable",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    exchanges_out = exchanges_proc.stdout
    assert INTEGRATION_EXCHANGE in exchanges_out, f"Missing main exchange in {exchanges_out}"
    assert INTEGRATION_DLX in exchanges_out, f"Missing DLX exchange in {exchanges_out}"

    queues_proc = subprocess.run(
        [
            "docker",
            "exec",
            "e7-rabbitmq",
            "rabbitmqctl",
            "list_queues",
            "-p",
            RABBITMQ_VHOST,
            "name",
            "durable",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    queues_out = queues_proc.stdout
    assert INTEGRATION_QUEUE in queues_out, f"Missing main queue in {queues_out}"
    assert INTEGRATION_DLQ in queues_out, f"Missing DLQ in {queues_out}"

    bindings_proc = subprocess.run(
        [
            "docker",
            "exec",
            "e7-rabbitmq",
            "rabbitmqctl",
            "list_bindings",
            "-p",
            RABBITMQ_VHOST,
            "source_name",
            "destination_name",
            "routing_key",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    bindings_out = bindings_proc.stdout
    assert INTEGRATION_QUEUE in bindings_out, f"Missing queue bindings in {bindings_out}"
    assert INTEGRATION_DLQ in bindings_out, f"Missing DLQ bindings in {bindings_out}"
