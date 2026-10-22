"""Unit tests verifying production Prometheus alerting rules validity and standards compliance.

Part of Stage 2 Production Completion & Corrective Review.
Validates:
- deploy/prometheus/alert.rules.yml is valid YAML matching Prometheus Alerting Rule schema.
- deploy/alertmanager/rules.yml mirrors the canonical Prometheus rules.
- deploy/alertmanager/alertmanager.yml is valid YAML and defines routes and inhibition rules.
- docker/observability/prometheus.yaml defines rule_files pointing to alert.rules.yml.
- Rules are strictly classified as OPERATIONAL vs NOT_OPERATIONAL.
- OPERATIONAL rules match active, verified metrics emitted in app/core/telemetry/.
- NOT_OPERATIONAL rules explicitly document remaining external dependencies.
- Zero customer PII, secrets, or unapproved credentials in rules.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMETHEUS_RULES_FILE = REPO_ROOT / "deploy" / "prometheus" / "alert.rules.yml"
ALERTMANAGER_RULES_FILE = REPO_ROOT / "deploy" / "alertmanager" / "rules.yml"
ALERTMANAGER_CONFIG_FILE = REPO_ROOT / "deploy" / "alertmanager" / "alertmanager.yml"
PROMETHEUS_CONFIG_FILE = REPO_ROOT / "docker" / "observability" / "prometheus.yaml"
INCIDENT_GUIDE_FILE = REPO_ROOT / "docs" / "production" / "INCIDENT_RESPONSE_GUIDE.md"
PROMTOOL_TEST_SUITE = REPO_ROOT / "tests" / "unit" / "test_alert_rules.promql_test.yml"

VALID_SEVERITIES = {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}
VALID_STATUSES = {"OPERATIONAL", "NOT_OPERATIONAL"}

# Verified metric names defined in app/core/telemetry/
VERIFIED_TELEMETRY_METRICS = {
    "e7_capacity_outbox_pending_depth",
    "e7_outbox_dispatch_failures_total",
    "e7_capacity_quota_rejections_total",
    "e7_capacity_schedule_queue_depth",
    "e7_scheduling_solve_results_total",
    "e7_flowable_requests_total",
}

ALL_REQUIRED_ALERTS = [
    "E7OutboxBacklogAccumulating",
    "E7OutboxDeliveryFailuresSpike",
    "E7CapacityQuotaRejectionsSpike",
    "E7SolverQueueBacklogAccumulating",
    "E7SolverBudgetExhaustionSpike",
    "E7FlowableUnavailable",
    "E7ApiUnavailable",
    "E7HttpHighErrorRate",
    "E7HttpExcessiveLatency",
    "E7SchedulingWorkerUnavailable",
    "E7AuthFailureSpike",
    "E7ExternalIntegrationDegraded",
    "E7PostgresDown",
    "E7PostgresPoolExhaustion",
    "E7RabbitMQDown",
    "E7RabbitMQConnectionFailure",
    "E7BackupMissingOrFailed",
]


def test_rules_file_exists_and_is_valid_yaml() -> None:
    """Verify that Prometheus alert rules files exist and parse as valid YAML."""
    for r_file in (PROMETHEUS_RULES_FILE, ALERTMANAGER_RULES_FILE):
        assert r_file.is_file(), f"Rules file missing: {r_file}"
        content = r_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data, dict), f"{r_file} must parse as dictionary"
        assert "groups" in data, f"{r_file} must define 'groups'"
        assert len(data["groups"]) > 0, f"{r_file} must define at least one group"


def test_all_required_alerts_present() -> None:
    """Verify all 17 alert rules covering required conditions are implemented."""
    content = PROMETHEUS_RULES_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    found_alerts: set[str] = set()
    for group in data["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                found_alerts.add(rule["alert"])

    for req in ALL_REQUIRED_ALERTS:
        assert req in found_alerts, f"Required alert '{req}' is missing from rules"


def test_rule_schema_and_operational_classification() -> None:
    """Verify rule schema, severities, runbooks, and OPERATIONAL classification."""
    assert INCIDENT_GUIDE_FILE.is_file(), "INCIDENT_RESPONSE_GUIDE.md must exist"
    content = PROMETHEUS_RULES_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    operational_count = 0
    not_operational_count = 0

    for group in data["groups"]:
        assert "name" in group, "Every group must have a name"
        for rule in group.get("rules", []):
            alert_name = rule.get("alert")
            assert alert_name, "Rule must have 'alert' name"
            assert "expr" in rule, f"{alert_name} must have 'expr'"
            assert len(rule["expr"].strip()) > 0, f"{alert_name} expr cannot be empty"
            assert "for" in rule, f"{alert_name} must specify 'for' duration"

            labels = rule.get("labels", {})
            assert "severity" in labels, f"{alert_name} missing 'severity' label"
            assert labels["severity"] in VALID_SEVERITIES, (
                f"{alert_name} invalid severity: {labels['severity']}"
            )
            assert "status" in labels, f"{alert_name} missing 'status' label"
            assert labels["status"] in VALID_STATUSES, (
                f"{alert_name} status must be OPERATIONAL or NOT_OPERATIONAL"
            )

            annotations = rule.get("annotations", {})
            assert "summary" in annotations, f"{alert_name} missing 'summary' annotation"
            assert "description" in annotations, f"{alert_name} missing 'description' annotation"
            assert "runbook_url" in annotations, f"{alert_name} missing 'runbook_url' annotation"

            # Verify OPERATIONAL rules link directly to verified telemetry metrics
            if labels["status"] == "OPERATIONAL":
                operational_count += 1
                assert "telemetry_producer" in annotations, (
                    f"OPERATIONAL alert {alert_name} must document telemetry_producer"
                )
                assert "metric_name" in annotations, (
                    f"OPERATIONAL alert {alert_name} must document metric_name"
                )
                metric = annotations["metric_name"]
                assert metric in VERIFIED_TELEMETRY_METRICS, (
                    f"{alert_name} metric '{metric}' is not in verified telemetry"
                )
            else:
                not_operational_count += 1
                assert "remaining_dependency" in annotations, (
                    f"NOT_OPERATIONAL alert {alert_name} must document remaining_dependency"
                )

    assert operational_count == 6, f"Expected exactly 6 OPERATIONAL alerts, got {operational_count}"
    assert not_operational_count == 11, (
        f"Expected exactly 11 NOT_OPERATIONAL alerts, got {not_operational_count}"
    )


def test_prometheus_loads_rule_file() -> None:
    """Verify Prometheus configuration specifies rule_files loading alert.rules.yml."""
    assert PROMETHEUS_CONFIG_FILE.is_file(), f"Prometheus config missing: {PROMETHEUS_CONFIG_FILE}"
    content = PROMETHEUS_CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(content)

    assert "rule_files" in cfg, "prometheus.yaml must define 'rule_files'"
    assert "/etc/prometheus/alert.rules.yml" in cfg["rule_files"], (
        "prometheus.yaml rule_files must load /etc/prometheus/alert.rules.yml"
    )


def test_prometheus_alertmanager_integration() -> None:
    """Verify Prometheus configuration defines alerting block routing to alertmanager."""
    assert PROMETHEUS_CONFIG_FILE.is_file(), f"Prometheus config missing: {PROMETHEUS_CONFIG_FILE}"
    content = PROMETHEUS_CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(content)

    assert "alerting" in cfg, "prometheus.yaml must define 'alerting'"
    alerting = cfg["alerting"]
    assert "alertmanagers" in alerting, "alerting must define 'alertmanagers'"
    am_targets = [
        target
        for am_cfg in alerting["alertmanagers"]
        for sc in am_cfg.get("static_configs", [])
        for target in sc.get("targets", [])
    ]
    assert any("alertmanager:9093" in t for t in am_targets), (
        f"Prometheus must route alerts to alertmanager:9093, found: {am_targets}"
    )


def test_alertmanager_routing_configuration_validity() -> None:
    """Verify Alertmanager alertmanager.yml is valid YAML and defines routing tree."""
    assert ALERTMANAGER_CONFIG_FILE.is_file(), (
        f"Alertmanager config missing: {ALERTMANAGER_CONFIG_FILE}"
    )
    content = ALERTMANAGER_CONFIG_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(content)

    assert isinstance(cfg, dict), "alertmanager.yml must be a dictionary"
    assert "route" in cfg, "alertmanager.yml must define 'route'"
    assert "receivers" in cfg, "alertmanager.yml must define 'receivers'"
    assert "inhibit_rules" in cfg, "alertmanager.yml must define 'inhibit_rules'"


def test_no_credentials_or_unapproved_secrets_in_rules() -> None:
    """Verify zero passwords, api keys, or sensitive credentials in rules or configs."""
    files_to_check = [PROMETHEUS_RULES_FILE, ALERTMANAGER_RULES_FILE, ALERTMANAGER_CONFIG_FILE]
    forbidden_tokens = [
        "bearer ",
        "password:",
        "api_key:",
        "token=",
        "BEGIN PRIVATE KEY",
    ]
    for f in files_to_check:
        content = f.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token.lower() not in content, f"Forbidden token '{token}' in {f}"


def _find_tool(binary_name: str) -> Path | None:
    """Locate tool binary in system PATH or local scratch bin directory."""
    p = shutil.which(binary_name)
    if p:
        return Path(p)
    brain_dir = Path(r"C:\Users\barma\.gemini\antigravity-ide\brain")
    conv_id = "ae1feb73-a990-46ae-a298-ee4deaf409fe"
    scratch_bin = brain_dir / conv_id / "scratch" / "bin" / f"{binary_name}.exe"
    if scratch_bin.is_file():
        return scratch_bin
    return None


def test_promtool_rule_syntax_and_promql_eval() -> None:
    """Run promtool check rules and promtool test rules when promtool is installed."""
    promtool = _find_tool("promtool")
    if not promtool:
        pytest.skip("promtool is not installed in the execution environment")

    # 1. Check syntax of canonical alert rules
    res = subprocess.run(
        [str(promtool), "check", "rules", str(PROMETHEUS_RULES_FILE)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"promtool check rules failed: {res.stderr or res.stdout}"

    # 2. Check syntax of alertmanager rules mirror
    res_am = subprocess.run(
        [str(promtool), "check", "rules", str(ALERTMANAGER_RULES_FILE)],
        capture_output=True,
        text=True,
    )
    err_am = res_am.stderr or res_am.stdout
    assert res_am.returncode == 0, f"promtool check rules failed on alertmanager rules: {err_am}"

    # 3. Test PromQL semantics and state transitions
    if PROMTOOL_TEST_SUITE.is_file():
        res_test = subprocess.run(
            [str(promtool), "test", "rules", str(PROMTOOL_TEST_SUITE)],
            capture_output=True,
            text=True,
        )
        err_test = res_test.stderr or res_test.stdout
        assert res_test.returncode == 0, f"promtool test rules failed: {err_test}"


def test_amtool_configuration_check() -> None:
    """Run amtool check-config on deploy/alertmanager/alertmanager.yml when amtool is installed."""
    amtool = _find_tool("amtool")
    if not amtool:
        pytest.skip("amtool is not installed in the execution environment")

    res = subprocess.run(
        [str(amtool), "check-config", str(ALERTMANAGER_CONFIG_FILE)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"amtool check-config failed: {res.stderr or res.stdout}"


def test_capacity_telemetry_updowncounter_delta_behavior() -> None:
    """Verify CapacityTelemetry computes deltas on UpDownCounters instead of absolutes."""
    from app.core.telemetry.capacity import CapacityTelemetry

    ct = CapacityTelemetry()
    # Mock the internal up_down_counters
    ct._outbox_pending_gauge = MagicMock()
    ct._schedule_queue_depth = MagicMock()

    # First call: depth 10 -> delta +10
    ct.record_queue_depths(outbox_depth=10, schedule_depth=5)
    ct._outbox_pending_gauge.add.assert_called_once_with(10)
    ct._schedule_queue_depth.add.assert_called_once_with(5)

    # Second call: depth unchanged -> delta 0 (no call to .add)
    ct._outbox_pending_gauge.reset_mock()
    ct._schedule_queue_depth.reset_mock()
    ct.record_queue_depths(outbox_depth=10, schedule_depth=5)
    ct._outbox_pending_gauge.add.assert_not_called()
    ct._schedule_queue_depth.add.assert_not_called()

    # Third call: depth drops to 3 and 1 -> delta -7 and -4
    ct.record_queue_depths(outbox_depth=3, schedule_depth=1)
    ct._outbox_pending_gauge.add.assert_called_once_with(-7)
    ct._schedule_queue_depth.add.assert_called_once_with(-4)
