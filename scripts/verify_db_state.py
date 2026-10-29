#!/usr/bin/env python3
"""Database State Verification Script for End-to-End Browser Acceptance Testing.

Directly queries PostgreSQL System of Record to verify persistence of entities
created or updated through real browser user journeys.

Commands:
- verify-goal: Verify goal persistence in 'goals' table.
- verify-plan: Verify plan persistence in 'plans' table.
- verify-workflow-task: Verify workflow task status in 'workflow_tasks' table.
- verify-schedule: Verify schedule status in 'schedules' table.
- verify-audit: Verify recorded audit events in 'audit_events' table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app.audit.models import AuditEventRecord
from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.models.planning import GoalRecord, PlanRecord
from app.db.models.scheduling import ScheduleRecord
from app.db.models.workflow import WorkflowTaskMappingRecord

DEFAULT_TENANT: str = "tenant-pilot-001"


async def check_goal(tenant_id: str, objective_contains: str) -> dict[str, Any]:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    async with db.session_factory() as session:
        stmt = select(GoalRecord).where(
            GoalRecord.tenant_id == tenant_id,
            GoalRecord.objective.ilike(f"%{objective_contains}%"),
        )
        records = (await session.scalars(stmt)).all()
        return {
            "success": len(records) > 0,
            "count": len(records),
            "records": [
                {"id": str(r.id), "objective": r.objective, "scope": r.scope} for r in records
            ],
        }


async def check_plan(tenant_id: str, name_contains: str) -> dict[str, Any]:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    async with db.session_factory() as session:
        stmt = select(PlanRecord).where(
            PlanRecord.tenant_id == tenant_id,
            PlanRecord.plan_name.ilike(f"%{name_contains}%"),
        )
        records = (await session.scalars(stmt)).all()
        return {
            "success": len(records) > 0,
            "count": len(records),
            "records": [
                {
                    "id": str(r.id),
                    "plan_name": r.plan_name,
                    "state": r.state,
                    "version": r.version_number,
                }
                for r in records
            ],
        }


async def check_workflow_task(tenant_id: str, status: str) -> dict[str, Any]:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    async with db.session_factory() as session:
        stmt = select(WorkflowTaskMappingRecord).where(
            WorkflowTaskMappingRecord.tenant_id == tenant_id,
            WorkflowTaskMappingRecord.status == status.upper(),
        )
        records = (await session.scalars(stmt)).all()
        return {
            "success": len(records) > 0,
            "count": len(records),
            "records": [
                {"id": str(r.id), "name": r.task_name, "status": r.status, "assignee": r.assignee}
                for r in records
            ],
        }


async def check_schedule(tenant_id: str, status: str) -> dict[str, Any]:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    async with db.session_factory() as session:
        stmt = select(ScheduleRecord).where(
            ScheduleRecord.tenant_id == tenant_id,
            ScheduleRecord.status == status.upper(),
        )
        records = (await session.scalars(stmt)).all()
        return {
            "success": len(records) > 0,
            "count": len(records),
            "records": [
                {"id": str(r.id), "status": r.status, "job_id": str(r.source_job_id)}
                for r in records
            ],
        }


async def check_audit(tenant_id: str, event_type: str) -> dict[str, Any]:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    async with db.session_factory() as session:
        stmt = select(AuditEventRecord).where(
            AuditEventRecord.tenant_id == tenant_id,
            AuditEventRecord.event_type == event_type.upper(),
        )
        records = (await session.scalars(stmt)).all()
        return {
            "success": len(records) > 0,
            "count": len(records),
            "records": [
                {
                    "id": str(r.audit_event_id),
                    "event_type": r.event_type,
                    "actor_id": r.actor_principal_id,
                    "outcome": r.outcome,
                }
                for r in records
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PostgreSQL state for E2E acceptance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Goal
    p_goal = subparsers.add_parser("verify-goal")
    p_goal.add_argument("--tenant-id", default=DEFAULT_TENANT)
    p_goal.add_argument("--objective", required=True)

    # Plan
    p_plan = subparsers.add_parser("verify-plan")
    p_plan.add_argument("--tenant-id", default=DEFAULT_TENANT)
    p_plan.add_argument("--name", required=True)

    # Workflow task
    p_task = subparsers.add_parser("verify-workflow-task")
    p_task.add_argument("--tenant-id", default=DEFAULT_TENANT)
    p_task.add_argument("--status", required=True)

    # Schedule
    p_sched = subparsers.add_parser("verify-schedule")
    p_sched.add_argument("--tenant-id", default=DEFAULT_TENANT)
    p_sched.add_argument("--status", required=True)

    # Audit
    p_audit = subparsers.add_parser("verify-audit")
    p_audit.add_argument("--tenant-id", default=DEFAULT_TENANT)
    p_audit.add_argument("--event-type", required=True)

    args = parser.parse_args()

    result: dict[str, Any]
    if args.command == "verify-goal":
        result = asyncio.run(check_goal(args.tenant_id, args.objective))
    elif args.command == "verify-plan":
        result = asyncio.run(check_plan(args.tenant_id, args.name))
    elif args.command == "verify-workflow-task":
        result = asyncio.run(check_workflow_task(args.tenant_id, args.status))
    elif args.command == "verify-schedule":
        result = asyncio.run(check_schedule(args.tenant_id, args.status))
    elif args.command == "verify-audit":
        result = asyncio.run(check_audit(args.tenant_id, args.event_type))
    else:
        result = {"success": False, "error": "Unknown command"}

    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())

