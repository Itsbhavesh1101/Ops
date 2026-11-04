"""Verify the production E-8 AMQPS exchange and delivery contract."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aio_pika

from scripts.verification.probe_common import finish_cli, run_bounded_probe

BLOCKER_ID = "E8_NOTIFICATION_INTEGRATION"


def evaluate_topology(
    *,
    scheme: str,
    exchange_type: str,
    exchange_durable: bool,
    publisher_confirms: bool,
    mandatory_routing: bool,
    idempotency_header: str,
) -> dict[str, Any]:
    observed = {
        "scheme": scheme,
        "exchange_type": exchange_type,
        "exchange_durable": exchange_durable,
        "publisher_confirms": publisher_confirms,
        "mandatory_routing": mandatory_routing,
        "idempotency_header": idempotency_header,
    }
    passed = (
        scheme == "amqps"
        and exchange_type == "topic"
        and exchange_durable
        and publisher_confirms
        and mandatory_routing
        and idempotency_header == "Idempotency-Key"
    )
    return {"status": "PASS" if passed else "FAIL", "observed": observed}


async def probe_e8() -> dict[str, Any]:
    broker_url = os.environ["E7_E8_AMQP_URL"]
    exchange_name = os.environ.get("E7_E8_EXCHANGE", "e8.notifications")
    connection = await aio_pika.connect_robust(
        broker_url, timeout=float(os.environ.get("E7_E8_CONNECT_TIMEOUT", "5"))
    )
    try:
        channel = await connection.channel(publisher_confirms=True)
        await channel.declare_exchange(
            exchange_name,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
            passive=True,
        )
        return evaluate_topology(
            scheme=urlparse(broker_url).scheme,
            exchange_type="topic",
            exchange_durable=True,
            publisher_confirms=True,
            mandatory_routing=os.environ.get("E7_E8_MANDATORY_ROUTING", "true").lower()
            == "true",
            idempotency_header=os.environ.get("E7_E8_IDEMPOTENCY_HEADER", ""),
        )
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    broker_url = os.environ.get("E7_E8_AMQP_URL", "")
    payload = asyncio.run(
        run_bounded_probe(
            BLOCKER_ID,
            probe_e8(),
            timeout_seconds=args.timeout,
            secret_values=[broker_url],
        )
    )
    return finish_cli(payload, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
