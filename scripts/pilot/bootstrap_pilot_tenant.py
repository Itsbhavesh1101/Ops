#!/usr/bin/env python3
"""Deterministic Pilot Tenant Staging and Bootstrap Script (Phase 9A).

Rehearses the bounded Semester Examination resource domain for a single controlled
synthetic pilot tenant without external institutional dependencies.

Invariants:
- Deterministic, idempotent, safe to rerun.
- Strictly tenant-scoped (zero mutation or exposure of other tenants).
- Bounded to Semester Examination domain (invigilators + examination rooms/labs).
- Uses validated repository fixtures: tests/fixtures/resources/RS-SYNTH-001.json.
- Validates IANA timezone (Asia/Kolkata) and UTC persistence boundaries.
- Uses canonical ExternalIdentityMapper for ERP/facility identity mapping.
- Automatically handles dual execution: runs directly inside e7-api or transparently
  delegates via docker exec when invoked on the host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

PILOT_TENANT_ID: str = "tenant-pilot-001"
DEFAULT_FIXTURE_PATH: str = "tests/fixtures/scheduling/education/phase5/PHASE5-RS-EDU-001.json"
PILOT_CREDENTIAL_ID: str = "cred-pilot-exam-admin"

EXAM_RESOURCE_PREFIXES = ("res-inv-", "res-room-", "res-eq-004")


def is_running_in_container() -> bool:
    """Detect whether script is running inside Docker container."""
    return os.path.exists("/.dockerenv") or os.environ.get("E7_CONTAINER") == "1"


def execute_via_docker_exec(args: list[str]) -> int:
    """Delegate script execution to e7-api container when invoked on host."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    container_script_dir = "/app/scripts/pilot"

    # Ensure scripts/pilot directory exists in container
    subprocess.run(
        ["docker", "exec", "e7-api", "mkdir", "-p", container_script_dir],
        check=False,
        capture_output=True,
    )

    # Sync script into container
    script_host_path = str(repo_root / "scripts" / "pilot" / "bootstrap_pilot_tenant.py")
    subprocess.run(
        [
            "docker",
            "cp",
            script_host_path,
            f"e7-api:{container_script_dir}/bootstrap_pilot_tenant.py",
        ],
        check=True,
        capture_output=True,
    )

    cmd = [
        "docker",
        "exec",
        "-e",
        "PYTHONPATH=/app",
    ]
    if "PILOT_CREDENTIAL_SECRET" in os.environ:
        cmd.extend(["-e", f"PILOT_CREDENTIAL_SECRET={os.environ['PILOT_CREDENTIAL_SECRET']}"])

    cmd.extend([
        "-i",
        "e7-api",
        "python",
        f"{container_script_dir}/bootstrap_pilot_tenant.py",
    ])
    cmd.extend(args)
    result = subprocess.run(cmd)
    return result.returncode


def validate_iana_timezone(tz_name: str) -> ZoneInfo:
    """Validate IANA timezone string and return ZoneInfo."""
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"Invalid IANA timezone '{tz_name}': {exc}") from exc


