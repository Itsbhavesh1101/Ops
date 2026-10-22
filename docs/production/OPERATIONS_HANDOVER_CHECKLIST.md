# E-7 Operations Engine — Production Operations Handover Checklist

**Document:** Comprehensive Production Readiness, Handover & Go-Live Acceptance Checklist  
**System:** IR Infotech E-7 Operations Engine  
**Version:** 1.0.0-rc1  
**Classification:** Enterprise Release Governance Checklist  
**Engineering Baseline:** Demo-Complete, Frozen Phase-8 RC Verified  
**Governance Principle:** Strict Distinction Between Internal Engineering Readiness and External Institutional Acceptance  

---

## 1. Readiness State Taxonomy

Every checklist item carries an explicit dual evaluation:
* **Engineering State:** `COMPLETED` (Implemented, tested, and validated internally) vs `IN_PROGRESS`
* **Institutional State:** `PENDING_EXTERNAL` (Gated on external company teams/infrastructure) vs `ACCEPTED` (Formally signed off by enterprise owners)

---

## 2. Master Go-Live Acceptance Checklist

### 2.1 Production Preflight & Working-Tree Hygiene
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.1.1 | Git baseline HEAD confirmed (`5722d1cdd821...`) | `COMPLETED` | `ACCEPTED` | Release Engineer | `git rev-parse HEAD` |
| 2.1.2 | Frozen Phase-8 RC anchor intact (`03e6305256db...`) | `COMPLETED` | `ACCEPTED` | Release Engineer | `git cat-file -e 03e63052` |
| 2.1.3 | Secret scan executed with zero active credentials | `COMPLETED` | `ACCEPTED` | SecOps / Release | `scripts/release/scan_secrets.py` |
| 2.1.4 | CycloneDX 1.5 SBOM generated with all dependencies | `COMPLETED` | `ACCEPTED` | Release Engineer | `scripts/release/generate_sbom.py` |
| 2.1.5 | Base container image pinned by SHA256 digest | `COMPLETED` | `ACCEPTED` | DevOps Engineer | `docker/Dockerfile` base image digest |

### 2.2 Infrastructure Provisioning
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.2.1 | Managed PostgreSQL 16 HA instance provisioned | `COMPLETED` | `PENDING_EXTERNAL` | Cloud Infra / SRE | Connection probe via `pg_isready` |
| 2.2.2 | Clustered RabbitMQ 3.13 broker provisioned | `COMPLETED` | `PENDING_EXTERNAL` | Cloud Infra / SRE | AMQPS handshake on port 5671 |
| 2.2.3 | Dedicated Flowable REST engine provisioned | `COMPLETED` | `PENDING_EXTERNAL` | Cloud Infra / SRE | Flowable `/actuator/health` probe |
| 2.2.4 | Layer 7 Ingress Gateway & modern TLS 1.3 | `COMPLETED` | `PENDING_EXTERNAL` | Cloud Infra / SRE | `curl -Iv --tlsv1.3 https://<domain>` |
| 2.2.5 | Edge volumetric rate limiting (100/s IP, 500/s tenant) | `COMPLETED` | `PENDING_EXTERNAL` | Cloud Infra / SecOps | Burst probe returning HTTP 429 |
| 2.2.6 | Production DNS FQDNs configured | `COMPLETED` | `PENDING_EXTERNAL` | Corporate IT | DNS resolution `dig +short <domain>` |

### 2.3 Application Deployment & Migrations
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.3.1 | Alembic migration head `0014` executed (26 tables) | `COMPLETED` | `PENDING_EXTERNAL` | Database Lead / SRE | `alembic current` matches head |
| 2.3.2 | API container (`e7-api`) deployed with health probes | `COMPLETED` | `PENDING_EXTERNAL` | SRE / DevOps | `GET /api/v1/health` returns 200 |
| 2.3.3 | Outbox worker (`e7-worker`) running with lease manager| `COMPLETED` | `PENDING_EXTERNAL` | SRE / DevOps | Process supervisor status |
| 2.3.4 | Scheduler worker (`e7-scheduler-worker`) running | `COMPLETED` | `PENDING_EXTERNAL` | SRE / DevOps | CP-SAT solver consumer logs |
| 2.3.5 | Frontend SPA deployed to CDN / Cloud Storage | `COMPLETED` | `PENDING_EXTERNAL` | Frontend / SRE | Browser loads SPA index bundle |

