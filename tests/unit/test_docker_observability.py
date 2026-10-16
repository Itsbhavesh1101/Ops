"""Static validation and configuration unit tests for Docker Observability runtime.

Part of Phase 1 Foundation Task 1K.6.
Validates:
- Optional 'observability' Compose profile exists
- OpenTelemetry Collector and Prometheus services belong exclusively to 'observability' profile
- Strict absence of Grafana
- Default Compose execution excludes observability services
- Internal OTLP endpoint configuration routes to otel-collector:4318
- OpenTelemetry Collector and Prometheus configuration files are valid and contain no secrets
- Worker does not expose /metrics or run direct Prometheus HTTP server
- No dedicated telemetry database service
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COLLECTOR_CONFIG_FILE = REPO_ROOT / "docker" / "observability" / "otel-collector.yaml"
PROMETHEUS_CONFIG_FILE = REPO_ROOT / "docker" / "observability" / "prometheus.yaml"


@pytest.fixture(scope="module")
def compose_data() -> dict[str, Any]:
    """Parse and return raw docker-compose.yml structure."""
    assert COMPOSE_FILE.exists(), f"docker-compose.yml must exist at {COMPOSE_FILE}"
    with open(COMPOSE_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "docker-compose.yml must parse to a dictionary"
    return data


def test_observability_services_under_profile(compose_data: dict[str, Any]) -> None:
    """Verify otel-collector and prometheus belong strictly to 'observability' profile."""
    services = compose_data.get("services", {})

    assert "otel-collector" in services, "otel-collector service must be defined"
    collector = services["otel-collector"]
    assert collector.get("profiles") == ["observability"], (
        "otel-collector must be under ['observability'] profile"
    )

    assert "prometheus" in services, "prometheus service must be defined"
    prom = services["prometheus"]
    assert prom.get("profiles") == ["observability"], (
        "prometheus must be under ['observability'] profile"
    )


def test_strict_absence_of_grafana(compose_data: dict[str, Any]) -> None:
    """Verify Grafana is deferred and strictly absent from Compose services."""
    services = compose_data.get("services", {})
    assert "grafana" not in services, "Grafana must NOT be present in Phase-1 Compose"
    for s_name, s_def in services.items():
        image = str(s_def.get("image", ""))
        assert "grafana" not in image.lower(), (
            f"Service {s_name} uses Grafana image which is deferred in Phase 1"
        )


def test_default_compose_excludes_observability() -> None:
    """Verify default docker compose config does not activate observability services."""
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
        services = set(result.stdout.strip().split())
        assert "otel-collector" not in services, (
            "otel-collector must not be included in default Compose"
        )
        assert "prometheus" not in services, "prometheus must not be included in default Compose"
        assert "worker" not in services, "worker must not be included in default Compose"


def test_collector_configuration_validity() -> None:
    """Verify otel-collector.yaml is valid YAML and configures OTLP/HTTP and Prometheus exporter."""
    assert COLLECTOR_CONFIG_FILE.exists(), f"Collector config missing: {COLLECTOR_CONFIG_FILE}"
    content = COLLECTOR_CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(content)
    assert isinstance(cfg, dict), "Collector config must be a dictionary"

    receivers = cfg.get("receivers", {})
    assert "otlp" in receivers, "Collector must configure otlp receiver"
    protocols = receivers["otlp"].get("protocols", {})
    assert "http" in protocols, "Collector must configure otlp http protocol"
    assert "4318" in str(protocols["http"].get("endpoint", "")), (
        "Collector HTTP endpoint must listen on port 4318"
    )

    exporters = cfg.get("exporters", {})
    assert "prometheus" in exporters, "Collector must configure prometheus exporter"
    assert "8889" in str(exporters["prometheus"].get("endpoint", "")), (
        "Collector prometheus exporter must listen on port 8889"
    )
    assert "debug" in exporters, (
        "Collector must configure debug exporter for local trace verification"
    )

    service = cfg.get("service", {})
    pipelines = service.get("pipelines", {})
    assert "traces" in pipelines, "Collector must define traces pipeline"
    assert "metrics" in pipelines, "Collector must define metrics pipeline"


def test_prometheus_configuration_validity() -> None:
    """Verify prometheus.yaml is valid YAML and scrapes the collector endpoint."""
    assert PROMETHEUS_CONFIG_FILE.exists(), f"Prometheus config missing: {PROMETHEUS_CONFIG_FILE}"
    content = PROMETHEUS_CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(content)
    assert isinstance(cfg, dict), "Prometheus config must be a dictionary"

    scrape_configs = cfg.get("scrape_configs", [])
    assert len(scrape_configs) > 0, "Prometheus must define at least one scrape config"
    targets = [
        target
        for sc in scrape_configs
        for sc_cfg in sc.get("static_configs", [])
        for target in sc_cfg.get("targets", [])
    ]
    assert any("otel-collector:8889" in t for t in targets), (
        f"Prometheus must scrape otel-collector:8889, found: {targets}"
    )


def test_api_and_worker_telemetry_environment(compose_data: dict[str, Any]) -> None:
    """Verify API and worker services configure internal OTLP endpoint and service names."""
    services = compose_data["services"]

    api_env = services["api"].get("environment", {})
    assert "E7_OTEL_ENABLED" in api_env
    assert "E7_OTEL_EXPORTER_OTLP_ENDPOINT" in api_env
    assert "http://otel-collector:4318" in api_env["E7_OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert api_env.get("E7_OTEL_SERVICE_NAME") == "${E7_OTEL_SERVICE_NAME:-e7-api}"

    worker_env = services["worker"].get("environment", {})
    assert "E7_OTEL_ENABLED" in worker_env
    assert "E7_OTEL_EXPORTER_OTLP_ENDPOINT" in worker_env
    assert "http://otel-collector:4318" in worker_env["E7_OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert worker_env.get("E7_OTEL_SERVICE_NAME") == "${E7_OTEL_SERVICE_NAME:-e7-worker}"


def test_worker_no_metrics_port(compose_data: dict[str, Any]) -> None:
    """Verify worker service does not expose a /metrics port or direct Prometheus server."""
    worker = compose_data["services"]["worker"]
    assert "ports" not in worker or not worker.get("ports"), (
        "worker service must NOT expose any network ports or /metrics endpoint"
    )


def test_no_telemetry_database_service(compose_data: dict[str, Any]) -> None:
    """Verify no separate database service is defined for telemetry."""
    services = compose_data.get("services", {})
    telemetry_db_names = {"clickhouse", "influxdb", "timescaledb", "telemetry-db", "otel-db"}
    inter = set(services.keys()).intersection(telemetry_db_names)
    assert not inter, f"Found unapproved telemetry database service: {inter}"


def test_configurable_host_ports(compose_data: dict[str, Any]) -> None:
    """Verify collector and prometheus exposed ports are environment-configurable."""
    services = compose_data["services"]

    collector_ports = services["otel-collector"].get("ports", [])
    assert any("OTEL_COLLECTOR_HTTP_PORT" in p for p in collector_ports), (
        "otel-collector host port must be configurable via OTEL_COLLECTOR_HTTP_PORT"
    )

    prom_ports = services["prometheus"].get("ports", [])
    assert any("PROMETHEUS_PORT" in p for p in prom_ports), (
        "prometheus host port must be configurable via PROMETHEUS_PORT"
    )


def test_no_secrets_in_observability_configs() -> None:
    """Verify absence of real secrets or production credentials in observability files."""
    forbidden_patterns = [
        "AKIA",
        "ghp_",
        "glpat-",
        "Bearer ey",
        "AIza",
        "mysql://",
        "client_secret",
    ]
    for file_path in [COLLECTOR_CONFIG_FILE, PROMETHEUS_CONFIG_FILE]:
        content = file_path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert pattern not in content, (
                f"Forbidden pattern '{pattern}' detected in {file_path.name}"
            )
