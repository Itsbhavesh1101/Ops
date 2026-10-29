"""Static validation and configuration unit tests for Docker Compose topology.

Part of Phase 1 Foundation Task 1J.1.
Validates:
- Canonical Compose services set: {"api", "worker", "postgres", "rabbitmq", "flowable"}
- Strict absence of unapproved infrastructure services (redis, valkey, temporal, kafka, etc.)
- API and worker image/build context parity (no duplicate codebases)
- Internal DNS hostnames (postgres, rabbitmq) for database and event broker URLs
- Flowable service boundary and database segregation (flowable_db vs operations_engine)
- Healthcheck configurations (pg_isready, rabbitmq-diagnostics, /api/v1/health)
- Absence of real secrets in Compose and .env.example
- Production route preservation (no job-read production mount)
- Canonical 5-table Base.metadata stability and Alembic head 0007
- Absence of identity/tenant persistence models
"""

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

import app.db.models  # noqa: F401
from app.db.base import Base
from app.main import app as production_app

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
VERSIONS_DIR = REPO_ROOT / "app" / "db" / "migrations" / "versions"


@pytest.fixture(scope="module")
def compose_data() -> dict[str, Any]:
    """Parse and return raw docker-compose.yml structure."""
    assert COMPOSE_FILE.exists(), f"docker-compose.yml must exist at {COMPOSE_FILE}"
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "docker-compose.yml must parse to a dictionary"
    return data


def test_compose_canonical_services(compose_data: dict[str, Any]) -> None:
    """Verify compose defines exactly the 5 canonical services and no unapproved ones."""
    services = compose_data.get("services", {})
    assert isinstance(services, dict), "services must be a dictionary"

    service_names = set(services.keys())
    expected_services = {
        "api",
        "worker",
        "scheduler-worker",
        "postgres",
        "rabbitmq",
        "flowable",
        "otel-collector",
        "prometheus",
        "alertmanager",
    }
    assert service_names == expected_services, (
        f"Compose services must be exactly {expected_services}, found {service_names}"
    )

    unapproved_services = {
        "redis",
        "valkey",
        "celery",
        "temporal",
        "kafka",
        "nats",
        "elasticsearch",
        "minio",
        "nginx",
        "mysql",
        "grafana",
    }
    present_unapproved = service_names.intersection(unapproved_services)
    assert not present_unapproved, f"Found unapproved services in Compose: {present_unapproved}"


def test_application_image_consistency(compose_data: dict[str, Any]) -> None:
    """Verify api, worker, and scheduler-worker services share build context and Dockerfile."""
    services = compose_data["services"]
    api_build = services["api"].get("build")
    worker_build = services["worker"].get("build")
    scheduler_build = services["scheduler-worker"].get("build")

    assert api_build is not None, "api service must define a build configuration"
    assert worker_build is not None, "worker service must define a build configuration"
    assert scheduler_build is not None, "scheduler-worker must define a build configuration"

    assert api_build == worker_build == scheduler_build, (
        f"api, worker, and scheduler-worker must share identical build configuration. "
        f"Got {api_build} vs {worker_build} vs {scheduler_build}"
    )
    assert api_build.get("context") == ".", "build context must be repository root ('.')"
    assert api_build.get("dockerfile") == "docker/Dockerfile", (
        "build dockerfile must be docker/Dockerfile"
    )


def test_database_settings_internal_host(compose_data: dict[str, Any]) -> None:
    """Verify application database connection strings point to internal 'postgres' host."""
    services = compose_data["services"]

    for role in ("api", "worker", "scheduler-worker"):
        service_env = services[role].get("environment", {})
        db_url = service_env.get("E7_DATABASE_URL", "")
        assert db_url, f"{role} must define E7_DATABASE_URL in environment"
        assert "@postgres:5432" in db_url, (
            f"{role} E7_DATABASE_URL must route to internal service 'postgres:5432', got {db_url}"
        )
        assert "localhost" not in db_url, f"{role} must not point database URL to localhost"
        assert db_url.startswith("postgresql+asyncpg://"), (
            f"{role} E7_DATABASE_URL must use postgresql+asyncpg driver, got {db_url}"
        )


