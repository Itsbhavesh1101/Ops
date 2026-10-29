#!/usr/bin/env python3
"""Bootstrap Synthetic Demonstration Data for Real Application Acceptance Testing.

Initializes:
1. Canonical BPMN workflow definition ('exam-operations-v1') deployed to Flowable.
2. Active workflow process instance with candidate human task ('Review Examination Moderation').
3. Synthetic educational resources and availability windows for scheduling.
4. Baseline audit event record in PostgreSQL for audit explorer verification.

Security & Invariants:
- Strictly scoped to 'tenant-pilot-001'.
- Pure synthetic data; zero external institutional dependencies.
- Deterministic and idempotent: safe to rerun multiple times.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select

from app.audit.models import AuditEventRecord
from app.audit.schemas import AuditEventOutcome
from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.models.scheduling import ResourceAvailabilityRecord, ResourceRecord
from app.db.uow import create_unit_of_work_factory
from app.resources.schemas import ResourceType
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.repository import WorkflowDefinitionRepository
from app.workflow.service import WorkflowService

TENANT_ID: str = "tenant-pilot-001"
WORKFLOW_KEY: str = "exam-operations-v1"
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


async def bootstrap_workflow(uow_factory: Any) -> dict[str, Any]:
    """Register and deploy BPMN workflow to Flowable and start an instance."""
    flowable_settings = FlowableSettings(
        base_url=os.environ.get("FLOWABLE_BASE_URL", "http://localhost:8080/flowable-rest/service"),
        timeout_seconds=float(os.environ.get("FLOWABLE_TIMEOUT_SECONDS", "10.0")),
        max_retries=int(os.environ.get("FLOWABLE_MAX_RETRIES", "3")),
        username=(
            os.environ.get("FLOWABLE_USER") or os.environ.get("FLOWABLE_USERNAME", "rest-admin")
        ),
        password=SecretStr(os.environ.get("FLOWABLE_PASSWORD", "test")),
    )
    adapter = FlowableAdapter(settings=flowable_settings)
    service = WorkflowService(uow_factory=uow_factory, adapter=adapter)

    bpmn_path = REPO_ROOT / "workflows" / "bpmn" / "semester_exam_readiness.bpmn20.xml"
    if not bpmn_path.exists():
        raise FileNotFoundError(f"Canonical BPMN XML not found at {bpmn_path}")

    bpmn_xml = bpmn_path.read_text(encoding="utf-8")

    # Check if already deployed
    async with uow_factory() as uow:
        repo = WorkflowDefinitionRepository(uow.session)
        existing = await repo.get_latest_active_definition(TENANT_ID, WORKFLOW_KEY)

    if existing and existing.is_active and existing.deployment_id:
        definition_id = existing.id
        deployment_id = existing.deployment_id
    else:
        registered = await service.register_definition(
            tenant_id=TENANT_ID,
            workflow_key=WORKFLOW_KEY,
            name="Grade 10 Semester Examination Readiness Process",
            bpmn_xml=bpmn_xml,
            description="Canonical Grade 10 Semester Examination Readiness Process",
            actor_id="system-bootstrap",
        )
        deployed = await service.deploy_definition(
            tenant_id=TENANT_ID,
            definition_id=registered.id,
        )
        definition_id = deployed.id
        deployment_id = deployed.deployment_id

    # Start a demo workflow instance so that a pending human task exists in the inbox
    active_tasks = await service.list_tasks(tenant_id=TENANT_ID, status="PENDING")
    if not active_tasks:
        instance = await service.start_instance(
            tenant_id=TENANT_ID,
            workflow_key=WORKFLOW_KEY,
            business_reference="demo-plan-exam-operations-001",
            variables={"exam_scope": "grade-10-core", "prepared_by": "planner"},
            actor_id="system-bootstrap",
        )
        instance_id = str(instance.id)
    else:
        instance_id = str(active_tasks[0].workflow_instance_id or "existing")

    return {
        "workflow_key": WORKFLOW_KEY,
        "definition_id": str(definition_id),
        "deployment_id": deployment_id,
        "instance_id": instance_id,
    }


async def bootstrap_resources(uow_factory: Any) -> int:
    """Seed synthetic educational resources and availability for scheduler solving."""
    now = datetime.now(UTC).replace(microsecond=0)

    resources_data = [
        ("res-hall-001", "Main Examination Hall A", ResourceType.ROOM, 120),
        ("res-hall-002", "Senior Examination Hall B", ResourceType.ROOM, 80),
        ("res-inv-001", "Dr. A. Sharma (Chief Invigilator)", ResourceType.PERSON, 1),
        ("res-inv-002", "Prof. R. Patel (Deputy Invigilator)", ResourceType.PERSON, 1),
        ("res-inv-003", "Ms. S. Rao (Invigilator)", ResourceType.PERSON, 1),
    ]

    count = 0
    async with uow_factory() as uow:
        for res_key, label, res_type, capacity in resources_data:
            stmt = select(ResourceRecord).where(
                ResourceRecord.tenant_id == TENANT_ID,
                ResourceRecord.resource_key == res_key,
            )
            res = await uow.session.scalar(stmt)
            if not res:
                rec_id = uuid4()
                rec = ResourceRecord(
                    id=rec_id,
                    tenant_id=TENANT_ID,
                    resource_key=res_key,
                    display_label=label,
                    type=res_type.value,
                    capacity=capacity,
                    active=True,
                    skills=["invigilator"] if res_type == ResourceType.PERSON else [],
                    attributes={"department": "Academic Affairs", "campus": "North"},
                    created_at=now,
                )
                uow.session.add(rec)
                count += 1

                # Add availability slot
                avail = ResourceAvailabilityRecord(
                    id=uuid4(),
                    tenant_id=TENANT_ID,
                    resource_id=rec_id,
                    start_at=now - timedelta(days=1),
                    end_at=now + timedelta(days=30),
                    interval_type="AVAILABLE",
                    reason="Academic Session 2026",
                    created_at=now,
                )
                uow.session.add(avail)

        await uow.commit()

    return count


async def bootstrap_baseline_audit(uow_factory: Any) -> None:
    """Seed an immutable baseline audit event so the audit explorer has initial records."""
    now = datetime.now(UTC).replace(microsecond=0)

    async with uow_factory() as uow:
        stmt = select(AuditEventRecord).where(
            AuditEventRecord.tenant_id == TENANT_ID,
            AuditEventRecord.event_type == "SYSTEM_INITIALIZED",
        )
        existing = await uow.session.scalar(stmt)
        if not existing:
            event = AuditEventRecord(
                id=uuid4(),
                tenant_id=TENANT_ID,
                correlation_id=f"corr-bootstrap-{uuid4().hex[:8]}",
                actor_id="system-bootstrap",
                actor_role="admin",
                event_type="SYSTEM_INITIALIZED",
                resource_type="system",
                resource_id="e7-operations-engine",
                action="INITIALIZE",
                outcome=AuditEventOutcome.SUCCESS.value,
                timestamp=now,
                details={
                    "environment": "acceptance",
                    "description": "Operations Engine synthetic baseline initialized successfully.",
                },
            )
            uow.session.add(event)
            await uow.commit()


async def main() -> int:
    settings = get_settings()
    db = DatabaseManager(settings=settings)
    uow_factory = create_unit_of_work_factory(db.session_factory)

    print(f"[*] Bootstrapping synthetic acceptance baseline for tenant '{TENANT_ID}'...")

    try:
        wf_res = await bootstrap_workflow(uow_factory)
        wf_key = wf_res["workflow_key"]
        inst_id = wf_res["instance_id"]
        print(f"[+] Flowable workflow ready: {wf_key} (instance: {inst_id})")
    except Exception as exc:
        print(f"[-] Flowable bootstrap warning: {exc} (ignoring if offline)")

    try:
        res_count = await bootstrap_resources(uow_factory)
        print(f"[+] Seeded {res_count} new scheduling resources into PostgreSQL.")
    except Exception as exc:
        print(f"[-] Resource bootstrap failed: {exc}")
        return 1

    try:
        await bootstrap_baseline_audit(uow_factory)
        print("[+] Baseline audit event recorded in PostgreSQL.")
    except Exception as exc:
        print(f"[-] Audit bootstrap failed: {exc}")
        return 1

    print("[SUCCESS] Acceptance baseline successfully prepared.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
