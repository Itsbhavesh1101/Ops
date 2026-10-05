"""Live Outbox Worker Container Verification.

Part of Phase 1 Foundation Task 1J.3C.
Strictly verifies the REAL Dockerized background worker runtime (app.worker) against:
- real PostgreSQL (e7-postgres) with isolated database 'operations_engine_worker_test'
- real RabbitMQ (e7-rabbitmq) with isolated vhost 'e7_worker_integration'
- real app.worker entrypoint (python -m app.worker)
- real OutboxDispatcherRunner
- real RabbitMqEventPublisher

Lifecycle & Invariants Verified:
1. Docker worker image exists and builds.
2. Worker container starts with command 'python -m app.worker'.
3. Worker connects to postgres:5432 and rabbitmq:5672 inside Compose network.
4. Synthetic outbox event is staged into isolated DB via OutboxRepository.
5. Real worker claims the outbox event.
6. Real worker publishes it to RabbitMQ exchange.
7. Outbox row is settled: published_at populated, claim cleared, dispatch_attempt_count == 1.
8. Observer queue receives exact event and AMQP metadata.
9. Worker remains outbox-only (zero consumer queues, zero inbox rows).
10. SIGTERM via 'docker stop' triggers graceful shutdown (exit code 0, no SIGKILL).
11. Container does not auto-restart.
"""

import asyncio
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import aio_pika
import pytest
from aio_pika.exceptions import QueueEmpty
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from tests.integration.worker.conftest import (
    AMQP_HOST_URL,
    REPO_ROOT,
    WORKER_EXCHANGE,
    WORKER_OBSERVER_QUEUE,
    WORKER_VHOST,
)