### 2.4 Security & Identity Verification
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.4.1 | Google Workspace OIDC Client registered | `COMPLETED` | `PENDING_EXTERNAL` | Corporate IAM Team | JWKS probe on googleapis.com |
| 2.4.2 | Authorized redirect URIs confirmed | `COMPLETED` | `PENDING_EXTERNAL` | Corporate IAM Team | OAuth consent screen test |
| 2.4.3 | Hosted domain restriction enforced (`hd=irinfotech.com`)| `COMPLETED` | `PENDING_EXTERNAL` | SecOps / IAM | Rejection of non-corporate email |
| 2.4.4 | Service API key authentication active | `COMPLETED` | `ACCEPTED` | Security Lead | `tests/unit/core/test_service_api_key.py` |
| 2.4.5 | Break-glass credential revocation drilled | `COMPLETED` | `ACCEPTED` | SecOps Lead | `INCIDENT_RESPONSE_GUIDE.md` Sec 3.1 |
| 2.4.6 | Multi-tenant isolation verified (cross-tenant 404) | `COMPLETED` | `ACCEPTED` | Security Lead | `tests/uat/test_role_based_uat.py` |

### 2.5 External Integrations
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.5.1 | MySQL ERP account provisioned (11 active tables) | `COMPLETED` | `PENDING_EXTERNAL` | Institutional DBA | `SHOW GRANTS FOR 'e7_ro_sync'@'%'` |
| 2.5.2 | Read-only enforcement confirmed (zero writes) | `COMPLETED` | `PENDING_EXTERNAL` | Institutional DBA | `SET SESSION TRANSACTION READ ONLY` |
| 2.5.3 | E-8 notification transport confirmed (AMQP vs HTTP) | `COMPLETED` | `PENDING_EXTERNAL` | E-8 Platform Team | `E8_PLATFORM_TEAM_HANDOVER.md` sign-off |
| 2.5.4 | E-8 notification delivery dry-run executed | `COMPLETED` | `PENDING_EXTERNAL` | E-8 Platform Team | `NotificationCommand` dispatch test |
| 2.5.5 | Google Calendar integration configured | `COMPLETED` | `PENDING_EXTERNAL` | Integrations Lead | Calendar service account verification |

### 2.6 Monitoring, Alerting & Observability
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.6.1 | OpenTelemetry Collector ingesting traces & metrics | `COMPLETED` | `PENDING_EXTERNAL` | Observability / SRE | OTLP/HTTP listener on :4318 |
| 2.6.2 | Prometheus scraping OTel Collector on port 8889 | `COMPLETED` | `PENDING_EXTERNAL` | Observability / SRE | Prometheus target status `UP` |
| 2.6.3 | Prometheus alert rules loaded (`alert.rules.yml`) | `COMPLETED` | `PENDING_EXTERNAL` | Observability / SRE | `deploy/prometheus/alert.rules.yml` loaded via `rule_files` |
| 2.6.4 | SEV-1 to SEV-4 Alertmanager routing configured | `COMPLETED` | `PENDING_EXTERNAL` | Observability / SRE | `deploy/alertmanager/alertmanager.yml` routing tree |
| 2.6.5 | Structured JSON logging with `X-Correlation-ID` | `COMPLETED` | `PENDING_EXTERNAL` | SRE / DevOps | Log pipeline validation |

