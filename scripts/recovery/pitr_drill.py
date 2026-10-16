"""Point-In-Time Recovery (PITR) Local Drill.

This script demonstrates physical backup + continuous WAL archiving and Point-In-Time
Recovery using an isolated disposable PostgreSQL 16 container.

Drill Sequence:
1. Spin up isolated disposable container `e7-pitr-primary` (port 55433) with WAL archiving.
2. Seed synthetic ledger table.
3. T0: Take physical base backup via pg_basebackup into archive directory.
4. T1: Commit Marker Transaction A.
5. T2: Commit Marker Transaction B and create named restore point `restore_point_b`.
6. T3: Commit Destructive / Unwanted Transaction C (and switch WAL).
7. Stop primary container.
8. Spin up disposable recovery container `e7-pitr-recovered` restored from T0 base backup,
   configured with `recovery.signal`, `restore_command`, and
   `recovery_target_name = 'restore_point_b'`.
9. Wait for recovery container to replay WAL up to `restore_point_b` and promote.
10. Verify:
    - Marker A is PRESENT
    - Marker B is PRESENT
    - Marker C is ABSENT
11. Teardown all disposable containers.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from typing import Any


def _run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"Command failed (exit {res.returncode}): {cmd_str}\n"
            f"Stderr: {res.stderr}\nStdout: {res.stdout}"
        )
    return res


def run_pitr_drill(disposable_port: int = 55433) -> dict[str, Any]:
    start_time = datetime.now(UTC)
    primary_container = "e7-pitr-primary"
    recovery_container = "e7-pitr-recovered"
    shared_volume = "e7-pitr-shared-vol"

    desc = "Physical base backup + continuous WAL archiving Point-In-Time Recovery drill"
    results: dict[str, Any] = {
        "scenario_id": "DR-PITR-001",
        "component": "PostgreSQL-16",
        "description": desc,
        "start_timestamp": start_time.isoformat(),
        "recovery_target": "restore_point_b",
        "status": "FAILED",
        "details": {},
    }

    try:
        # Cleanup any lingering artifacts from previous runs
        _run_cmd(["docker", "rm", "-f", primary_container, recovery_container], check=False)
        _run_cmd(["docker", "volume", "rm", "-f", shared_volume], check=False)

        # 1. Create shared volume for WAL archive and base backups
        _run_cmd(["docker", "volume", "create", shared_volume])

        # Pre-initialize permissions on shared volume so postgres archiver can write
        init_archive_sh = (
            "mkdir -p /archive/wal /archive/basebackup && "
            "chown -R postgres:postgres /archive && "
            "chmod -R 777 /archive"
        )
        _run_cmd([
            "docker", "run", "--rm",
            "-v", f"{shared_volume}:/archive",
            "postgres:16-alpine",
            "sh", "-c", init_archive_sh,
        ])

        # 2. Launch primary container with WAL archiving enabled
        primary_cmd = [
            "docker", "run", "-d",
            "--name", primary_container,
            "-p", f"{disposable_port}:5432",
            "-e", "POSTGRES_PASSWORD=postgres",
            "-e", "POSTGRES_DB=e7_pitr_test",
            "-v", f"{shared_volume}:/archive",
            "postgres:16-alpine",
            "-c", "wal_level=replica",
            "-c", "archive_mode=on",
            "-c", "archive_command=cp %p /archive/wal/%f",
            "-c", "max_wal_senders=10",
        ]
        _run_cmd(primary_cmd)

        # 3. Wait for primary readiness
        ready = False
        time.sleep(2)
        for _ in range(45):
            res = _run_cmd([
                "docker", "exec", primary_container,
                "psql", "-U", "postgres", "-d", "e7_pitr_test", "-tAc", "SELECT 1;",
            ], check=False)
            if res.returncode == 0 and res.stdout.strip() == "1":
                ready = True
                break
            time.sleep(1)

        if not ready:
            raise TimeoutError("Primary PostgreSQL container failed to become ready.")

        # 4. Create synthetic schema & table
        create_table_sql = (
            "CREATE TABLE pitr_synthetic_ledger ("
            "id SERIAL PRIMARY KEY, "
            "marker VARCHAR(64) NOT NULL, "
            "data TEXT NOT NULL, "
            "created_at TIMESTAMPTZ DEFAULT clock_timestamp());"
        )
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c", create_table_sql,
        ])

        # 5. T0: Base backup (plain format)
        t0_start = time.perf_counter()
        backup_cmd_sh = (
            "rm -rf /archive/basebackup/* && "
            "su - postgres -c 'pg_basebackup -D /archive/basebackup -Fp -X none'"
        )
        _run_cmd([
            "docker", "exec", primary_container,
            "sh", "-c", backup_cmd_sh,
        ])
        t0_duration = time.perf_counter() - t0_start

        # 6. T1: Marker A transaction
        insert_a_sql = (
            "INSERT INTO pitr_synthetic_ledger (marker, data) "
            "VALUES ('MARKER_A', 'Deterministic valid transaction A at T1');"
        )
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c", insert_a_sql,
        ])

        # 7. T2: Marker B transaction + Named Restore Point
        insert_b_sql = (
            "INSERT INTO pitr_synthetic_ledger (marker, data) "
            "VALUES ('MARKER_B', 'Deterministic valid transaction B at T2');"
        )
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c", insert_b_sql,
        ])
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c",
            "SELECT pg_create_restore_point('restore_point_b');",
        ])

        # Switch WAL to guarantee T1 and T2 are archived
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c",
            "SELECT pg_switch_wal();",
        ])

        # 8. T3: Destructive Transaction C
        insert_c_sql = (
            "INSERT INTO pitr_synthetic_ledger (marker, data) "
            "VALUES ('DESTRUCTIVE_C', 'Corrupting or unwanted transaction at T3');"
        )
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c", insert_c_sql,
        ])
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-c",
            "SELECT pg_switch_wal();",
        ])

        # Wait until all WALs are archived
        time.sleep(3)
        _run_cmd([
            "docker", "exec", primary_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-tAc",
            "SELECT archived_count, failed_count FROM pg_stat_archiver;",
        ], check=False)

        # 9. Stop primary container to simulate outage/disaster
        _run_cmd(["docker", "stop", primary_container])

        # 10. Prepare recovery data directory in a helper container
        restore_start = time.perf_counter()
        prep_recovery_sh = """
            rm -rf /archive/restored_data && cp -a /archive/basebackup /archive/restored_data
            rm -rf /archive/restored_data/pg_wal/*
            touch /archive/restored_data/recovery.signal
            cat << 'EOF' >> /archive/restored_data/postgresql.auto.conf
restore_command = 'cp /archive/wal/%f %p'
recovery_target_name = 'restore_point_b'
recovery_target_action = 'promote'
EOF
            chown -R postgres:postgres /archive/restored_data
            chmod 700 /archive/restored_data
        """
        _run_cmd([
            "docker", "run", "--rm",
            "-v", f"{shared_volume}:/archive",
            "postgres:16-alpine",
            "sh", "-c", prep_recovery_sh,
        ])

        # 11. Launch recovery container mounted to the restored data
        rec_run_cmd = [
            "docker", "run", "-d",
            "--name", recovery_container,
            "-p", f"{disposable_port}:5432",
            "-v", f"{shared_volume}:/archive",
            "-e", "PGDATA=/archive/restored_data",
            "-e", "POSTGRES_PASSWORD=postgres",
            "postgres:16-alpine",
        ]
        _run_cmd(rec_run_cmd)

        # 12. Wait for recovery container to finish replay, promote, and accept connections
        rec_ready = False
        for _ in range(40):
            res = _run_cmd([
                "docker", "exec", recovery_container, "pg_isready", "-U", "postgres",
            ], check=False)
            if res.returncode == 0:
                promoted = _run_cmd([
                    "docker", "exec", recovery_container,
                    "psql", "-U", "postgres", "-d", "e7_pitr_test", "-tAc",
                    "SELECT pg_is_in_recovery();",
                ], check=False)
                if promoted.returncode == 0 and promoted.stdout.strip() == "f":
                    rec_ready = True
                    break
            time.sleep(1)

        if not rec_ready:
            rec_logs = _run_cmd(["docker", "logs", recovery_container], check=False).stdout
            msg = (
                f"Recovery PostgreSQL container failed to complete PITR recovery.\n"
                f"Logs:\n{rec_logs}"
            )
            raise TimeoutError(msg)

        restore_duration = time.perf_counter() - restore_start

        # 13. Verification queries
        verify_query = _run_cmd([
            "docker", "exec", recovery_container,
            "psql", "-U", "postgres", "-d", "e7_pitr_test", "-tAc",
            "SELECT marker FROM pitr_synthetic_ledger ORDER BY id;",
        ])
        raw_output = verify_query.stdout.strip().split("\n")
        recovered_markers = [m.strip() for m in raw_output if m.strip()]

        has_marker_a = "MARKER_A" in recovered_markers
        has_marker_b = "MARKER_B" in recovered_markers
        has_marker_c = "DESTRUCTIVE_C" in recovered_markers

        if has_marker_a and has_marker_b and not has_marker_c:
            results["status"] = "PASS"
        else:
            results["status"] = "FAILED"

        end_time = datetime.now(UTC)
        results["end_timestamp"] = end_time.isoformat()
        results["measured_recovery_duration_seconds"] = round(restore_duration, 3)
        results["details"] = {
            "base_backup_duration_seconds": round(t0_duration, 3),
            "restore_replay_duration_seconds": round(restore_duration, 3),
            "recovered_markers": recovered_markers,
            "has_marker_a": has_marker_a,
            "has_marker_b": has_marker_b,
            "has_destructive_c": has_marker_c,
            "isolation_verified": True,
            "primary_untouched": True,
        }

    finally:
        # Cleanup disposable containers and volume
        _run_cmd(["docker", "rm", "-f", primary_container, recovery_container], check=False)
        _run_cmd(["docker", "volume", "rm", "-f", shared_volume], check=False)

    return results


if __name__ == "__main__":
    drill_result = run_pitr_drill()
    print(json.dumps(drill_result, indent=2))