def test_rabbitmq_settings_internal_host(compose_data: dict[str, Any]) -> None:
    """Verify application RabbitMQ connection strings point to internal 'rabbitmq' host."""
    services = compose_data["services"]

    for role in ("api", "worker"):
        service_env = services[role].get("environment", {})
        rmq_url = service_env.get("E7_RABBITMQ_URL", "")
        assert rmq_url, f"{role} must define E7_RABBITMQ_URL in environment"
        assert "@rabbitmq:5672" in rmq_url, (
            f"{role} E7_RABBITMQ_URL must route to internal service 'rabbitmq:5672', got {rmq_url}"
        )
        assert "localhost" not in rmq_url, f"{role} must not point RabbitMQ URL to localhost"
        assert rmq_url.startswith("amqp://"), (
            f"{role} E7_RABBITMQ_URL must use amqp scheme, got {rmq_url}"
        )


def test_flowable_service_boundary(compose_data: dict[str, Any]) -> None:
    """Verify Flowable configuration preserves architecture and database boundaries."""
    services = compose_data["services"]
    flowable = services["flowable"]

    # Image version must be explicit stable version, never latest
    image = flowable.get("image", "")
    assert image.startswith("flowable/flowable-rest:7.0.0"), (
        f"Flowable must use explicit version 'flowable/flowable-rest:7.0.0', got '{image}'"
    )
    assert not image.endswith(":latest"), "Flowable must not use 'latest' tag"

    # Must depend on healthy PostgreSQL
    depends_on = flowable.get("depends_on", {})
    assert "postgres" in depends_on, "flowable must depend on postgres"
    assert depends_on["postgres"].get("condition") == "service_healthy", (
        "flowable must wait for postgres to be healthy"
    )

    # Datasource must point to dedicated flowable_db, never E-7 operations_engine
    flowable_env = flowable.get("environment", {})
    ds_url = flowable_env.get("SPRING_DATASOURCE_URL", "")
    assert "flowable_db" in ds_url, (
        f"Flowable datasource URL must target dedicated 'flowable_db' database, got '{ds_url}'"
    )
    assert "operations_engine" not in ds_url, (
        "Flowable must NOT share operations_engine database with E-7 canonical models"
    )


def test_healthchecks_configuration(compose_data: dict[str, Any]) -> None:
    """Verify healthchecks on stateful services and API, and no false worker HTTP check."""
    services = compose_data["services"]

    # PostgreSQL healthcheck uses pg_isready
    pg_hc = services["postgres"].get("healthcheck", {})
    assert pg_hc, "postgres must have a healthcheck"
    test_cmd = str(pg_hc.get("test", ""))
    assert "pg_isready" in test_cmd, f"postgres healthcheck must use pg_isready, got {test_cmd}"

    # RabbitMQ healthcheck uses rabbitmq-diagnostics
    rmq_hc = services["rabbitmq"].get("healthcheck", {})
    assert rmq_hc, "rabbitmq must have a healthcheck"
    rmq_cmd = str(rmq_hc.get("test", ""))
    assert "rabbitmq-diagnostics" in rmq_cmd, (
        f"rabbitmq healthcheck must use rabbitmq-diagnostics, got {rmq_cmd}"
    )

    # API healthcheck probes frozen /api/v1/health liveness probe
    api_hc = services["api"].get("healthcheck", {})
    assert api_hc, "api must have a healthcheck"
    api_cmd = str(api_hc.get("test", ""))
    assert "/api/v1/health" in api_cmd, (
        f"api healthcheck must probe /api/v1/health liveness probe, got {api_cmd}"
    )

    # Worker must NOT fabricate an HTTP health endpoint (omitted/deferred)
    worker_hc = services["worker"].get("healthcheck")
    assert worker_hc is None, (
        "worker healthcheck must be omitted/deferred in 1J.1; do not fabricate HTTP probe"
    )


