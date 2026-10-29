from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_workflow(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _step_commands(job: dict[str, object]) -> list[str]:
    commands: list[str] = []
    for step in job.get("steps", []):
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            commands.append(step["run"])
    return commands


def test_primary_ci_has_no_stale_hard_coded_collection_count() -> None:
    workflow = _load_workflow(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)

    step_names = [
        str(step.get("name", ""))
        for job in jobs.values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]
    assert "Verify Global Pytest Collection" in step_names
    assert all("2,391" not in name for name in step_names)


def test_full_verification_workflow_has_linux_and_windows_gates() -> None:
    workflow = _load_workflow(
        REPO_ROOT / ".github" / "workflows" / "full-verification.yml"
    )
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert {"linux-full-verification", "windows-flowable-config"} <= set(jobs)

    linux_commands = "\n".join(_step_commands(jobs["linux-full-verification"]))
    windows_commands = "\n".join(_step_commands(jobs["windows-flowable-config"]))
    assert "scripts/verification/run_verification.py" in linux_commands
    assert "--group recovery" in linux_commands
    assert "--group uat" in linux_commands
    assert "tests/unit/workflow/test_flowable_config.py" in windows_commands


def test_manifest_opts_in_recovery_and_uat_only_in_provisioned_runs() -> None:
    manifest = json.loads(
        (REPO_ROOT / "scripts" / "verification" / "verification_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    gates = {gate["id"]: gate for gate in manifest["gates"]}

    assert gates["recovery-suite"]["environment"]["E7_RUN_RECOVERY"] == "1"
    assert gates["role-uat"]["environment"]["E7_RUN_UAT"] == "1"


def test_infrastructure_opt_in_hooks_do_not_skip_unrelated_tests() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/workflow/test_flowable_config.py::test_flowable_settings_requires_explicit_arguments",
            "tests/recovery/test_api_auth_recovery.py::test_postgres_service_credential_persistence_and_api_restart_recovery",
            "tests/uat/test_role_based_uat.py::TestUatWorkflowRuntime::test_uat_workflow_003_sla_timer_experience",
            "-q",
            "--tb=short",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed, 2 skipped" in completed.stdout