def _create_synthetic_worker_envelope(
    event_type: str = "worker.integration.test",
) -> EventEnvelope:
    """Create a strictly valid synthetic EventEnvelope for outbox worker verification."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"synthetic-tenant-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            subject_type="worker_task",
            subject_id="WKR-001",
            payload={"worker_test": "synthetic_payload_value", "attempt": 1},
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_outbox_worker_container_lifecycle(
    isolated_worker_db: str,
    isolated_worker_vhost: str,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    compose_override_file: Path,
    docker_env: dict[str, str],
) -> None:
    """Prove real Dockerized worker container dispatches outbox events and shuts down cleanly."""
    container_name = "e7-worker"
    routing_key = "worker.integration.test"

    # Step 1: Ensure image is present
    img_proc = subprocess.run(
        ["docker", "image", "inspect", "operations-engine-worker:latest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert img_proc.returncode == 0, "operations-engine-worker:latest image must exist"

    # Step 2: Set up test observer topology on RabbitMQ before worker starts
    amqp_conn = await aio_pika.connect_robust(AMQP_HOST_URL)
    try:
        channel = await amqp_conn.channel()
        exchange = await channel.declare_exchange(
            name=WORKER_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        observer_queue = await channel.declare_queue(
            WORKER_OBSERVER_QUEUE,
            durable=True,
            auto_delete=False,
        )
        await observer_queue.bind(exchange=exchange, routing_key=routing_key)

        # Step 3: Insert synthetic outbox event into isolated database
        envelope = _create_synthetic_worker_envelope(event_type=routing_key)
        async with uow_factory() as uow:
            outbox_repo = OutboxRepository(uow.session)
            await outbox_repo.enqueue(envelope)
            await uow.commit()

        # Step 4: Verify initial pending outbox state in isolated DB
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT event_id, published_at, claim_token, claimed_at, "
                    "dispatch_attempt_count FROM outbox_events WHERE event_id = :eid"
                ),
                {"eid": envelope.event_id},
            )
            initial_row = result.mappings().one()
            assert initial_row["event_id"] == envelope.event_id
            assert initial_row["published_at"] is None, "published_at must initially be NULL"
            assert initial_row["claim_token"] is None, "claim_token must initially be NULL"
            assert initial_row["claimed_at"] is None, "claimed_at must initially be NULL"
            assert initial_row["dispatch_attempt_count"] == 0, "attempt count must initially be 0"

        # Step 5: Start the real worker container via Docker Compose
        # Ensure any leftover container is removed first
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)

        compose_cmd = [
            "docker",
            "compose",
            "-f",
            str(REPO_ROOT / "docker-compose.yml"),
            "-f",
            str(compose_override_file),
            "--profile",
            "worker-runtime",
            "up",
            "-d",
            "--no-deps",
            "worker",
        ]
        start_proc = subprocess.run(
            compose_cmd,
            cwd=str(REPO_ROOT),
            env=docker_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert start_proc.returncode == 0, (
            f"Failed to start worker container: {start_proc.stderr}\n{start_proc.stdout}"
        )

        try:
            # Step 6: Verify container is created, running, and command is python -m app.worker
            inspect_proc = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert inspect_proc.returncode == 0, f"Cannot inspect {container_name}"
            c_info = json.loads(inspect_proc.stdout.strip())

            assert c_info["State"]["Running"] is True, "Worker container must be running"
            cmd_list = c_info.get("Config", {}).get("Cmd", [])
            assert "python" in cmd_list and "-m" in cmd_list and "app.worker" in cmd_list, (
                f"Worker command must be python -m app.worker, got: {cmd_list}"
            )

            # Step 7: Bounded polling for published message from observer queue
            received_message: aio_pika.IncomingMessage | None = None
            max_wait_seconds = 20.0
            start_time = time.monotonic()

            while (time.monotonic() - start_time) < max_wait_seconds:
                try:
                    msg = await observer_queue.get(fail=False)
                    if msg is not None:
                        received_message = msg
                        break
                except (QueueEmpty, TimeoutError):
                    pass
                await asyncio.sleep(0.5)

            if received_message is None:
                log_out = subprocess.run(
                    ["docker", "logs", container_name],
                    capture_output=True,
                    text=True,
                ).stdout
                pytest.fail(
                    f"Worker failed to publish outbox event to RabbitMQ within "
                    f"{max_wait_seconds}s. Logs:\n{log_out}"
                )

            # Step 8: Verify received message content and AMQP metadata
            async with received_message.process(requeue=False):
                body_json = json.loads(received_message.body.decode("utf-8"))
                assert body_json["event_id"] == str(envelope.event_id)
                assert body_json["event_type"] == envelope.event_type
                assert body_json["event_version"] == envelope.event_version
                assert body_json["tenant_id"] == envelope.tenant_id
                assert body_json["correlation_id"] == envelope.correlation_id
                assert body_json["payload"] == dict(envelope.payload)

                # AMQP properties
                assert received_message.content_type == "application/json", (
                    f"Unexpected content_type: {received_message.content_type}"
                )
                assert received_message.content_encoding in (
                    "utf-8",
                    "utf8",
                ), f"Unexpected content_encoding: {received_message.content_encoding}"
                assert received_message.message_id == str(envelope.event_id), (
                    f"Unexpected message_id: {received_message.message_id}"
                )
                assert received_message.type == envelope.event_type, (
                    f"Unexpected type: {received_message.type}"
                )

            # Step 9: Verify outbox row settlement in isolated PostgreSQL
            async with session_factory() as session:
                settled_res = await session.execute(
                    text(
                        "SELECT event_id, published_at, claim_token, claimed_at, "
                        "dispatch_attempt_count FROM outbox_events WHERE event_id = :eid"
                    ),
                    {"eid": envelope.event_id},
                )
                settled_row = settled_res.mappings().one()
                assert settled_row["published_at"] is not None, "published_at must be populated"
                assert settled_row["claim_token"] is None, (
                    "claim_token must be cleared after publication"
                )
                assert settled_row["claimed_at"] is None, "claimed_at must be cleared"
                assert settled_row["dispatch_attempt_count"] == 1, (
                    f"dispatch_attempt_count must be 1 on first success, "
                    f"got {settled_row['dispatch_attempt_count']}"
                )

            # Step 10: Verify worker process remains running after successful dispatch
            inspect_after = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{.State.Running}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert inspect_after.stdout.strip() == "true", "Worker must remain running"

            # Step 11: Verify consumer-runtime exclusion
            # A) Check that no consumer queues were declared by the worker
            q_proc = subprocess.run(
                [
                    "docker",
                    "exec",
                    "e7-rabbitmq",
                    "rabbitmqctl",
                    "list_queues",
                    "-p",
                    WORKER_VHOST,
                    "name",
                    "--silent",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            declared_queues = [q.strip() for q in q_proc.stdout.splitlines() if q.strip()]
            assert declared_queues == [WORKER_OBSERVER_QUEUE], (
                f"Only observer queue should exist, found extra queues: {declared_queues}"
            )

            # B) Verify zero inbox rows exist in isolated DB
            async with session_factory() as session:
                inbox_res = await session.execute(text("SELECT COUNT(*) FROM inbox_messages"))
                inbox_count = inbox_res.scalar()
                assert inbox_count == 0, f"Inbox should be empty, found {inbox_count} rows"

            # Step 12: SIGTERM Live Graceful Shutdown Verification
            stop_start = time.monotonic()
            stop_proc = subprocess.run(
                ["docker", "stop", "--time", "10", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            stop_duration = time.monotonic() - stop_start

            assert stop_proc.returncode == 0, (
                f"docker stop failed: {stop_proc.stderr}\n{stop_proc.stdout}"
            )
            # Verify shutdown occurred well before the 10s timeout
            assert stop_duration < 10.0, (
                f"Worker graceful shutdown took {stop_duration:.2f}s, "
                f"exceeding 10s timeout (possible SIGKILL)"
            )

            # Step 13: Verify container exit code == 0, stopped state, no auto-restart
            inspect_stopped = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_name,
                    "--format",
                    "{{.State.ExitCode}} | {{.State.Status}} | {{.State.Running}}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            exit_code, status, running = [s.strip() for s in inspect_stopped.stdout.split("|")]
            assert exit_code == "0", f"Worker exited with non-zero exit code: {exit_code}"
            assert status == "exited", f"Worker status should be exited, got {status}"
            assert running == "false", "Worker should not be running"

            # Step 14: Inspect shutdown logs for clean exit
            logs_proc = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            logs = logs_proc.stdout + logs_proc.stderr
            assert "Traceback" not in logs, f"Traceback found in worker logs:\n{logs}"

        finally:
            # Cleanup worker container
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)

    finally:
        await amqp_conn.close()