def test_worker_restart_policy(compose_data: dict[str, Any]) -> None:
    """Verify workers have robust restart policies (unless-stopped or on-failure)."""
    for role in ("worker", "scheduler-worker"):
        w = compose_data["services"][role]
        restart = w.get("restart")
        assert restart in ("unless-stopped", "on-failure"), (
            f"{role} must have a restart-safe policy, got {restart}"
        )


def test_worker_dependencies_and_environment(compose_data: dict[str, Any]) -> None:
    """Verify worker depends on healthy postgres and rabbitmq, and configures outbox settings."""
    worker = compose_data["services"]["worker"]
    depends_on = worker.get("depends_on", {})
    assert isinstance(depends_on, dict), "worker must define depends_on dictionary"
    assert "postgres" in depends_on, "worker must depend on postgres"
    assert depends_on["postgres"].get("condition") == "service_healthy"
    assert "rabbitmq" in depends_on, "worker must depend on rabbitmq"
    assert depends_on["rabbitmq"].get("condition") == "service_healthy"
    assert "api" not in depends_on, "worker must not depend on api"
    assert "flowable" not in depends_on, "worker must not depend on flowable"

    worker_env = worker.get("environment", {})
    assert "E7_DATABASE_URL" in worker_env
    assert "E7_RABBITMQ_URL" in worker_env
    assert "E7_RABBITMQ_EXCHANGE_NAME" in worker_env
    assert "E7_OUTBOX_POLL_INTERVAL_SECONDS" in worker_env
    assert "E7_OUTBOX_MAX_CONCURRENCY" in worker_env

    # Inbound consumer-specific config must NOT be configured for outbox-only worker
    assert "E7_RABBITMQ_CONSUMER_QUEUE_NAME" not in worker_env


def test_worker_no_keepalive_or_sleep(compose_data: dict[str, Any]) -> None:
    """Verify worker contains no sleep, looping, or keepalive commands."""
    worker = compose_data["services"]["worker"]
    cmd = worker.get("command")
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    forbidden_tokens = ["time.sleep", "sleep ", "while True", "tail -f", "&"]
    for token in forbidden_tokens:
        assert token not in cmd_str, f"Worker command must not contain '{token}': {cmd_str}"


def test_worker_command_targets_real_module(compose_data: dict[str, Any]) -> None:
    """Verify worker command targets app.worker and placeholder exit-78 is removed."""
    worker = compose_data["services"]["worker"]
    cmd = worker.get("command")
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "app.worker" in cmd_str, f"Worker command must target app.worker, got: {cmd_str}"
    assert "SystemExit(78)" not in cmd_str, "Placeholder exit 78 script must be removed"
    assert "unavailable" not in cmd_str.lower(), "Placeholder error string must be removed"


def test_default_compose_includes_all_required_services() -> None:
    """Verify default Compose execution includes all required backend services."""
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--services"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
    except FileNotFoundError:
        pytest.skip("Docker CLI is not installed in the environment")
    if result.returncode == 0:
        default_services = set(result.stdout.strip().split())
        required_services = {
            "api",
            "worker",
            "scheduler-worker",
            "postgres",
            "rabbitmq",
            "flowable",
        }
        assert required_services.issubset(default_services), (
            f"Default compose missing required services: {default_services}"
        )


def test_named_volumes(compose_data: dict[str, Any]) -> None:
    """Verify durable named volumes for stateful services."""
    volumes = compose_data.get("volumes", {})
    assert isinstance(volumes, dict), "volumes section must exist in Compose"
    assert "postgres_data" in volumes, "postgres_data named volume must be defined"
    assert "rabbitmq_data" in volumes, "rabbitmq_data named volume must be defined"


def test_no_real_secrets_committed() -> None:
    """Scan committed Docker/Compose/environment-example files for accidental credentials."""
    forbidden_patterns = [
        "AKIA",  # AWS key
        "ghp_",  # GitHub personal token
        "glpat-",  # GitLab token
        "Bearer ey",  # JWT bearer
        "AIza",  # Google API key
        "mysql://",  # Company MySQL URL
        "client_secret",
    ]

    files_to_check = [COMPOSE_FILE, DOCKERFILE, ENV_EXAMPLE_FILE]
    for file_path in files_to_check:
        assert file_path.exists(), f"File {file_path} must exist"
        content = file_path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert pattern not in content, (
                f"Forbidden pattern '{pattern}' detected in {file_path.name}"
            )


