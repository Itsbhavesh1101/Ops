# E-7 Operations Engine — Institutional Database Administration Handover

**Document:** Institutional MySQL Account Provisioning & Security Governance  
**Target Audience:** Institutional Database Administration (DBA) Team  
**System:** IR Infotech E-7 Operations Engine (Timetable & Academic Operations Engine)  
**Classification Key:** `MYSQL_LIVE_READ_VERIFICATION`  
**Current Status:** `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS`  
**Security Posture:** Zero Database Mutation — Strictly `SELECT`-Only Engine Privilege  

---

## 1. Executive Summary & Security Principles

The E-7 Operations Engine interfaces with the upstream institutional MySQL ERP/SIS database (`iredu`) exclusively to ingest contextual operational data: academic session boundaries, course catalogs, section rosters, sanitized staff directories, and published timetable commitments.

Under institutional security policy and **ADR-007**, E-7 operates under a strict **Zero-Mutation / Read-Only** architecture:
1. **Engine-Level Least Privilege:** E-7 must connect using a dedicated service account granted `SELECT`-only privileges on specifically enumerated tables. All DML (`INSERT`, `UPDATE`, `DELETE`), DDL (`CREATE`, `ALTER`, `DROP`), and administrative privileges are strictly prohibited.
2. **Defense-in-Depth:** In addition to database-level privileges, E-7 automatically issues `SET SESSION TRANSACTION READ ONLY` on connection acquisition and enforces an in-process regular expression gate that blocks any query containing mutating SQL keywords.
3. **Strict Data Minimization & Privacy:** Student records are **never** extracted. E-7 queries only mathematical aggregates (headcounts per section). Faculty salary, payroll, and private banking details are excluded at the query projection level.
4. **Predeclared Parameterized SQL Only:** No dynamic SQL, arbitrary SQL, or LLM-generated queries are ever executed against the institutional database.
5. **No Direct Execution by E-7 Team:** The E-7 engineering team will never execute GRANT statements or mutate the company database; all account provisioning remains under institutional DBA ownership.

---

## 2. Table Reconciliation & Schema Audit Findings

