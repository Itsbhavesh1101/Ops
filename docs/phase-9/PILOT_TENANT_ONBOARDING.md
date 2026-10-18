# Pilot Tenant Onboarding & Staging Rehearsal (Phase 9A)

## 1. Executive Summary & Objective

This document records the **Phase 9A Pilot Infrastructure & Tenant Staging Rehearsal** for the E-7 Operations Engine. Phase 9A delivers a deterministic, reproducible staging rehearsal for a single controlled pilot tenant (`tenant-pilot-001`) within the bounded **Semester Examination** domain, operating entirely under the approved **Restricted Pilot Scope**.

All tests, preflight validations, and staging operations execute without live external institutional dependencies (such as institutional MySQL or production E8 notification endpoints).

---

## 2. Restricted Pilot Scope & Governance Baseline

| Control Property | Approved Value / Status | Governance Notes |
|:---|:---|:---|
| **Restricted Pilot Engineering Readiness** | `READY` | Approved engineering continuation layer |
| **Phase 8 Production Readiness** | `NOT_READY` | Blocked canonically on institutional DB grants & E8 API |
| **MySQL Live Read Verification** | `DEFERRED_FROM_RESTRICTED_PILOT_SCOPE` | Zero live institutional DB connection permitted |
| **E8 Notification Integration** | `DEFERRED_FROM_RESTRICTED_PILOT_SCOPE` | Zero external notification dependency required |
| **E8 Local Contract Dispatch** | `PASS` | Local dispatch contract verified |
| **Pilot Gateway Rate Limiting** | `PASS` | Verified via `scripts/pilot/verify_gateway_rate_limit.py` |
| **Frozen Phase-8 RC Tag** | `phase8-rc-38d080fadab8` | Annotated tag points to `03e6305256db3d838b472aaac50893ddb513414e` |
| **Frozen Phase-8 RC Content ID** | `38d080fadab8f0f98e60f125d0ade060d13a3e36686252670e482c6c4a784897` | Unmodified |
| **Frozen E-7 Docker Image** | `e7-operations-engine:rc-38d080fadab8` | Unmodified |
| **Alembic Database Version** | `0014 (head)` | Zero schema changes, no migration 0015 created |
| **Public Table Count** | `27` (26 application + `alembic_version`) | Schema verified drift-free |

---

## 3. Pilot Tenant Identity & Semantic Domain

- **Pilot Tenant Identifier**: `tenant-pilot-001`
- **Tenant Display Name**: `Restricted Pilot / Semester Examination Rehearsal`
- **Domain Scope**: `Semester Examination` (Bounded)
  - Resources: Invigilators and Examination Rooms / Laboratories only.
  - Excluded: Facility Maintenance technicians, mobile lifts, diagnostic equipment, and unmapped administrative domains.
- **Canonical Timezone**: `Asia/Kolkata` (IANA canonical identifier)
- **Administrative Service Key ID**: `cred-pilot-exam-admin`
- **Roles**: `["tenant_admin", "planner", "scheduler"]`
- **Permissions**: `["*"]`

---

## 4. Source Fixture & Staging Mapping Summary

The staging rehearsal ingests the repository's canonical education resource catalog:
`tests/fixtures/scheduling/education/phase5/PHASE5-RS-EDU-001.json` (Dataset ID: `PHASE5-RS-EDU-001`, Version `1.0.0`).

### Staged In-Scope Resources (Bounded Semester Examination Domain)