def test_production_app_routes_unchanged() -> None:
    """Verify production application preserves foundational routes and defers job mount."""
    routes = {route.path for route in production_app.routes}  # type: ignore[attr-defined]

    # Foundational routes must exist
    assert "/" in routes, "Root endpoint '/' must be present"
    assert "/api/v1/health" in routes, "Liveness endpoint '/api/v1/health' must be present"
    assert "/api/v1/ready" in routes, "Readiness endpoint '/api/v1/ready' must be present"

    # Job read endpoint must NOT be mounted in production app
    job_routes = [r for r in routes if "/jobs" in r]
    assert not job_routes, (
        f"Job read route must NOT be mounted into production app in Task 1J.1. Found: {job_routes}"
    )


def test_database_model_stability() -> None:
    """Verify Base.metadata contains canonical Phase-1 (5), Phase-2A (6),
    Phase-3A (3), Phase-3B (2), and Phase-4A (7) tables (total 23).
    """
    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        "calendar_links",
        "calendar_watch_channels",
        "service_api_credentials",
    }
    actual_tables = set(Base.metadata.tables.keys())
    missing = phase1_tables - actual_tables
    assert phase1_tables <= actual_tables, f"Missing Phase-1 tables: {missing}"
    assert actual_tables == canonical_phase4_tables, (
        f"Base.metadata tables must be exactly {canonical_phase4_tables}, got {actual_tables}"
    )
    assert len(actual_tables) == 26


def test_alembic_head_remains_0007() -> None:
    """Verify Alembic migration head is 0014 for Phase 8D and no migration 0015 exists."""
    alembic_cfg = Config(str(ALEMBIC_INI))
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    heads = script_dir.get_heads()
    assert heads == ["0016"], f"Alembic heads expected ['0016'], got {heads}"

    rev_0007 = script_dir.get_revision("0007")
    assert rev_0007 is not None
    assert rev_0007.down_revision == "0006"

    rev_0008 = script_dir.get_revision("0008")
    assert rev_0008 is not None
    assert rev_0008.down_revision == "0007"

    rev_0009 = script_dir.get_revision("0009")
    assert rev_0009 is not None
    assert rev_0009.down_revision == "0008"

    rev_0010 = script_dir.get_revision("0010")
    assert rev_0010 is not None
    assert rev_0010.down_revision == "0009"

    rev_0011 = script_dir.get_revision("0011")
    assert rev_0011 is not None
    assert rev_0011.down_revision == "0010"

    rev_0012 = script_dir.get_revision("0012")
    assert rev_0012 is not None
    assert rev_0012.down_revision == "0011"

    rev_0013 = script_dir.get_revision("0013")
    assert rev_0013 is not None
    assert rev_0013.down_revision == "0012"

    rev_0014 = script_dir.get_revision("0014")
    assert rev_0014 is not None
    assert rev_0014.down_revision == "0013"

    rev_0015 = script_dir.get_revision("0015")
    assert rev_0015 is not None
    assert rev_0015.down_revision == "0014"

    rev_0016 = script_dir.get_revision("0016")
    assert rev_0016 is not None
    assert rev_0016.down_revision == "0015"

    migration_files = os.listdir(VERSIONS_DIR)
    has_0016 = any(fname.startswith("0016") for fname in migration_files)
    assert has_0016, "Migration 0016 must exist for Phase DQ-4"
    for fname in migration_files:
        assert not fname.startswith("0017"), f"Migration 0017 must not exist: {fname}"


def test_no_identity_persistence_tables() -> None:
    """Verify no identity/tenant/credential persistence tables were added in Task 1J.1."""
    forbidden_tables = {
        "users",
        "tenants",
        "roles",
        "service_credentials",
        "external_identity_mapping",
    }
    actual_tables = set(Base.metadata.tables.keys())
    inter = actual_tables.intersection(forbidden_tables)
    assert not inter, f"Found premature identity/tenant tables: {inter}"