An exhaustive audit of the 247-table institutional schema inventory (`MYSQL_SCHEMA_INVENTORY.json`) against the active adapter implementation ([mysql_adapter.py](file:///f:/operations-engine/app/integrations/erp/mysql_adapter.py)) resolved discrepancies in preliminary documentation:

* **Tables Formally Removed (Do Not Exist in Institutional Schema):**
  - `iredu.academic_session`: Table does not exist in schema. Academic sessions are stored in `iredu.sessions` (`MySqlIreduAdapter.get_academic_sessions`).
  - `iredu.student_cohort`: Table does not exist in schema. Student cohorts and headcounts are computed from relational joins across `classes`, `sections`, `class_sections`, and `student_session` (`MySqlIreduAdapter.get_student_headcounts`).
  - `iredu.examination_session`: Table does not exist in schema. Examination schedules in the dump are stored in exam tables not queried by E-7 timetable operations.
* **Table Formally Added (Actively Queried):**
  - `iredu.subject_timetable`: Actively queried by `MySqlIreduAdapter.get_timetable_commitments` to identify published commitments and room allocations.

---

## 3. Verified Minimum 11 Active Tables

The following **11 tables** represent the complete, verified set of tables queried by the production adapter [MySqlIreduAdapter](file:///f:/operations-engine/app/integrations/erp/mysql_adapter.py):

| # | Table Name | Adapter Method | Projected Fields / Purpose |
|---|---|---|---|
| 1 | `iredu.sch_settings` | `get_organization_context` | `id`, `name`, `email`, `phone`, `address`, `session_id` (Institutional header) |
| 2 | `iredu.sessions` | `get_academic_sessions` | `id`, `session`, `is_active` (Academic year boundaries) |
| 3 | `iredu.classes` | `get_classes_and_sections` | `id`, `class`, `is_active` (Grade / class definitions) |
| 4 | `iredu.sections` | `get_classes_and_sections` | `id`, `section` (Section division labels) |
| 5 | `iredu.class_sections` | `get_classes_and_sections` | `id`, `class_id`, `section_id` (Class-to-section cohort linkage) |
| 6 | `iredu.subjects` | `get_subjects` | `id`, `name`, `code`, `type` (Course catalog & Theory/Practical type) |
| 7 | `iredu.staff` | `get_staff_roster` | `id`, `name`, `surname`, `qualification`, `work_exp`, `is_active`, `department`, `designation` (Sanitized directory; zero salary) |
| 8 | `iredu.department` | `get_staff_roster` | `id`, `department_name` (Departmental structure) |
| 9 | `iredu.staff_designation`| `get_staff_roster` | `id`, `designation` (Professional title) |
| 10| `iredu.student_session` | `get_student_headcounts` | Relational join only (`COUNT(ss.id)`, `SUM(is_active)`) — **AGGREGATE HEADCOUNTS ONLY** |
| 11| `iredu.subject_timetable`| `get_timetable_commitments`| `id`, `staff_id`, `subject_group_subject_id`, `class_id`, `section_id`, `day`, `start_time`, `end_time`, `room_no` |

---

## 4. Conditional Support Tables (Pending Future Approval)

The following **4 tables** were mapped during Phase 0 Task Intelligence architectural design, but are **not** currently queried by the active `MySqlIreduAdapter`:

| # | Table Name | Purpose in Data Mapping | Runtime Necessity Status |
|---|---|---|---|
| 1 | `iredu.subject_groups` | Curricular subject groupings | **CONDITIONAL** (Deferred until multi-track subject resolution is required) |
| 2 | `iredu.subject_group_subjects` | Curricular group to subject linkage | **CONDITIONAL** (Deferred until multi-track subject resolution is required) |
| 3 | `iredu.roles` | RBAC role definitions | **CONDITIONAL** (Deferred; E-7 enforces internal canonical RBAC) |
| 4 | `iredu.staff_roles` | Staff to RBAC role mappings | **CONDITIONAL** (Deferred; E-7 enforces internal canonical RBAC) |

> [!IMPORTANT]
> The DBA team is requested to provision privileges for **Tier 1 (11 active tables)** first. Tier 2 tables should remain ungranted until runtime necessity is formally demonstrated and approved by the Architecture Board.

---

## 5. Provisioning Script Template for Institutional DBA

Below is the illustrative template for the institutional DBA to execute on the institutional MySQL database server.

```sql
-- ============================================================================
-- INSTITUTIONAL DBA PROVISIONING SCRIPT (Illustrative Template)
-- Target Database: iredu (MySQL 8.0+)
-- ============================================================================

-- 1. Create dedicated read-only service account
-- (Replace '<vpc_cidr>' with the authorized private VPC subnet of the E-7 API cluster)
-- (Replace '<strong_random_password>' with an enterprise vault-managed secret)
CREATE USER IF NOT EXISTS 'e7_ro_sync'@'<vpc_cidr>' 
  IDENTIFIED BY '<strong_random_password>'
  REQUIRE SSL;

-- 2. Restrict global capabilities to USAGE only (no global SELECT, no administrative grants)
GRANT USAGE ON *.* TO 'e7_ro_sync'@'<vpc_cidr>';

-- 3. Grant strictly SELECT-only privileges on the verified 11 active tables
GRANT SELECT ON iredu.sch_settings TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.sessions TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.classes TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.sections TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.class_sections TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.subjects TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.staff TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.department TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.staff_designation TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.student_session TO 'e7_ro_sync'@'<vpc_cidr>';
GRANT SELECT ON iredu.subject_timetable TO 'e7_ro_sync'@'<vpc_cidr>';

-- 4. Apply connection limits to protect upstream ERP from resource exhaustion
ALTER USER 'e7_ro_sync'@'<vpc_cidr>' WITH 
  MAX_QUERIES_PER_HOUR 0
  MAX_UPDATES_PER_HOUR 0
  MAX_CONNECTIONS_PER_HOUR 0
  MAX_USER_CONNECTIONS 10;

-- 5. Flush privileges
FLUSH PRIVILEGES;

-- 6. Verification query (DBA verification)
SHOW GRANTS FOR 'e7_ro_sync'@'<vpc_cidr>';
```

---

## 6. Verification & Handover Procedure

Once provisioned, the DBA team delivers the connection parameters (via enterprise password manager / HashiCorp Vault):
* Database Host: Private VPC FQDN (e.g. `mysql-internal.corp.irinfotech.internal`)
* Port: `3306`
* Database Name: `iredu`
* Username: `e7_ro_sync`
* TLS Requirement: `ssl_mode=VERIFY_IDENTITY` (Institutional CA certificate provided)

The E-7 engineering operator verifies the connection with least-privilege grant compliance and read-only validation:
```bash
# 1. Audit grants via MySQL client
mysql -h <host> -u e7_ro_sync -p -e "SHOW GRANTS FOR 'e7_ro_sync'@'%';"

# 2. Automated grant evaluation and read-only verification through MySqlIreduAdapter
python -c "
import asyncio
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from app.integrations.erp.config import MySqlErpSettings

settings = MySqlErpSettings(
    enabled=True,
    host='<host>',
    user='e7_ro_sync',
    password='<pass>',
    database='iredu',
    single_tenant_id='<tenant_id>'
)
adapter = MySqlIreduAdapter(settings=settings)

async def check():
    # Programmatic least-privilege audit
    grants = await adapter.audit_database_user_grants()
    compliance = adapter.evaluate_grants_least_privilege(grants)
    assert compliance['is_compliant'], f'Least-privilege violation: {compliance[\"violations\"]}'
    print('SUCCESS: Least-privilege grant compliance verified.')

    # Functional read-only context verification
    org = await adapter.get_organization_context('<tenant_id>')
    print('SUCCESS: Organization context read:', org.institution_name)
    sessions = await adapter.get_academic_sessions('<tenant_id>')
    print('SUCCESS: Sessions read, count:', len(sessions))

asyncio.run(check())
"
```

---

## 7. Status Transition Criteria

* Delivery of service account credentials with verified `SELECT` privileges transitions:  
  `MYSQL_LIVE_READ_VERIFICATION` from `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` to `PASS`.
