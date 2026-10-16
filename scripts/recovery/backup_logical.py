"""Automated Logical PostgreSQL Backup Script (Phase 8D).

Performs safe, non-destructive logical backup using pg_dump.
Adheres to Section 9 (Secret Safety) and Section 33 (Script Rules):
- Never embeds secrets or passwords.
- Derives credentials strictly from environment variables.
- Calculates SHA-256 checksum and integrity manifest.
- Never drops or alters active databases.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def run_logical_backup(
    output_path: Path,
    database: str = "operations_engine",
    container_name: str = "e7-postgres",
    user: str = "postgres",
) -> dict[str, object]:
    """Execute pg_dump in custom format and return backup manifest."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    timestamp = datetime.now(UTC).isoformat()

    # Execute pg_dump inside container to avoid host tool version skew
    cmd = [
        "docker",
        "exec",
        container_name,
        "pg_dump",
        "-U",
        user,
        "-d",
        database,
        "-Fc",  # Custom archive format with compression
        "-f",
        "/tmp/e7_backup.dump",
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"pg_dump failed with exit code {res.returncode}: {res.stderr.strip()}")

    # Copy backup artifact from container to host output_path
    copy_cmd = ["docker", "cp", f"{container_name}:/tmp/e7_backup.dump", str(output_path)]
    copy_res = subprocess.run(copy_cmd, capture_output=True, text=True)
    if copy_res.returncode != 0:
        raise RuntimeError(f"docker cp failed: {copy_res.stderr.strip()}")

    # Clean up container /tmp artifact
    subprocess.run(
        ["docker", "exec", container_name, "rm", "-f", "/tmp/e7_backup.dump"],
        check=False,
    )

    duration = time.perf_counter() - t0
    size_bytes = output_path.stat().st_size

    # Calculate SHA-256 hash
    hasher = hashlib.sha256()
    with open(output_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    checksum = hasher.hexdigest()

    manifest: dict[str, object] = {
        "backup_type": "LOGICAL_PG_DUMP_CUSTOM",
        "source_database": database,
        "container_name": container_name,
        "timestamp_utc": timestamp,
        "duration_seconds": round(duration, 3),
        "backup_size_bytes": size_bytes,
        "sha256_checksum": checksum,
        "output_file": str(output_path),
        "status": "COMPLETED_VERIFIED_NON_EMPTY" if size_bytes > 0 else "EMPTY_FAILURE",
    }

    manifest_path = output_path.with_suffix(".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="E-7 Operations Engine Logical Backup")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scratch/backups/e7_logical_backup.dump"),
        help="Target output dump file path",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=os.getenv("POSTGRES_DB", "operations_engine"),
        help="Database to dump",
    )
    parser.add_argument(
        "--container",
        type=str,
        default="e7-postgres",
        help="PostgreSQL docker container name",
    )
    args = parser.parse_args()

    try:
        manifest = run_logical_backup(
            output_path=args.output,
            database=args.database,
            container_name=args.container,
        )
        print(json.dumps(manifest, indent=2))
        sys.exit(0)
    except Exception as exc:
        print(f"ERROR: Logical backup failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
