# Phase 8I: Restricted Pilot Scope Exception & Deployment Boundary

**Repository**: `F:\operations-engine`  
**Execution Timestamp**: 2026-10-19T02:17:00+05:30  
**Phase Identifier**: `PHASE_8I`  
**Phase Title**: `RESTRICTED_PILOT_SCOPE_EXCEPTION_AND_DEPLOYMENT_BOUNDARY`  

---

## 1. Objective & Governance Context

The objective of this document is to establish a formally governed **RESTRICTED PILOT** deployment boundary for the **IR Infotech E-7 Operations Engine**.

This restricted pilot boundary enables engineering verification, controlled pilot onboarding, and end-to-end operational workflows without waiting for two external institutional dependencies:
1. Provisioning of a dedicated production MySQL read-only credentials by the institutional DBA.
2. Deployment of a live external E-8 Engagement service endpoint by the platform team.

### Critical Invariants
* **Not Full Production Readiness**: This restricted pilot scope exception certifies readiness exclusively for a bounded pilot environment. Full production readiness remains governed as `NOT_READY`.
* **Frozen RC Immutability**: The frozen E-7 Release Candidate (`RC_CONTENT_ID: 38d080fadab8f0f98e60f125d0ade060d13a3e36686252670e482c6c4a784897`, commit `03e6305256db3d838b472aaac50893ddb513414e`, tag `phase8-rc-38d080fadab8`, image `e7-operations-engine:rc-38d080fadab8`) is strictly preserved and unmodified.
* **No Git Mutation**: No git commit, amend, tag movement, or push is performed.

---

## 2. Restricted Pilot Scope Exceptions

```
================================================================================
MYSQL_LIVE_READ_VERIFICATION = DEFERRED_FROM_RESTRICTED_PILOT_SCOPE
================================================================================
```
* **Reason**: Institutional DBA has not yet provisioned a dedicated `SELECT`-only account.
* **Pilot Behavior**:
  * Do not use live ERP / MySQL synchronization.
  * Do not use broader company database credentials that possess write or DDL privileges.
  * No database grant mutations shall be attempted.
  * The application may use local, test, or synthetic demo data fixtures where appropriate.
* **Production Canonical Status**:
  * `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`

---

```
================================================================================
E8_NOTIFICATION_INTEGRATION = DEFERRED_FROM_RESTRICTED_PILOT_SCOPE
================================================================================
```
* **Local Contract Status**:
  * `E8_LOCAL_CONTRACT_DISPATCH = PASS`
* **Reason**: Live E-8 Engagement service endpoint has not yet been deployed by the external platform team.
* **Pilot Behavior**:
  * E-7 may create, validate, and persist notification commands.
  * Transactional outbox persistence remains fully enabled in PostgreSQL.
  * CloudEvent serialization and contract dispatch remain fully functional.
  * Do not claim real SMS, email, WhatsApp, or external telecom delivery.
  * Do not fabricate an unverified E-8 URL or endpoint.
* **Production Canonical Status**:
  * `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_E8_ENDPOINT`

---

## 3. Restricted Pilot Terminology & Status Model

To preserve the absolute integrity of institutional governance and prevent misleading operational claims, pilot readiness is tracked independently of production readiness:

| Dimension | Governed Status | Rationale |
|---|---|---|
| **`RESTRICTED_PILOT_ENGINEERING_READINESS`** | **`READY`** *(upon gateway verification)* | Application code, solvers, workflows, database models, and edge rate limiting are verified and operational. |
| **`PHASE_8_PRODUCTION_READINESS`** | **`NOT_READY`** | Full institutional production deployment remains blocked on external read-only MySQL ERP privileges and E-8 live endpoint. |
| **`PILOT_GATEWAY_RATE_LIMITING`** | **`PASS`** *(upon gateway verification)* | Edge Nginx reverse proxy enforces volumetric rate-limiting and HTTP 429 Retry-After responses on the pilot ingress boundary. |
| **`RATE_LIMITING`** | **`BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`** | Production edge infrastructure (WAF / Cloud Gateway) requires deployment in corporate infrastructure. |

---

## 4. Architectural Boundary

In the restricted pilot topology:
1. Inbound client traffic terminates at an external Nginx gateway proxy (`docker/pilot-gateway/nginx.conf`).
2. The gateway enforces Layer-7 rate limiting (`10r/s`, `burst=20`, returning `HTTP 429` with `Retry-After: 1`).
3. Validated requests are proxied internally to the immutable E-7 API container (`e7-operations-engine:rc-38d080fadab8`).
4. Internal infrastructure services (`postgres`, `rabbitmq`, `flowable`) maintain strict network isolation with no public host port exposure.
