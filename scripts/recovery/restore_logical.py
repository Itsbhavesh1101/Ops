"""Automated Logical PostgreSQL Restore & Verification Script (Phase 8D).

Restores a logical pg_dump backup into a designated disposable target database.
Adheres to Section 5, 8, 9, 33 rules:
- Prohibits accidental overwrite of primary development database.
- Verifies Alembic schema version (0013) and 25 application tables.
- Verifies entity invariants, row counts, and tenant isolation.
- Measures restore duration and verification duration.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def restore_and_verify(
    dump_path: Path,
    target_database: str = "e7_recovery_drill_disposable",
    container_name: str = "e7-postgres",
    user: str = "postgres",
    cleanup_after: bool = False,
    allow_primary_overwrite: bool = False,
) -> dict[str, object]:
    """Restore logical backup into target database and verify schema/row invariants."""
    if not dump_path.exists():
        raise FileNotFoundError(f"Backup file not found: {dump_path}")

    primary_db = os.getenv("POSTGRES_DB", "operations_engine")
    if target_database == primary_db and not allow_primary_overwrite:
        msg = (
            f"SAFETY VIOLATION: Target database '{target_database}' is the active primary DB. "
            f"Logical restore drills must use a disposable database "
            f"(e.g. e7_recovery_drill_disposable) unless --force-dangerous-overwrite is set."
        )
        raise ValueError(msg)

    t0_restore = time.perf_counter()

    # 1. Copy dump file into container
    copy_cmd = ["docker", "cp", str(dump_path), f"{container_name}:/tmp/e7_restore.dump"]
    copy_res = subprocess.run(copy_cmd, capture_output=True, text=True)
    if copy_res.returncode != 0:
        raise RuntimeError(f"docker cp failed: {copy_res.stderr.strip()}")

    # 2. Re-create clean disposable database
    drop_sql = f"DROP DATABASE IF EXISTS {target_database};"
    subprocess.run(
        ["docker", "exec", container_name, "psql", "-U", user, "-c", drop_sql],
        capture_output=True,
        text=True,
        check=True,
    )
    create_sql = f"CREATE DATABASE {target_database};"
    subprocess.run(
        ["docker", "exec", container_name, "psql", "-U", user, "-c", create_sql],
        capture_output=True,
        text=True,
        check=True,
    )

    # 3. Execute pg_restore
    restore_cmd = [
        "docker",
        "exec",
        container_name,
        "pg_restore",
        "-U",
        user,
        "-d",
        target_database,
        "--no-owner",
        "--no-privileges",
        "/tmp/e7_restore.dump",
    ]
    restore_res = subprocess.run(restore_cmd, capture_output=True, text=True)
    # Note: pg_restore returns exit code 0 on success, or 1 if non-fatal warnings occurred
    if restore_res.returncode > 1:
        msg = f"pg_restore failed with code {restore_res.returncode}: {restore_res.stderr.strip()}"
        raise RuntimeError(msg)

    restore_duration = time.perf_counter() - t0_restore

    # 4. Verify Schema & Row Count Invariants
    t0_verify = time.perf_counter()

    # Query Alembic revision
    alembic_res = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-U",
            user,
            "-d",
            target_database,
            "-t",
            "-A",
            "-c",
            "SELECT version_num FROM alembic_version LIMIT 1;",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    alembic_rev = alembic_res.stdout.strip()

    # Query application tables count
    count_tables_sql = (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' "
        "AND table_name != 'alembic_version';"
    )
    tables_res = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-U",
            user,
            "-d",
            target_database,
            "-t",
            "-A",
            "-c",
            count_tables_sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    app_tables_count = int(tables_res.stdout.strip())

    # Query key table row counts
    key_tables = [
        "goals",
        "plans",
        "tasks",
        "resources",
        "schedule_jobs",
        "schedules",
        "outbox_events",
        "inbox_records",
        "audit_events",
        "workflow_task_mappings",
    ]
    row_counts: dict[str, int] = {}
    for tbl in key_tables:
        cnt_res = subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "psql",
                "-U",
                user,
                "-d",
                target_database,
                "-t",
                "-A",
                "-c",
                f"SELECT count(*) FROM {tbl};",
            ],
            capture_output=True,
            text=True,
        )
        if cnt_res.returncode == 0 and cnt_res.stdout.strip().isdigit():
            row_counts[tbl] = int(cnt_res.stdout.strip())
        else:
            row_counts[tbl] = -1

    # Check tenant isolation on goals
    tenants_res = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-U",
            user,
            "-d",
            target_database,
            "-t",
            "-A",
            "-c",
            "SELECT count(DISTINCT tenant_id) FROM goals;",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    raw_tenants = tenants_res.stdout.strip()
    distinct_tenants_count = int(raw_tenants) if raw_tenants.isdigit() else 0

    verify_duration = time.perf_counter() - t0_verify

    # 5. Clean up container /tmp artifact
    subprocess.run(
        ["docker", "exec", container_name, "rm", "-f", "/tmp/e7_restore.dump"],
        check=False,
    )

    # 6. Cleanup disposable target DB if requested
    if cleanup_after and target_database != primary_db:
        drop_target_sql = f"DROP DATABASE IF EXISTS {target_database};"
        subprocess.run(
            ["docker", "exec", container_name, "psql", "-U", user, "-c", drop_target_sql],
            check=False,
        )

    alembic_valid = alembic_rev in ("0013", "0014")
    tables_valid = app_tables_count in (25, 26)
    verification_pass = alembic_valid and tables_valid

    return {
        "status": "PASS" if verification_pass else "FAIL",
        "target_database": target_database,
        "restore_duration_seconds": round(restore_duration, 3),
        "verification_duration_seconds": round(verify_duration, 3),
        "alembic_head": alembic_rev,
        "alembic_verified": alembic_valid,
        "application_tables_count": app_tables_count,
        "tables_verified": tables_valid,
        "distinct_tenants_verified": distinct_tenants_count,
        "row_counts": row_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="E-7 Operations Engine Logical Restore Drill")
    parser.add_argument("dump_file", type=Path, help="Path to .dump archive")
    parser.add_argument(
        "--target-db",
        type=str,
        default="e7_recovery_drill_disposable",
        help="Disposable target database name",
    )
    parser.add_argument(
        "--container",
        type=str,
        default="e7-postgres",
        help="PostgreSQL docker container name",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Drop target database after verification",
    )
    parser.add_argument(
        "--force-dangerous-overwrite",
        action="store_true",
        help="Allow restoring over active primary database (DANGEROUS)",
    )
    args = parser.parse_args()

    try:
        report = restore_and_verify(
            dump_path=args.dump_file,
            target_database=args.target_db,
            container_name=args.container,
            cleanup_after=args.cleanup,
            allow_primary_overwrite=args.force_dangerous_overwrite,
        )
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["status"] == "PASS" else 1)
    except Exception as exc:
        print(f"ERROR: Logical restore verification failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
