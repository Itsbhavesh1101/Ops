"""Live Docker Observability Integration Verification (Task 1K.6 / Phase-1 Stage D).

Verifies the end-to-end containerized observability pipeline:
1. Dockerized API (e7-api) and Worker (e7-worker) with E7_OTEL_ENABLED=true
2. OpenTelemetry Collector (e7-otel-collector) receiving OTLP/HTTP on :4318
3. Prometheus (e7-prometheus) scraping Collector metrics on :8889
4. Live API trace verification:
   - Server span generated with service.name = e7-api
   - e7.correlation_id correctly attached
   - Structured API logs contain correlation_id, trace_id, span_id
   - Zero leak of Authorization/Cookie/body data
5. Health/readiness probe exclusion verification:
   - /api/v1/health and /api/v1/ready return 200 OK
   - Probe spans excluded from Collector traces
6. Live worker outbox telemetry verification:
   - Worker claims and dispatches synthetic outbox event
   - Event published to broker and row settled in DB
   - e7.outbox.dispatch span emitted with service.name = e7-worker
   - Zero event payload in span attributes
7. Prometheus verification:
   - Target is UP
   - Outbox metric family exists
   - Zero forbidden high-cardinality labels (event_type, tenant_id, etc.)
8. Graceful shutdown verification:
   - Clean shutdown with exit code 0
"""

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import aio_pika
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "55432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
DB_NAME = os.environ.get("POSTGRES_DB", "operations_engine")

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5673"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "guest")

API_PORT = int(os.environ.get("PORT", "8000"))
COLLECTOR_HTTP_PORT = int(os.environ.get("OTEL_COLLECTOR_HTTP_PORT", "4318"))
PROMETHEUS_PORT = int(os.environ.get("PROMETHEUS_PORT", "9090"))

API_BASE_URL = f"http://localhost:{API_PORT}"
PROMETHEUS_BASE_URL = f"http://localhost:{PROMETHEUS_PORT}"
DB_ASYNC_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{DB_NAME}"
)
AMQP_HOST_URL = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"


