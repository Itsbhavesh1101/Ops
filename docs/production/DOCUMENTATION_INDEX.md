# E-7 Operations Engine — Canonical Documentation Index

**Document Version:** 1.0.0  
**Phase:** Phase 8F Production Documentation, API Contracts & Operational Runbooks  
**Repository:** `F:\operations-engine`  
**Accepted Baseline State:** Phase 8A, 8B, 8C, 8D, 8E = PASS | Phase 8F = PASS  

---

## 1. Quick Navigation for Engineers

Welcome to the IR Infotech E-7 Operations Engine documentation index. Use this guide to locate canonical engineering documents, API contracts, runbooks, and recovery guides:

| Engineering Domain | Canonical Production Document | Key Topics Covered |
|---|---|---|
| **API Reference** | [`docs/production/API_REFERENCE.md`](file:///f:/operations-engine/docs/production/API_REFERENCE.md) | All endpoints by domain, auth, request/response, errors, quotas |
| **OpenAPI Specification** | [`docs/production/openapi.json`](file:///f:/operations-engine/docs/production/openapi.json) | Deterministic OpenAPI 3.1.0 schema generated from running app |
| **Database & ERD** | [`docs/production/ERD.md`](file:///f:/operations-engine/docs/production/ERD.md) | 26 application tables + alembic_version, relationships, security |
| **Mermaid ER Diagram** | [`docs/production/erd.mmd`](file:///f:/operations-engine/docs/production/erd.mmd) | Raw Mermaid ER source diagram for version control and diagrams |
| **Workflows & Flowable** | [`docs/production/WORKFLOW_AUTHORING_GUIDE.md`](file:///f:/operations-engine/docs/production/WORKFLOW_AUTHORING_GUIDE.md) | Flowable 7.x, BPMN 2.0 / DMN 1.3 authoring, SoD, task actions |
| **Scheduler Constraints**| [`docs/production/CONSTRAINT_CATALOG.md`](file:///f:/operations-engine/docs/production/CONSTRAINT_CATALOG.md) | OR-Tools CP-SAT constraints, status catalog, solver budgets |
| **AI Planner & Evaluation**| [`docs/production/MODEL_EVALUATION_GUIDE.md`](file:///f:/operations-engine/docs/production/MODEL_EVALUATION_GUIDE.md) | Task intelligence, 28-field allowlist, offline/live eval, privacy |
| **Production Deployment** | [`docs/production/DEPLOYMENT_RUNBOOK.md`](file:///f:/operations-engine/docs/production/DEPLOYMENT_RUNBOOK.md) | Pre-flight, startup order, migrations (0014), external blockers |
| **Operations & Incidents**| [`docs/production/INCIDENT_RESPONSE_GUIDE.md`](file:///f:/operations-engine/docs/production/INCIDENT_RESPONSE_GUIDE.md) | 20 triage scenarios, key revocation, metrics, low-cardinality rule |

---

## 2. Architecture Decision Records (ADRs)

Canonical decisions governing system design are documented under `docs/adr/`:

- [ADR-000: Source of Truth and Reconciliation](file:///f:/operations-engine/docs/adr/ADR-000-source-of-truth-and-reconciliation.md)
- [ADR-001: Workflow Runtime Choice](file:///f:/operations-engine/docs/adr/ADR-001-workflow-runtime.md)
- [ADR-002: Task Intelligence Orchestration](file:///f:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md)
- [ADR-003: Scheduling Solver Strategy](file:///f:/operations-engine/docs/adr/ADR-003-scheduling-solver-strategy.md)
- [ADR-004: Business Rules Engine](file:///f:/operations-engine/docs/adr/ADR-004-business-rules.md)
- [ADR-005: Event Broker](file:///f:/operations-engine/docs/adr/ADR-005-event-broker.md)
- [ADR-006: System of Record Persistence](file:///f:/operations-engine/docs/adr/ADR-006-system-of-record.md)
- [ADR-007: Cache and Coordination](file:///f:/operations-engine/docs/adr/ADR-007-cache-and-coordination.md)
- [ADR-008: LLM Provider Strategy](file:///f:/operations-engine/docs/adr/ADR-008-llm-provider-strategy.md)
- [ADR-009: Workflow Authoring Toolkit](file:///f:/operations-engine/docs/adr/ADR-009-workflow-authoring-toolkit.md)
- [ADR-010: Education Scheduler Production Choice](file:///f:/operations-engine/docs/adr/ADR-010-education-scheduler-production-choice.md)
- [ADR-011: Integration Governance and BLK-01](file:///f:/operations-engine/docs/adr/ADR-011-integration-governance-and-blk01.md)
- [ADR-012: E8 Notification Boundary](file:///f:/operations-engine/docs/adr/ADR-012-e8-notification-boundary.md)
- [ADR-013: Service API Credential Persistence](file:///f:/operations-engine/docs/adr/ADR-013-service-api-credential-persistence.md)

---

## 3. Disaster Recovery Runbooks (Phase 8D)

Validated disaster recovery drill procedures reside in `docs/phase-8/recovery/`:

- [PostgreSQL Full Backup & Restore](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_BACKUP_RESTORE.md)
- [PostgreSQL Point-in-Time Recovery (PITR)](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_PITR.md)
- [RabbitMQ Broker Partition Recovery](file:///f:/operations-engine/docs/phase-8/recovery/RABBITMQ_RECOVERY.md)
- [Flowable Engine Interruption Recovery](file:///f:/operations-engine/docs/phase-8/recovery/FLOWABLE_RECOVERY.md)
- [Worker Crash & Lease Reclamation](file:///f:/operations-engine/docs/phase-8/recovery/WORKER_RECOVERY.md)
- [External Integration Outage Recovery](file:///f:/operations-engine/docs/phase-8/recovery/INTEGRATION_OUTAGE_RECOVERY.md)
- [Authentication Restart Recovery](file:///f:/operations-engine/docs/phase-8/recovery/AUTH_RESTART_RECOVERY.md)
- [Disaster Recovery Drill Evidence Report](file:///f:/operations-engine/docs/phase-8/recovery/RECOVERY_DRILL_REPORT.md)

---

## 4. Performance & Capacity Reports (Phase 8C & 8E)

- [Load Benchmark Summary (Phase 8C)](file:///f:/operations-engine/docs/phase-8/performance/BENCHMARK_SUMMARY.md)
- [Capacity Tuning Report (Phase 8E)](file:///f:/operations-engine/docs/phase-8/capacity/CAPACITY_TUNING_REPORT.md)
- [Rate Limiting and Quota Boundary (Phase 8E)](file:///f:/operations-engine/docs/phase-8/capacity/RATE_LIMITING_AND_QUOTAS.md)

---

---

## 5. Traceability & Source of Truth

- [Requirements Traceability Matrix](file:///f:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md)
- **Original Source Documents:** `docs/source-of-truth/` (*Strictly Read-Only*)

---

## 6. Stage 2 Production Handover & Operational Automation

Canonical handover packages, release packaging tooling, and alerting automation:

- [Operations Handover Master Checklist](file:///f:/operations-engine/docs/production/OPERATIONS_HANDOVER_CHECKLIST.md)
- [Database Administration Handover](file:///f:/operations-engine/docs/production/handover/DATABASE_ADMINISTRATION_HANDOVER.md)
- [Corporate Identity & IAM Handover](file:///f:/operations-engine/docs/production/handover/IDENTITY_TEAM_HANDOVER.md)
- [E-8 Engagement Platform Handover](file:///f:/operations-engine/docs/production/handover/E8_PLATFORM_TEAM_HANDOVER.md)
- [Cloud Infrastructure & SRE Handover](file:///f:/operations-engine/docs/production/handover/CLOUD_INFRASTRUCTURE_SRE_HANDOVER.md)
- [Prometheus Alert Evaluation Rules](file:///f:/operations-engine/deploy/prometheus/alert.rules.yml)
- [Alertmanager Notification Routing Configuration](file:///f:/operations-engine/deploy/alertmanager/alertmanager.yml)
- [Automated Production Release Packaging Tool](file:///f:/operations-engine/scripts/release/build_production_release.py)