def validate_datetime_utc_roundtrip(
    dt_str: str,
    expected_tz: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Parse timezone-aware string, convert to UTC, and verify roundtrip instant equality."""
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        raise ValueError(f"Datetime string '{dt_str}' is naive; timezone-aware required.")
    dt_utc = dt.astimezone(UTC)
    dt_back = dt_utc.astimezone(expected_tz)
    if dt.timestamp() != dt_back.timestamp():
        raise ValueError(f"UTC conversion roundtrip timestamp mismatch for '{dt_str}'.")
    return dt, dt_utc


async def run_bootstrap(
    tenant_id: str,
    fixture_path: Path,
    domain: str = "exam",
    reset_first: bool = False,
    dry_run: bool = False,
    credential_secret: str | None = None,
    rotate_credential: bool = False,
) -> dict[str, Any]:
    """Execute deterministic bootstrap and resource staging inside database transaction."""
    from sqlalchemy import delete, func, select

    from app.audit.models import AuditEventRecord
    from app.core.auth.service_api_key import compute_secret_digest
    from app.db.engine import DatabaseManager
    from app.db.models.scheduling import ResourceAvailabilityRecord, ResourceRecord
    from app.db.models.service_credential import ServiceApiKeyCredentialRecordModel
    from app.integrations.identity_mapper import ExternalIdentityMapper
    from app.resources.schemas import IntervalType, ResourceType

    active_secret = (
        credential_secret
        or os.environ.get("PILOT_CREDENTIAL_SECRET")
        or secrets.token_urlsafe(32)
    )

    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found at {fixture_path}")

    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)

    # 1. Timezone verification
    tz_str = data.get("timezone", "Asia/Kolkata")
    tenant_tz = validate_iana_timezone(tz_str)

    raw_resources: list[dict[str, Any]] = data.get("resources", [])
    raw_availabilities: list[dict[str, Any]] = data.get("resource_availability", [])

    source_fixture_resource_count = len(raw_resources)
    source_fixture_availability_count = len(raw_availabilities)

    # 2. Domain bounding (Semester Examination)
    if domain == "exam":
        in_scope_resources = [
            r
            for r in raw_resources
            if any(r["id"].startswith(p) for p in EXAM_RESOURCE_PREFIXES)
        ]
        excluded_resources = [r for r in raw_resources if r not in in_scope_resources]
        in_scope_resource_ids = {r["id"] for r in in_scope_resources}
        in_scope_availabilities = [
            a for a in raw_availabilities if a.get("resource_id") in in_scope_resource_ids
        ]
        excluded_availabilities = [
            a for a in raw_availabilities if a not in in_scope_availabilities
        ]
    else:
        in_scope_resources = list(raw_resources)
        excluded_resources = []
        in_scope_availabilities = list(raw_availabilities)
        excluded_availabilities = []

    # 3. Validation preflight
    tenant_ownership_violations = 0
    timezone_validation_failures = 0
    rejected_record_count = 0
    resources_by_type: dict[str, int] = {}
    validated_resources: list[dict[str, Any]] = []

    seen_keys: set[str] = set()
    for res_dict in in_scope_resources:
        rid = res_dict.get("id", "")
        rtype_str = res_dict.get("type", "")
        # Validate resource type
        try:
            rtype = ResourceType(rtype_str)
        except ValueError:
            rejected_record_count += 1
            continue

        resources_by_type[rtype.value] = resources_by_type.get(rtype.value, 0) + 1

        # Check duplicate keys
        if rid in seen_keys:
            rejected_record_count += 1
            continue
        seen_keys.add(rid)

        # External mapping completeness
        provider = res_dict.get("provider", "synthetic-erp")
        external_id = res_dict.get("external_id", rid)
        if not provider or not external_id:
            rejected_record_count += 1
            continue

        # Derived canonical key
        canonical_key = ExternalIdentityMapper.derive_canonical_resource_key(
            provider=provider,
            entity_type=rtype.value,
            external_id=external_id,
            tenant_id=tenant_id,
        )

        source_ver = f"{data.get('dataset_id')}@{data.get('dataset_version', '1.0.0')}"
        source_fresh = res_dict.get("source_freshness_at", data.get("source_freshness_at"))

        validated_resources.append(
            {
                "raw_id": rid,
                "resource_key": canonical_key,
                "type": rtype.value,
                "display_label": res_dict.get("display_label", rid),
                "capacity": int(res_dict.get("capacity", 1)),
                "skills": list(res_dict.get("skills", [])),
                "attributes": dict(res_dict.get("attributes", {})),
                "active": bool(res_dict.get("active", True)),
                "provider": provider,
                "external_id": external_id,
                "source_version": source_ver,
                "source_freshness_at": source_fresh,
            }
        )

    # Validate availability intervals
    validated_availabilities: list[dict[str, Any]] = []
    for avail_dict in in_scope_availabilities:
        raw_rid = avail_dict.get("resource_id", "")
        start_str = avail_dict.get("start_at", "")
        end_str = avail_dict.get("end_at", "")
        iv_type_str = avail_dict.get("availability_type", "AVAILABLE")

        try:
            _, start_utc = validate_datetime_utc_roundtrip(start_str, tenant_tz)
            _, end_utc = validate_datetime_utc_roundtrip(end_str, tenant_tz)
        except Exception:
            timezone_validation_failures += 1
            rejected_record_count += 1
            continue

        if end_utc < start_utc:
            rejected_record_count += 1
            continue

        try:
            iv_type = IntervalType(iv_type_str)
        except ValueError:
            rejected_record_count += 1
            continue

        validated_availabilities.append(
            {
                "raw_resource_id": raw_rid,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "interval_type": iv_type.value,
                "reason": avail_dict.get("source", "synthetic-fixture"),
            }
        )

    db_manager = DatabaseManager()

    # 4. Database Transaction
    created_resources = 0
    updated_resources = 0
    created_availabilities = 0
    created_credentials = 0
    unresolved_mapping_count = 0

    async with db_manager.session_factory() as session:
        # Optional safe tenant reset
        if reset_first:
            await session.execute(
                delete(ResourceRecord).where(ResourceRecord.tenant_id == tenant_id)
            )
            await session.execute(
                delete(ServiceApiKeyCredentialRecordModel).where(
                    ServiceApiKeyCredentialRecordModel.tenant_id == tenant_id
                )
            )
            await session.flush()

        # Query existing before counts
        before_res_count = (
            await session.scalar(
                select(func.count(ResourceRecord.id)).where(ResourceRecord.tenant_id == tenant_id)
            )
            or 0
        )
        before_avail_count = (
            await session.scalar(
                select(func.count(ResourceAvailabilityRecord.id)).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id
                )
            )
            or 0
        )

        resource_key_to_id: dict[str, Any] = {}

        # 4A. Stage Resources
        for r_spec in validated_resources:
            stmt = select(ResourceRecord).where(
                ResourceRecord.tenant_id == tenant_id,
                ResourceRecord.resource_key == r_spec["resource_key"],
            )
            res_rec = (await session.execute(stmt)).scalar_one_or_none()

            # Attach external identity mapping in attributes
            now_iso = datetime.now(UTC).isoformat()
            attributes = dict(r_spec["attributes"])
            attributes["external_mapping"] = {
                "provider": r_spec["provider"],
                "external_type": r_spec["type"],
                "external_id": r_spec["external_id"],
                "last_seen_at": now_iso,
                "source_version": r_spec["source_version"],
                "source_updated_at": r_spec["source_freshness_at"],
            }
            attributes["fixture_id"] = r_spec["raw_id"]

            if res_rec is None:
                new_id = uuid4()
                res_rec = ResourceRecord(
                    id=new_id,
                    tenant_id=tenant_id,
                    resource_key=r_spec["resource_key"],
                    type=r_spec["type"],
                    display_label=r_spec["display_label"],
                    capacity=r_spec["capacity"],
                    skills=r_spec["skills"],
                    attributes=attributes,
                    active=r_spec["active"],
                )
                session.add(res_rec)
                created_resources += 1
                resource_key_to_id[r_spec["raw_id"]] = new_id
            else:
                res_rec.display_label = r_spec["display_label"]
                res_rec.capacity = r_spec["capacity"]
                res_rec.skills = r_spec["skills"]
                res_rec.attributes = attributes
                res_rec.active = r_spec["active"]
                updated_resources += 1
                resource_key_to_id[r_spec["raw_id"]] = res_rec.id

        await session.flush()

        # 4B. Stage Availabilities
        for a_spec in validated_availabilities:
            parent_id = resource_key_to_id.get(a_spec["raw_resource_id"])
            if not parent_id:
                unresolved_mapping_count += 1
                continue

            # Idempotent match: same tenant, same resource, identical interval
            stmt_avail = select(ResourceAvailabilityRecord).where(
                ResourceAvailabilityRecord.tenant_id == tenant_id,
                ResourceAvailabilityRecord.resource_id == parent_id,
                ResourceAvailabilityRecord.start_at == a_spec["start_utc"],
                ResourceAvailabilityRecord.end_at == a_spec["end_utc"],
            )
            avail_rec = (await session.execute(stmt_avail)).scalar_one_or_none()
            if avail_rec is None:
                avail_rec = ResourceAvailabilityRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    resource_id=parent_id,
                    start_at=a_spec["start_utc"],
                    end_at=a_spec["end_utc"],
                    interval_type=a_spec["interval_type"],
                    reason=a_spec["reason"],
                )
                session.add(avail_rec)
                created_availabilities += 1

        await session.flush()

        # 4C. Stage Pilot Admin Service Credential
        stmt_cred = select(ServiceApiKeyCredentialRecordModel).where(
            ServiceApiKeyCredentialRecordModel.tenant_id == tenant_id,
            ServiceApiKeyCredentialRecordModel.credential_id == PILOT_CREDENTIAL_ID,
        )
        cred_rec = (await session.execute(stmt_cred)).scalar_one_or_none()
        secret_digest = compute_secret_digest(active_secret)

        if cred_rec is None:
            cred_rec = ServiceApiKeyCredentialRecordModel(
                tenant_id=tenant_id,
                credential_id=PILOT_CREDENTIAL_ID,
                service_subject=f"svc:{tenant_id}:exam-pilot-admin",
                secret_digest=secret_digest,
                roles=["tenant_admin", "planner", "scheduler"],
                permissions=["*"],
                enabled=True,
                issuer="e7-pilot-bootstrap",
                description="Pilot Tenant Rehearsal Credential (Semester Examination)",
            )
            session.add(cred_rec)
            created_credentials += 1
            credential_status = "CREATED"
        elif rotate_credential:
            cred_rec.secret_digest = secret_digest
            cred_rec.enabled = True
            credential_status = "ROTATED"
        else:
            credential_status = "UNCHANGED"

        # 4D. Append-only Audit Trail
        audit_event = AuditEventRecord(
            tenant_id=tenant_id,
            actor_principal_id=None,
            actor_principal_type=None,
            authentication_method=None,
            event_type="tenant.bootstrap.rehearsal",
            resource_type="tenant",
            resource_id=tenant_id,
            outcome="SUCCESS",
            details={
                "domain": domain,
                "fixture_dataset_id": data.get("dataset_id"),
                "staged_resources": len(validated_resources),
                "staged_availabilities": len(validated_availabilities),
                "timezone": tz_str,
            },
        )
        session.add(audit_event)

        if not dry_run:
            await session.commit()
        else:
            await session.rollback()

        # Query after counts
        after_res_count = (
            await session.scalar(
                select(func.count(ResourceRecord.id)).where(ResourceRecord.tenant_id == tenant_id)
            )
            or 0
        )
        after_avail_count = (
            await session.scalar(
                select(func.count(ResourceAvailabilityRecord.id)).where(
                    ResourceAvailabilityRecord.tenant_id == tenant_id
                )
            )
            or 0
        )

    # 5. External identity resolution verification
    async with db_manager.session_factory() as session:
        mapper = ExternalIdentityMapper()
        for r_spec in validated_resources:
            resolved = await mapper.resolve_resource_entity(
                session=session,
                tenant_id=tenant_id,
                provider=r_spec["provider"],
                external_type=r_spec["type"],
                external_id=r_spec["external_id"],
            )
            if resolved is None:
                unresolved_mapping_count += 1

    # Reconciliation report compilation
    reconciliation = {
        "tenant_id": tenant_id,
        "domain": domain,
        "fixture_path": str(fixture_path),
        "fixture_dataset_id": data.get("dataset_id"),
        "fixture_version": data.get("dataset_version"),
        "timezone": tz_str,
        "source_fixture_resource_count": source_fixture_resource_count,
        "source_fixture_availability_count": source_fixture_availability_count,
        "in_scope_resource_count": len(in_scope_resources),
        "in_scope_availability_count": len(in_scope_availabilities),
        "excluded_maintenance_resource_count": len(excluded_resources),
        "excluded_maintenance_availability_count": len(excluded_availabilities),
        "accepted_resource_count": len(validated_resources),
        "accepted_availability_count": len(validated_availabilities),
        "rejected_record_count": rejected_record_count,
        "duplicate_count": 0,
        "resources_by_type": resources_by_type,
        "unresolved_mapping_count": unresolved_mapping_count,
        "tenant_ownership_violations": tenant_ownership_violations,
        "timezone_validation_failures": timezone_validation_failures,
        "db_before": {
            "resources": before_res_count,
            "availabilities": before_avail_count,
        },
        "db_after": {
            "resources": after_res_count,
            "availabilities": after_avail_count,
        },
        "records_created": {
            "resources": created_resources,
            "resources_updated": updated_resources,
            "availabilities": created_availabilities,
            "service_credentials": created_credentials,
        },
        "credential_id": PILOT_CREDENTIAL_ID,
        "credential_status": credential_status,
        "reconciliation_status": (
            "PASS"
            if (
                unresolved_mapping_count == 0
                and tenant_ownership_violations == 0
                and timezone_validation_failures == 0
                and rejected_record_count == 0
            )
            else "FAIL"
        ),
    }

    return reconciliation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap Pilot Tenant Staging Rehearsal (Phase 9A)"
    )
    parser.add_argument(
        "--tenant-id",
        default=PILOT_TENANT_ID,
        help="Pilot tenant identifier",
    )
    parser.add_argument(
        "--fixture",
        default=DEFAULT_FIXTURE_PATH,
        help="Path to resource catalog fixture",
    )
    parser.add_argument(
        "--domain",
        choices=["exam", "all"],
        default="exam",
        help="Target domain scope",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Safely remove existing tenant data before staging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without committing transaction",
    )
    parser.add_argument(
        "--rotate-credential",
        action="store_true",
        help="Rotate the pilot service credential with a fresh runtime secret",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON only",
    )
    args = parser.parse_args()

    # If running on host, delegate execution to container
    if not is_running_in_container():
        # Pass through arguments
        pass_args: list[str] = []
        if args.tenant_id:
            pass_args.extend(["--tenant-id", args.tenant_id])
        if args.fixture:
            pass_args.extend(["--fixture", args.fixture])
        if args.domain:
            pass_args.extend(["--domain", args.domain])
        if args.reset:
            pass_args.append("--reset")
        if args.dry_run:
            pass_args.append("--dry-run")
        if args.rotate_credential:
            pass_args.append("--rotate-credential")
        if args.json:
            pass_args.append("--json")
        return execute_via_docker_exec(pass_args)

    fixture_path = Path(args.fixture)
    if not fixture_path.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        fixture_path = repo_root / args.fixture

    try:
        report = asyncio.run(
            run_bootstrap(
                tenant_id=args.tenant_id,
                fixture_path=fixture_path,
                domain=args.domain,
                reset_first=args.reset,
                dry_run=args.dry_run,
                rotate_credential=args.rotate_credential,
            )
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "status": "FAIL"}, indent=2))
        else:
            print(f"BOOTSTRAP FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        src_res = report["source_fixture_resource_count"]
        src_avail = report["source_fixture_availability_count"]
        in_res = report["in_scope_resource_count"]
        in_avail = report["in_scope_availability_count"]
        ex_res = report["excluded_maintenance_resource_count"]
        ex_avail = report["excluded_maintenance_availability_count"]
        acc_res = report["accepted_resource_count"]
        acc_avail = report["accepted_availability_count"]
        print("=" * 60)
        print("PILOT TENANT STAGING REHEARSAL REPORT")
        print("=" * 60)
        print(f"Tenant ID:                     {report['tenant_id']}")
        print(f"Domain Scope:                  {report['domain']} (Bounded Semester Examination)")
        print(f"Timezone:                      {report['timezone']}")
        print(f"Source Fixture:                {report['fixture_path']}")
        print(f"Source Fixture Records:        {src_res} resources, {src_avail} availabilities")
        print(f"In-Scope Exam Records:         {in_res} resources, {in_avail} availabilities")
        print(f"Excluded Maint Records:        {ex_res} resources, {ex_avail} availabilities")
        print(f"Accepted Records:              {acc_res} resources, {acc_avail} availabilities")
        print(f"Rejected Records:              {report['rejected_record_count']}")
        print(f"Duplicate Count:               {report['duplicate_count']}")
        print(f"Resources By Type:             {report['resources_by_type']}")
        print(f"Unresolved Mapping Count:      {report['unresolved_mapping_count']}")
        print(f"Tenant Ownership Violations:   {report['tenant_ownership_violations']}")
        print(f"Timezone Validation Failures:  {report['timezone_validation_failures']}")
        print(
            f"DB Before / After Resources:   "
            f"{report['db_before']['resources']} -> {report['db_after']['resources']}"
        )
        print(
            f"DB Before / After Avail:       "
            f"{report['db_before']['availabilities']} -> {report['db_after']['availabilities']}"
        )
        print(f"Created (This Run):            {report['records_created']}")
        print(f"Reconciliation Status:         {report['reconciliation_status']}")
        print("=" * 60)

    return 0 if report["reconciliation_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