| Fixture ID | Canonical E-7 Resource Key | Type | Display Label | Capacity | Skills / Certifications | Role / External ID |
|:---|:---|:---|:---|:---:|:---|:---|
| `res-inv-001` | `urn:e7:tenant-pilot-001:synthetic-erp:person:SYN-ERP-INV-001` | `PERSON` | Invigilator Alpha | 1 | `INVIGILATION_GENERAL`, `SCIENCE` | `INVIGILATOR` (`SYN-ERP-INV-001`) |
| `res-inv-002` | `urn:e7:tenant-pilot-001:synthetic-erp:person:SYN-ERP-INV-002` | `PERSON` | Invigilator Beta | 1 | `INVIGILATION_GENERAL`, `MATHEMATICS` | `INVIGILATOR` (`SYN-ERP-INV-002`) |
| `res-inv-003` | `urn:e7:tenant-pilot-001:synthetic-erp:person:SYN-ERP-INV-003` | `PERSON` | Invigilator Gamma | 1 | `INVIGILATION_GENERAL`, `LANGUAGE` | `INVIGILATOR` (`SYN-ERP-INV-003`) |
| `res-inv-004` | `urn:e7:tenant-pilot-001:synthetic-erp:person:SYN-ERP-INV-004` | `PERSON` | Invigilator Delta | 1 | `INVIGILATION_GENERAL`, `ACCESSIBILITY_SUPPORT` | `INVIGILATOR` (`SYN-ERP-INV-004`) |
| `res-room-001` | `urn:e7:tenant-pilot-001:synthetic-erp:room:SYN-ERP-ROOM-001` | `ROOM` | Exam Room Alpha | 40 | *(Projector equipment attribute)* | `CLASSROOM` (`SYN-ERP-ROOM-001`) |
| `res-room-002` | `urn:e7:tenant-pilot-001:synthetic-erp:room:SYN-ERP-ROOM-002` | `ROOM` | Exam Room Beta | 32 | *(Standard room)* | `CLASSROOM` (`SYN-ERP-ROOM-002`) |
| `res-room-003` | `urn:e7:tenant-pilot-001:synthetic-erp:room:SYN-ERP-ROOM-003` | `ROOM` | Science Lab Alpha | 24 | *(Lab benches & projector)* | `SCIENCE_LAB` (`SYN-ERP-ROOM-003`) |
| `res-eq-004` | `urn:e7:tenant-pilot-001:synthetic-erp:equipment:SYN-ERP-EQ-004` | `EQUIPMENT` | Mobile Science Lab Kit | 1 | `LAB_KIT` | `LAB_KIT` (`SYN-ERP-EQ-004`) |

### Staged Availability Windows
Each of the 8 in-scope examination resources is provisioned with 5 full daily operational availability intervals:
- **Horizon**: `2026-11-09` to `2026-11-13` (5 days)
- **Local Daily Window**: `08:00:00+05:30` to `17:00:00+05:30`
- **UTC Stored Instant**: `02:30:00Z` to `11:30:00Z`
- **Interval Semantics**: `AVAILABLE`
- **Total Intervals Staged**: 40 intervals (8 resources × 5 days)

### Domain Exclusions (Maintained Outside Pilot Scope)
The following resources from `PHASE5-RS-EDU-001.json` are excluded from staging because they belong to the Facility Maintenance domain:
- `res-tech-001` through `res-tech-004` (4 maintenance technicians)
- `res-eq-001` through `res-eq-003` (3 maintenance equipment units)
- 35 maintenance availability intervals

---

## 5. Reconciliation & Determinism Results

| Metric | Source Fixture | Evaluated In-Scope | Accepted | Rejected | Discrepancy / Violations |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Total Resources** | 15 | 8 | 8 | 0 | 0 |
| **Total Availabilities** | 75 | 40 | 40 | 0 | 0 |
| **Resources by Type: PERSON** | 8 | 4 | 4 | 0 | 0 |
| **Resources by Type: ROOM** | 3 | 3 | 3 | 0 | 0 |
| **Resources by Type: EQUIPMENT** | 4 | 1 | 1 | 0 | 0 |
| **Unresolved External Mappings** | N/A | 8 | 8 resolved | 0 | **0** |
| **Tenant Ownership Violations** | N/A | 48 records | 48 records | 0 | **0** |
| **Timezone Validation Failures** | N/A | 80 timestamps | 80 timestamps | 0 | **0** |
| **Idempotent Rerun Duplicate Count** | N/A | Second execution | 0 created | 0 | **0** |
| **Reconciliation Outcome** | — | — | — | — | **PASS** |

