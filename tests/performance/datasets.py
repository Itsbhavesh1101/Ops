"""Deterministic synthetic dataset definitions and fixtures for Phase 8C.

Enforces:
- Deterministic seeding (random seed = 42).
- Zero production or company PII.
- Multi-tenant partitioning (tenant-alpha, tenant-beta, tenant-gamma, etc.).
- Bounded memory footprint.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

DATASET_NAMESPACE = NAMESPACE_DNS


@dataclass(frozen=True)
class TenantBenchmarkData:
    tenant_id: str
    goals: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    schedule_jobs: list[dict[str, Any]] = field(default_factory=list)
    workflow_instances: list[dict[str, Any]] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    outbox_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkDataset:
    tier: str
    tenants: list[str]
    tenant_data: dict[str, TenantBenchmarkData]

    @property
    def total_goals(self) -> int:
        return sum(len(d.goals) for d in self.tenant_data.values())

    @property
    def total_tasks(self) -> int:
        return sum(len(d.tasks) for d in self.tenant_data.values())

    @property
    def total_resources(self) -> int:
        return sum(len(d.resources) for d in self.tenant_data.values())


def generate_benchmark_dataset(tier: str) -> BenchmarkDataset:
    """Generate a deterministic synthetic multi-tenant dataset for the specified tier."""
    tier_upper = tier.upper()
    configs = {
        "SMALL": {"num_tenants": 2, "goals_per_tenant": 2, "tasks_per_goal": 5, "resources": 5},
        "MEDIUM": {"num_tenants": 5, "goals_per_tenant": 5, "tasks_per_goal": 6, "resources": 10},
        "TARGET": {
            "num_tenants": 10,
            "goals_per_tenant": 10,
            "tasks_per_goal": 10,
            "resources": 20,
        },
        "STRESS": {
            "num_tenants": 25,
            "goals_per_tenant": 20,
            "tasks_per_goal": 10,
            "resources": 40,
        },
    }
    cfg = configs.get(tier_upper, configs["SMALL"])
    tenants = [f"perf-tenant-{idx:02d}" for idx in range(1, cfg["num_tenants"] + 1)]
    base_time = datetime(2026, 11, 1, 8, 0, 0, tzinfo=UTC)

    tenant_data: dict[str, TenantBenchmarkData] = {}
    for _t_idx, tenant_id in enumerate(tenants):
        goals = []
        plans = []
        tasks = []
        resources = []
        schedule_jobs = []
        workflow_instances = []
        audit_events = []
        outbox_events = []

        # Resources
        for r_idx in range(cfg["resources"]):
            res_id = str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-res-{r_idx}"))
            resources.append(
                {
                    "id": res_id,
                    "tenant_id": tenant_id,
                    "name": f"Resource-{r_idx:02d}",
                    "resource_type": "FACULTY" if r_idx % 2 == 0 else "ROOM",
                    "capacity": 30 if r_idx % 2 == 1 else 1,
                }
            )

        # Goals & Plans
        for g_idx in range(cfg["goals_per_tenant"]):
            goal_id = str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-goal-{g_idx}"))
            plan_id = str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-plan-{g_idx}"))
            goals.append(
                {
                    "id": goal_id,
                    "tenant_id": tenant_id,
                    "objective": f"Operational Goal {g_idx:02d} for {tenant_id}",
                    "scope": "Standard academic term execution",
                    "status": "ACTIVE",
                }
            )
            plans.append(
                {
                    "id": plan_id,
                    "tenant_id": tenant_id,
                    "goal_id": goal_id,
                    "name": f"Plan for Goal {g_idx:02d}",
                    "status": "VALIDATED",
                    "is_baseline": False,
                }
            )

            # Tasks
            for tk_idx in range(cfg["tasks_per_goal"]):
                task_id = str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-task-{g_idx}-{tk_idx}"))
                tasks.append(
                    {
                        "id": task_id,
                        "tenant_id": tenant_id,
                        "plan_id": plan_id,
                        "temporary_id": f"T{tk_idx + 1}",
                        "title": f"Task {tk_idx + 1} of Plan {g_idx}",
                        "estimated_duration_minutes": 45,
                        "status": "TODO",
                    }
                )

            # Audit & Outbox
            audit_events.append(
                {
                    "id": str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-audit-{g_idx}")),
                    "tenant_id": tenant_id,
                    "action": "GOAL_CREATED",
                    "entity_type": "GOAL",
                    "entity_id": goal_id,
                    "timestamp": (base_time + timedelta(minutes=g_idx)).isoformat(),
                }
            )
            outbox_events.append(
                {
                    "id": str(uuid5(DATASET_NAMESPACE, f"{tenant_id}-outbox-{g_idx}")),
                    "tenant_id": tenant_id,
                    "event_type": "goal.created.v1",
                    "payload": {"goal_id": goal_id, "tenant_id": tenant_id},
                    "status": "PUBLISHED",
                }
            )

        tenant_data[tenant_id] = TenantBenchmarkData(
            tenant_id=tenant_id,
            goals=goals,
            plans=plans,
            tasks=tasks,
            resources=resources,
            schedule_jobs=schedule_jobs,
            workflow_instances=workflow_instances,
            audit_events=audit_events,
            outbox_events=outbox_events,
        )

    return BenchmarkDataset(
        tier=tier_upper,
        tenants=tenants,
        tenant_data=tenant_data,
    )