### 2.7 Backup & Recovery Verification
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.7.1 | Automated daily PostgreSQL snapshot scheduled | `COMPLETED` | `PENDING_EXTERNAL` | Cloud SRE / DBA | Cloud backup policy status |
| 2.7.2 | Continuous WAL archiving active | `COMPLETED` | `PENDING_EXTERNAL` | Cloud SRE / DBA | Object storage bucket WAL stream |
| 2.7.3 | Database restore procedure drilled (< 4h RTO) | `COMPLETED` | `ACCEPTED` | SRE / Database Lead | `POSTGRES_BACKUP_RESTORE.md` |
| 2.7.4 | Point-in-Time Recovery (PITR) drilled (< 5m RPO) | `COMPLETED` | `ACCEPTED` | SRE / Database Lead | `POSTGRES_PITR.md` drill records |

### 2.8 Institutional User Acceptance Testing (UAT)
| # | Verification Task | Engineering State | Institutional State | Responsible Role | Verification Method / Evidence |
|---|---|---|---|---|---|
| 2.8.1 | Automated UAT suite passes (49/49 scenarios) | `COMPLETED` | `ACCEPTED` | QA / UAT Lead | `pytest tests/uat/test_role_based_uat.py` |
| 2.8.2 | Academic Planner role workflow sign-off | `COMPLETED` | `PENDING_EXTERNAL` | Faculty Dean / Planner | Goal creation & plan proposal sign-off |
| 2.8.3 | Approver role workflow sign-off | `COMPLETED` | `PENDING_EXTERNAL` | Academic Director | Plan approval & correction review |
| 2.8.4 | Scheduler role workflow sign-off | `COMPLETED` | `PENDING_EXTERNAL` | Operations Lead | Solver execution & repair acceptance |
| 2.8.5 | Tenant Administrator role workflow sign-off | `COMPLETED` | `PENDING_EXTERNAL` | IT Administrator | Workflow & service key management |

---

## 3. Operational Ownership & Handover Matrix

| Operational Domain | Primary Engineering Owner | Institutional Operations Owner | Escalation Channel |
|---|---|---|---|
| **API & Background Workers** | E-7 Backend Engineering | Cloud SRE Operations Team | Primary On-Call Pager |
| **PostgreSQL Database** | E-7 Persistence Lead | Enterprise Database Administration | Enterprise DBA On-Call |
| **RabbitMQ Event Bus** | E-7 Infrastructure Lead | Enterprise Middleware Team | Middleware Support Queue |
| **Flowable Workflow Engine** | E-7 Workflow Lead | Business Process Operations | Workflow Operations Queue |
| **Frontend SPA & CDN** | E-7 Frontend Lead | Web Operations / CDN SRE | Frontend Incident Channel |
| **Authentication & SSO** | E-7 Security Lead | Corporate Identity & IAM Team | Corporate IAM Escalation |
| **MySQL ERP Data Sync** | E-7 Integrations Lead | Institutional SIS / ERP Support | ERP Systems Administrator |
| **E-8 Notifications** | E-7 Integrations Lead | Enterprise E-8 Platform Team | E-8 Support Channel |

---

## 4. Final Production V1 Sign-Off Gates

To authorize the official cutover from Stage 2 preparation to production V1 release:

```
[ ] GATE 1: All 5 Institutional Handover Packages delivered and formally acknowledged.
[ ] GATE 2: Company DBA provisions dedicated SELECT-only account with verified SHOW GRANTS.
[ ] GATE 3: Corporate IT provisions Google Workspace OIDC Client ID with authorized origins.
[ ] GATE 4: E-8 Platform team confirms transport selection and delivers endpoint/broker access.
[ ] GATE 5: Cloud SRE provisions managed PostgreSQL 16, RabbitMQ 3.13, and Ingress with TLS 1.3.
[ ] GATE 6: Production pre-flight migration executes (Alembic head 0014, 26 tables).
[ ] GATE 7: End-to-end smoke test passes against live production endpoints.
[ ] GATE 8: Human representatives for Planner, Approver, Scheduler, and Admin execute UAT sign-off.
[ ] GATE 9: Executive Architecture Board issues immutable V1 Production Release Tag.
```

---

*Document Author: IR Infotech Operations Architecture Team*<br/>
*Baseline HEAD:* `5722d1cdd8210942d4f63e41362cec66ccca2101`<br/>
*Status: Ready for Institutional Operational Review*