---

## 6. Pilot Topology & Infrastructure Preflight

The pilot architecture enforces strict edge ingress through the loopback NGINX reverse proxy (`e7-pilot-gateway`) while preventing direct external access to internal backing services:

```
[Host / Pilot Client]
        │
        ▼ (Port 8088 / 127.0.0.1 loopback only)
┌───────────────────────────────────────────────┐
│ e7-pilot-gateway (NGINX edge rate limiter)   │
└───────────────────────┬───────────────────────┘
                        │ (Internal Docker network)
                        ▼
┌───────────────────────────────────────────────┐
│ e7-api (FastAPI, frozen RC image)             │
│ [No public host port exposure]                │
└───┬───────────────────┬───────────────────┬───┘
    │                   │                   │
    ▼                   ▼                   ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ e7-postgres  │ │ e7-flowable  │ │ e7-rabbitmq  │
│ [No host port│ │ [Internal    │ │ [Internal    │
│  exposure]   │ │  API wire]   │ │  transport]  │
└──────────────┘ └──────────────┘ └──────────────┘
```

### Preflight Endpoint Verifications
- `GET http://127.0.0.1:8088/api/v1/health` → `HTTP 200 OK` (`{"status":"ok","service":"operations-engine","environment":"local"}`)
- `GET http://127.0.0.1:8088/api/v1/ready` → `HTTP 200 OK` (`{"status":"ready","service":"operations-engine","environment":"local"}`)
- Internal port isolation: PostgreSQL port 5432 and API port 8000 refuse direct host connection; external traffic enters exclusively via gateway port 8088.

---

## 7. Operational Runbook & Execution Commands

### 7.1 Verify Pilot Topology Configuration
```powershell
docker compose -f docker-compose.yml -f docker-compose.pilot.yml config
```

### 7.2 Bootstrap Pilot Tenant (Idempotent Rerun Safe)
```powershell
python scripts/pilot/bootstrap_pilot_tenant.py --tenant-id tenant-pilot-001 --domain exam
```
*Outputs JSON when run with `--json`.*

### 7.3 Rate Limit Edge Regression Test
```powershell
python scripts/pilot/verify_gateway_rate_limit.py
```

### 7.4 Execute Preflight Automation
```powershell
python scripts/pilot/verify_phase9a_preflight.py
```
*Outputs complete preflight report verifying containers, gateway routing, tenant staging, and isolation tests.*

### 7.5 Run Tenant Isolation Test Suite
```powershell
.\.venv\Scripts\pytest.exe -v tests/pilot/test_pilot_tenant_isolation.py
```

---

## 8. Rollback & Reset Procedure for Rehearsal Data

If staging rehearsal data needs to be rolled back or reset prior to subsequent test runs, execute:

```powershell
python scripts/pilot/bootstrap_pilot_tenant.py --tenant-id tenant-pilot-001 --reset
```

### Safe Reset Invariants:
1. **Targeted Deletion**: Deletes *only* rows where `tenant_id = 'tenant-pilot-001'` from `resources` (cascading to `resource_availabilities`) and `service_api_credentials`.
2. **Non-Destructive Boundary**:
   - `DROP DATABASE` is strictly prohibited.
   - `DROP TABLE` is strictly prohibited.
   - `TRUNCATE` on shared tables is strictly prohibited.
   - Data belonging to other tenants is never modified or deleted.
   - Append-only `audit_events` rows remain immutable per database triggers (`trg_audit_events_prevent_mutation`).

---

## 9. Known Limitations & Phase 9B Prerequisites

1. **Restricted Scope**: Live institutional MySQL synchronization and E8 notification webhooks remain intentionally deferred.
2. **Schema Invariant**: Alembic remains on revision `0014 (head)`. No schema changes were made.
3. **Phase 9B Gate**: Phase 9A is complete. Transitioning to Phase 9B requires user review and explicit phase kickoff.
