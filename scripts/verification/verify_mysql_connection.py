"""Verify the institutional MySQL account is complete and strictly read-only."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from scripts.verification.probe_common import finish_cli, run_bounded_probe

BLOCKER_ID = "MYSQL_LIVE_READ_VERIFICATION"


def evaluate_grants(grants: list[str], *, target_database: str = "iredu") -> dict[str, Any]:
    evaluation = MySqlIreduAdapter.evaluate_grants_least_privilege(
        grants, target_database=target_database
    )
    write_detected = any(
        keyword in grant.upper()
        for grant in grants
        for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "GRANT OPTION")
    )
    passed = bool(evaluation["is_compliant"] and evaluation["is_complete"] and not write_detected)
    return {
        "status": "PASS" if passed else "FAIL",
        "write_privileges_detected": write_detected,
        "grant_count": len(grants),
        "granted_tables": evaluation["granted_tables"],
        "missing_active_tables": evaluation["missing_active_tables"],
        "violation_count": len(evaluation["violations"]),
    }


async def probe_mysql() -> dict[str, Any]:
    import aiomysql

    password = os.environ["E7_MYSQL_PASSWORD"]
    connection = await aiomysql.connect(
        host=os.environ["E7_MYSQL_HOST"],
        port=int(os.environ.get("E7_MYSQL_PORT", "3306")),
        user=os.environ["E7_MYSQL_USER"],
        password=password,
        db=os.environ.get("E7_MYSQL_DATABASE", "iredu"),
        connect_timeout=float(os.environ.get("E7_MYSQL_CONNECT_TIMEOUT", "5")),
        autocommit=False,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SET SESSION TRANSACTION READ ONLY")
            await cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            rows = await cursor.fetchall()
        grants = [str(row[0]) for row in rows]
        return evaluate_grants(grants, target_database=os.environ.get("E7_MYSQL_DATABASE", "iredu"))
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    secret = os.environ.get("E7_MYSQL_PASSWORD", "")
    payload = asyncio.run(
        run_bounded_probe(
            BLOCKER_ID,
            probe_mysql(),
            timeout_seconds=args.timeout,
            secret_values=[secret],
        )
    )
    return finish_cli(payload, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
