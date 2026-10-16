"""Benchmark fixtures, authentication personas, and test setup for Phase 8C.

Provides:
- Multi-tenant authenticated principals (Planner, Operator, Auditor, Admin) for perf-tenants.
- Fast, secure ServiceApiKey registration for benchmark clients.
- Clean setup and teardown for benchmark tenant data.
"""
# ruff: noqa: E501

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from app.core.auth.dependency import get_default_authenticator
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.core.auth.service_api_key import (
    ServiceApiKeyCredentialRecord,
    compute_secret_digest,
)
from app.db.engine import DatabaseManager
from app.main import app
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings

PERF_SECRET_SALT = "perf_secret_benchmark_fixed_length_45_bytes__"


def register_perf_principal(
    tenant_id: str,
    role: str = "planner",
    principal_id_suffix: str = "main",
) -> tuple[str, str]:
    """Register a deterministic service API key for a performance test tenant.

    Returns:
        tuple[str, str]: (credential_id, raw_api_key)
    """
    auth = get_default_authenticator()
    cred_id = f"perf_{tenant_id.replace('-', '_')}_{role[:4]}"
    raw_secret = f"{PERF_SECRET_SALT[:35]}_{role[:4]}_{tenant_id[-4:]}"
    if len(raw_secret) < 43:
        raw_secret = raw_secret.ljust(45, "x")

    digest = compute_secret_digest(raw_secret)
    record = ServiceApiKeyCredentialRecord(
        credential_id=cred_id,
        service_subject=f"perf_svc_{tenant_id}_{role}",
        secret_digest=digest,
    )
    auth.credential_store.add_record(record)

    roles_map: dict[str, set[str]] = {
        "planner": {"planner", "operator", "tenant_admin"},
        "operator": {"operator", "scheduler", "tenant_admin"},
        "auditor": {"auditor"},
        "admin": {"admin", "tenant_admin", "planner", "operator", "scheduler"},
    }
    assigned_roles = roles_map.get(role, {"planner", "tenant_admin"})

    principal = AuthenticatedPrincipal(
        principal_id=f"perf-principal-{tenant_id}-{role}-{principal_id_suffix}",
        principal_type=PrincipalType.SERVICE,
        tenant_id=tenant_id,
        roles=assigned_roles,
        permissions={"goal:create", "plan:create", "task:update", "plan:approve", "schedule:read"},
        authentication_method=AuthenticationMethod.SERVICE_API_KEY,
    )
    auth.identity_resolver.register_service_mapping(cred_id, principal)

    raw_api_key = f"e7sk_{cred_id}.{raw_secret}"
    return cred_id, raw_api_key


@asynccontextmanager
async def create_perf_client(
    tenant_id: str = "perf-tenant-01",
    role: str = "planner",
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an authenticated AsyncClient bound to the FastAPI application for load testing."""
    _, raw_api_key = register_perf_principal(tenant_id=tenant_id, role=role)
    headers = {
        "X-E7-Service-Key": raw_api_key,
        "Accept": "application/json",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        yield client


def get_benchmark_flowable_adapter() -> FlowableAdapter:
    """Return FlowableAdapter configured for the local Flowable 7.x container."""
    settings = FlowableSettings(
        base_url="http://localhost:8080/flowable-rest/service",
        username="rest-admin",
        password=SecretStr("test"),
        timeout_seconds=15.0,
        max_retries=1,
    )
    return FlowableAdapter(settings=settings)


async def cleanup_perf_tenant_data(tenant_prefix: str = "perf-tenant-") -> None:
    """Safely clean up synthetic benchmark tenant rows from PostgreSQL."""
    db = DatabaseManager()
    async with db.session_factory() as session:
        async with session.begin():
            # Delete in proper foreign key order
            # 1. schedule_assignments, schedules, schedule_jobs
            await session.execute(
                text(
                    f"DELETE FROM schedule_assignments WHERE schedule_id IN (SELECT id FROM schedules WHERE tenant_id LIKE '{tenant_prefix}%')"
                )
            )
            await session.execute(
                text(f"DELETE FROM schedules WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM schedule_jobs WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            # 2. workflow mappings, instances, definitions
            await session.execute(
                text(f"DELETE FROM workflow_task_mappings WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM workflow_instances WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM workflow_definitions WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            # 3. tasks, milestones, plans, goals
            await session.execute(
                text(f"DELETE FROM task_dependencies WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM task_risks WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM tasks WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM milestones WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM plans WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            await session.execute(
                text(f"DELETE FROM goals WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            # 4. resources
            await session.execute(
                text(
                    f"DELETE FROM resource_availabilities WHERE resource_id IN (SELECT id FROM resources WHERE tenant_id LIKE '{tenant_prefix}%')"
                )
            )
            await session.execute(
                text(f"DELETE FROM resources WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
            # 5. outbox, inbox, idempotency (audit_events is append-only by security policy)
            await session.execute(
                text(
                    f"DELETE FROM outbox_events WHERE envelope->>'tenant_id' LIKE '{tenant_prefix}%'"
                )
            )
            await session.execute(
                text("DELETE FROM inbox_messages WHERE consumer_name LIKE 'perf.%'")
            )
            await session.execute(
                text(f"DELETE FROM idempotency_records WHERE tenant_id LIKE '{tenant_prefix}%'")
            )