def _create_synthetic_outbox_envelope(
    event_type: str = "observability.integration.test",
) -> EventEnvelope:
    """Create a valid synthetic EventEnvelope for live telemetry verification."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"test-tenant-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            subject_type="test_subject",
            subject_id="SUB-001",
            payload={"secret_field": "do_not_leak_this", "step": "verification"},
        )
    )


def _get_docker_logs(container_name: str) -> str:
    """Retrieve combined stdout and stderr from container logs."""
    proc = subprocess.run(
        ["docker", "logs", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def _is_docker_available() -> bool:
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, check=False)
        return proc.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _is_docker_available(), reason="Docker daemon is not running")
@pytest.mark.asyncio
async def test_live_docker_observability_pipeline() -> None:
    """End-to-end verification of API, Worker, Collector, and Prometheus."""
    # Step 1: Ensure Postgres and RabbitMQ containers are running and healthy
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", "e7-postgres"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "healthy" in res.stdout, f"e7-postgres must be healthy, got: {res.stdout}"

    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", "e7-rabbitmq"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "healthy" in res.stdout, f"e7-rabbitmq must be healthy, got: {res.stdout}"

    # Step 2: Start Collector and Prometheus under observability profile
    # and API and Worker with E7_OTEL_ENABLED=true
    env = os.environ.copy()
    env["E7_OTEL_ENABLED"] = "true"
    env["PORT"] = str(API_PORT)
    env["POSTGRES_PORT"] = str(POSTGRES_PORT)
    env["RABBITMQ_PORT"] = str(RABBITMQ_PORT)
    env["OTEL_COLLECTOR_HTTP_PORT"] = str(COLLECTOR_HTTP_PORT)
    env["PROMETHEUS_PORT"] = str(PROMETHEUS_PORT)
    env["E7_OTEL_METRIC_EXPORT_INTERVAL_SECONDS"] = "2.0"

    up_cmd = [
        "docker",
        "compose",
        "--profile",
        "worker-runtime",
        "--profile",
        "observability",
        "up",
        "-d",
        "--no-deps",
        "otel-collector",
        "prometheus",
        "api",
        "worker",
    ]
    up_proc = subprocess.run(up_cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    assert up_proc.returncode == 0, f"docker compose up failed: {up_proc.stderr}"

    amqp_conn: aio_pika.abc.AbstractConnection | None = None
    try:
        # Step 3: Wait for services to be ready
        async with httpx.AsyncClient(timeout=10.0) as client:
            api_ready = False
            for _ in range(30):
                try:
                    resp = await client.get(f"{API_BASE_URL}/api/v1/health")
                    if resp.status_code == 200:
                        api_ready = True
                        break
                except Exception:
                    await asyncio.sleep(1.0)
            assert api_ready, "API service did not become healthy within 30s"

            # Check Prometheus readiness
            prom_ready = False
            for _ in range(30):
                try:
                    resp = await client.get(f"{PROMETHEUS_BASE_URL}/-/ready")
                    if resp.status_code == 200:
                        prom_ready = True
                        break
                except Exception:
                    await asyncio.sleep(1.0)
            assert prom_ready, "Prometheus did not become ready within 30s"

        # Step 4: LIVE API TRACE TEST
        test_correlation_id = f"trace-test-{uuid4()}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{API_BASE_URL}/?email=user@example.com&token=secret-value&search=private",
                headers={
                    "X-Correlation-ID": test_correlation_id,
                    "Authorization": "Bearer super-secret-token",
                    "Cookie": "session=secret-session-id",
                },
            )
            assert resp.status_code == 200
            assert resp.json().get("status") == "online"

        # Wait briefly for OTLP batch processor to flush (timeout=1s)
        await asyncio.sleep(2.5)

        # Step 5: Check API structured log for correlation_id, trace_id, span_id
        api_logs = _get_docker_logs("e7-api")
        assert test_correlation_id in api_logs, "Effective correlation ID must appear in API logs"
        assert "trace_id" in api_logs, "Structured API log must contain trace_id"
        assert "span_id" in api_logs, "Structured API log must contain span_id"

        # Step 6: Check Collector logs for API trace evidence
        # OpenTelemetry Python SDK BatchSpanProcessor default schedule_delay_millis is 5000ms.
        # Poll Collector logs up to 12 seconds for the trace to arrive.
        collector_logs = ""
        for _ in range(12):
            collector_logs = _get_docker_logs("e7-otel-collector")
            if (
                "service.name: Str(e7-api)" in collector_logs
                and test_correlation_id in collector_logs
            ):
                break
            await asyncio.sleep(1.0)

        assert "service.name: Str(e7-api)" in collector_logs, (
            "Collector must receive trace with service.name = e7-api"
        )
        assert test_correlation_id in collector_logs, (
            f"Collector trace must contain e7.correlation_id: {test_correlation_id}"
        )
        # Privacy contract validation: No Authorization, Cookie, or query values in trace attributes
        assert "super-secret-token" not in collector_logs, (
            "Authorization token must NOT appear in Collector trace output"
        )
        assert "secret-session-id" not in collector_logs, (
            "Cookie value must NOT appear in Collector trace output"
        )
        assert "user@example.com" not in collector_logs, (
            "Query parameter email value must NOT appear in Collector trace output"
        )
        assert "secret-value" not in collector_logs, (
            "Query parameter token value must NOT appear in Collector trace output"
        )
        assert "private" not in collector_logs, (
            "Query parameter search value must NOT appear in Collector trace output"
        )

        # Step 7: HEALTH PROBE EXCLUSION TEST
        # Query health probes
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp_h = await client.get(f"{API_BASE_URL}/api/v1/health")
            assert resp_h.status_code == 200
            resp_r = await client.get(f"{API_BASE_URL}/api/v1/ready")
            assert resp_r.status_code == 200

        await asyncio.sleep(2.0)
        # Collector logs must NOT contain traces for health or ready
        fresh_collector_logs = _get_docker_logs("e7-otel-collector")
        assert "GET /api/v1/health" not in fresh_collector_logs, (
            "Health probe /api/v1/health must be excluded from traces"
        )
        assert "GET /api/v1/ready" not in fresh_collector_logs, (
            "Readiness probe /api/v1/ready must be excluded from traces"
        )

        # Step 8: LIVE WORKER OUTBOX TELEMETRY TEST
        # Set up an observer queue on RabbitMQ so mandatory routing succeeds
        amqp_conn = await aio_pika.connect_robust(AMQP_HOST_URL)
        channel = await amqp_conn.channel()
        exchange = await channel.declare_exchange(
            name="events",
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        observer_queue = await channel.declare_queue(
            "e7.observability.test.observer",
            durable=True,
            auto_delete=True,
        )
        await observer_queue.bind(
            exchange=exchange,
            routing_key="observability.integration.test",
        )

        engine = create_async_engine(DB_ASYNC_URL)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        envelope = _create_synthetic_outbox_envelope()
        async with session_factory() as session:
            outbox_repo = OutboxRepository(session)
            await outbox_repo.enqueue(envelope)
            await session.commit()

        # Wait for worker to claim and publish event (poll interval is 1.0s)
        settled = False
        for _ in range(20):
            async with session_factory() as session:
                res = await session.execute(
                    text(
                        "SELECT published_at, claim_token FROM outbox_events WHERE event_id = :eid"
                    ),
                    {"eid": envelope.event_id},
                )
                row = res.mappings().one_or_none()
                if row and row["published_at"] is not None:
                    settled = True
                    break
            await asyncio.sleep(1.0)

        assert settled, f"Worker did not settle outbox event {envelope.event_id} within 20s"
        await engine.dispose()

        # Poll Collector logs for Worker telemetry (up to 12s)
        worker_collector_logs = ""
        for _ in range(12):
            worker_collector_logs = _get_docker_logs("e7-otel-collector")
            if (
                "service.name: Str(e7-worker)" in worker_collector_logs
                and "e7.outbox.dispatch" in worker_collector_logs
            ):
                break
            await asyncio.sleep(1.0)

        assert "service.name: Str(e7-worker)" in worker_collector_logs, (
            "Collector must receive telemetry with service.name = e7-worker"
        )
        assert "e7.outbox.dispatch" in worker_collector_logs, (
            "Collector must receive e7.outbox.dispatch span"
        )
        # Verify event payload is NOT attached to spans
        assert "do_not_leak_this" not in worker_collector_logs, (
            "Event payload must NOT be attached to telemetry spans"
        )

        # Step 9: PROMETHEUS SCRAPE AND CARDINALITY VERIFICATION
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check target is healthy
            targets_resp = await client.get(f"{PROMETHEUS_BASE_URL}/api/v1/targets")
            assert targets_resp.status_code == 200
            targets_data = targets_resp.json()
            active_targets = targets_data.get("data", {}).get("activeTargets", [])
            assert any(t.get("health") == "up" for t in active_targets), (
                f"Prometheus targets must show UP: {active_targets}"
            )

            # Query e7.outbox.dispatch.attempts metric family
            # Note: in Prometheus OTLP exporter, dots become underscores:
            # e7_outbox_dispatch_attempts_total or e7_outbox_dispatch_attempts
            # Poll Prometheus for metrics to appear (scrape_interval=2s, export_interval=2s)
            results = []
            for _ in range(15):
                query_resp = await client.get(
                    f"{PROMETHEUS_BASE_URL}/api/v1/query",
                    params={"query": "{__name__=~'e7_outbox_dispatch.*'}"},
                )
                if query_resp.status_code == 200:
                    query_data = query_resp.json()
                    results = query_data.get("data", {}).get("result", [])
                    if results:
                        break
                await asyncio.sleep(1.0)

            assert len(results) > 0, "Prometheus should have scraped e7_outbox_dispatch metrics"

            # Check forbidden cardinality labels on all returned metrics
            forbidden_labels = {
                "event_type",
                "tenant_id",
                "event_id",
                "job_id",
                "correlation_id",
                "trace_id",
                "span_id",
                "error",
                "exception",
                "message",
                "path",
                "url",
            }
            allowed_error_categories = {
                "publish_error",
                "timeout",
                "database_error",
                "unexpected",
            }
            for metric_result in results:
                metric_labels = set(metric_result.get("metric", {}).keys())
                forbidden_found = metric_labels.intersection(forbidden_labels)
                assert not forbidden_found, (
                    f"Forbidden high-cardinality labels found in Prometheus: {forbidden_found}"
                )
                if "error_category" in metric_labels:
                    cat_val = metric_result["metric"]["error_category"]
                    assert cat_val in allowed_error_categories, (
                        f"Unapproved error_category '{cat_val}' found in metric: {metric_result}"
                    )

    finally:
        # Step 10: GRACEFUL SHUTDOWN AND ORDERING VERIFICATION
        # Stop worker and API gracefully
        stop_proc = subprocess.run(
            ["docker", "compose", "stop", "worker", "api"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert stop_proc.returncode == 0, f"docker compose stop failed: {stop_proc.stderr}"

        # Verify worker exit code is 0 (graceful shutdown)
        inspect_res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", "e7-worker"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert inspect_res.stdout.strip() == "0", (
            f"Worker must exit with 0 on graceful SIGTERM, got {inspect_res.stdout}"
        )

        # Verify worker shutdown sequence in logs
        worker_logs = _get_docker_logs("e7-worker")
        assert "worker_stopped_cleanly" in worker_logs, (
            "Worker logs must show runner stopped cleanly"
        )
        assert "telemetry_runtime_shut_down" in worker_logs, (
            "Worker logs must show telemetry shutdown flush"
        )
        assert "database_manager_disposed" in worker_logs, (
            "Worker logs must show database manager disposed"
        )

        idx_runner = worker_logs.index("worker_stopped_cleanly")
        idx_telemetry = worker_logs.index("telemetry_runtime_shut_down")
        idx_db = worker_logs.index("database_manager_disposed")
        assert idx_runner < idx_telemetry < idx_db, (
            "Worker shutdown order must be: runner stop -> telemetry shutdown -> DB disposal"
        )

        # Stop observability containers
        subprocess.run(
            ["docker", "compose", "stop", "otel-collector", "prometheus"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        # Remove stopped test containers without touching volumes
        subprocess.run(
            ["docker", "rm", "-f", "e7-api", "e7-worker", "e7-otel-collector", "e7-prometheus"],
            capture_output=True,
            text=True,
            check=False,
        )

        if amqp_conn is not None and not amqp_conn.is_closed:
            await amqp_conn.close()
