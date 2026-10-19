# Operations Engine — Live Demo Operational Checklist

**Repository**: `F:\operations-engine`
**Standard Port**: `8088` (API Gateway) / `5173` (Frontend)
**Verification Scope**: Pre-Flight, Live Flow, Security & Cleanup

---

## Pre-Demo Environment & Health

- [ ] **Docker Containers Active**: `e7-api`, `e7-worker`, `e7-scheduler-worker`, `e7-pilot-gateway`, `e7-postgres`, `e7-rabbitmq`, `e7-flowable`
- [ ] **Gateway Health Check**: `http://127.0.0.1:8088/api/v1/health` returns `{"status":"ok"}` (HTTP 200)
- [ ] **Gateway Readiness Check**: `http://127.0.0.1:8088/api/v1/ready` returns `{"status":"ready"}` (HTTP 200)
- [ ] **OpenAPI Spec Available**: `http://127.0.0.1:8088/openapi.json` reachable
- [ ] **Frontend Dev Server Running**: `http://127.0.0.1:5173` loads without runtime compile errors
- [ ] **Browser Console Clean**: No uncaught React exceptions, unhandled rejections, or missing asset errors
- [ ] **Zero Active Secrets Exposed**: No service tokens, DB passwords, or keys in frontend bundles or logs

---

## Authentication & External Status Truthfulness

- [ ] **OIDC Status Disclosure Verified**: `/login` screen displays **"Authentication Configuration Required"**
- [ ] **No Mock Auth Bypasses**: No hard-coded tokens or client-side secret inputs present
- [ ] **External Dependency 1**: `HUMAN_BROWSER_AUTH = BLOCKED_ON_COMPANY_OIDC_CLIENT_CONFIGURATION`
- [ ] **External Dependency 2**: `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_COMPANY_READ_ONLY_ACCESS`
- [ ] **External Dependency 3**: `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_COMPANY_E8_ENDPOINT`
- [ ] **Tenant Context Confirmed**: Operating context set to `tenant-engineering` / `tenant-pilot-001`

---

## Operational Lifecycle Demonstration

- [ ] **1. Executive Dashboard**: Renders 5 core KPI cards, active blockers table, and live event stream
- [ ] **2. Goal Creation**: Form creates operational objective, navigates directly to Goal Detail
- [ ] **3. Plan Generation**: "Generate Plan" synthesizes WBS tasks, durations, and role assignments
- [ ] **4. Plan Validation**: "Validate Plan" executes dependency cycle verification with zero errors
- [ ] **5. Approval Submission**: "Submit for Approval" transitions plan state to `APPROVAL_PENDING`
- [ ] **6. Baseline Approval**: "Approve Plan" establishes approved baseline with SHA-256 integrity hash (`is_baseline=True`)
- [ ] **7. Resource & Availability Audit**: Inspects workforce (`PERSON`), rooms (`ROOM`), equipment, and calendars
- [ ] **8. Solver Job Dispatch**: CP-SAT engine dispatches job and polls to `FEASIBLE` status in < 5 seconds
- [ ] **9. Timetable Inspection**: "Open Timetable" directly opens conflict-free timetable grid and table views
- [ ] **10. Schedule Publication**: "Publish Timetable" transitions candidate to authoritative `PUBLISHED` (v1)
- [ ] **11. Schedule Repair**: "Repair Schedule" generates candidate successor (v2) preserving baseline traceability

---

## Post-Demo Reset & Cleanup

- [ ] **Frontend Server Stopped**: Dev server process cleanly stopped
- [ ] **Docker Containers Gracefully Shutdown**: `docker compose -f docker-compose.yml -f docker-compose.pilot.yml down`
- [ ] **No Unwanted Git Artifacts**: No accidental commits, tags, or unvetted tracked files
