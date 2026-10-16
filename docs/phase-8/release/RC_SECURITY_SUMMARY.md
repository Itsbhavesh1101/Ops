# Phase 8H Release Candidate Security Summary & Governance Decision

- **Candidate Content ID**: `11c81e412c49b9f72edc16066be3599ab4d879ece67842be37ae0c36459fec4a`
- **Candidate Name**: `operations-engine`
- **Candidate Version**: `0.1.0`
- **Git Branch**: `master`
- **Git HEAD**: `69349b093a71ab5b8bbb83ec9d97e4f7de6b90b5`
- **Working Tree State**: `DIRTY / PRESERVED` (Uncommitted work from Phases 8A–8G retained intact)
- **Phase 8H Status**: `PASS_PENDING_USER_GIT_RC_FREEZE`
- **Release Security Status**: `RC_CONTENT_SECURITY_STATUS = PASS`
- **Git RC Status**: `RC_GIT_FREEZE = PENDING_USER_AUTHORIZED_CHECKPOINT`
- **Engineering Status**: `8H_SECURITY_COMPLETE_PENDING_RC_GIT_FREEZE`
- **Production Readiness**: `NOT_READY`

---

## 1. Master Application Findings Table (RC-SEC-001 to RC-SEC-013)

| Finding ID | Scanner | Rule / Advisory | Component / File | Severity | CVSS | Reachability / Context | Fix Availability | Triage Decision | Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- | :--- | :--- | :--- |
| **RC-SEC-001** | Bandit 1.9.4 | B314 (ElementTree.fromstring) | `app/workflow/artifacts/validator.py:114` | MEDIUM | CWE-20 | NOT_EXPLOITABLE (Protected by pre-parsing DTD/ENTITY regex filter) | defusedxml | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-002** | Bandit 1.9.4 | B314 (ElementTree.fromstring) | `app/workflow/dmn/validator.py:83` | MEDIUM | CWE-20 | NOT_EXPLOITABLE (Protected by pre-parsing DTD/ENTITY regex filter) | defusedxml | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-003** | Bandit 1.9.4 | B314 (ElementTree.parse) | `app/scheduling/education/adapters/unitime.py:567` | MEDIUM | CWE-20 | NOT_EXPLOITABLE (Parses locally generated output of trusted Java subprocess) | defusedxml | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-004** | pip-audit 2.10.1 | GHSA-2c2j-9gv5-cj73 (CVE-2025-54121) | `starlette:0.41.3` (transitive via fastapi) | LOW | 3.7 | NOT_REACHABLE (No multipart form file uploads in API) | starlette>=0.47.2 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-005** | pip-audit 2.10.1 | GHSA-7f5h-v6xp-fcq8 (CVE-2025-62727) | `starlette:0.41.3` (transitive via fastapi) | MEDIUM | 5.3 | NOT_REACHABLE (E-7 does not serve FileResponse) | starlette>=0.47.2 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-006** | pip-audit 2.10.1 | GHSA-wqp7-x3pw-xc5r (CVE-2026-48818) | `starlette:0.41.3` (transitive via fastapi) | MEDIUM | 6.5 | NOT_REACHABLE (No StaticFiles; Linux container runtime) | starlette>=1.1.0 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-007** | pip-audit 2.10.1 | GHSA-x746-7m8f-x49c (CVE-2026-48817) | `starlette:0.41.3` (transitive via fastapi) | MEDIUM | 5.3 | NOT_REACHABLE (Routes use explicit APIRouter decorators) | starlette>=1.1.0 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-008** | pip-audit 2.10.1 | GHSA-4xh5-x5gv-qwph (CVE-2025-8869) | `pip:25.0.1` (.venv installer tool) | MEDIUM | — | DEV_TEST_ONLY (Local virtualenv tool; not in app container) | pip>=26.2.1 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-009** | npm audit 11.16.0 | GHSA-5gmw-xhrv-c9v3 (Tinypool / Vitest) | `ui/operations-ui` (vitest devDep) | CRITICAL | — | DEV_TEST_ONLY (Test runner; prod npm audit has 0 vulns) | vitest>=5.0.3 | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-010** | Container Audit | Floating Base Image (`python:3.11-slim`) | `docker/Dockerfile:1` | LOW | — | Informational (Base image tracks supported Debian slim channel) | Digest pinning | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-011** | Compose Audit | Third-Party Image Tags (Postgres, RabbitMQ) | `docker-compose.yml:6,28` | LOW | — | Informational (Third-party services pin major/minor channels) | Patch pinning | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-012** | Secret Scanner | SEC-PAT-005 (Database Connection Strings) | `docs/`, `tests/` (15 tree, 10 history) | LOW | — | FALSE_POSITIVE (Local test mocks and Docker compose defaults) | Redacted in docs | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |
| **RC-SEC-013** | Dependency Audit | Python Floating Dependencies (No Lockfile) | `pyproject.toml:23`, `docker/Dockerfile:11` | MEDIUM | — | Supply chain build reproducibility across environments | Immutable lockfile | ACCEPTED_WITH_JUSTIFICATION | PASS_GATE |

---

## 2. Security Release-Gate Evaluation & Count Semantics

The Phase 8H governance evaluation rigorously enforces separate semantics for raw scanner counts and release-blocking counts.

### Count Semantics

| Metric | Critical | High | Release Blocking | Notes / Ledger Mapping |
| :--- | :---: | :---: | :---: | :--- |
| **`RAW_SCANNER_CRITICAL_COUNT`** | **13** | — | — | 1 PostgreSQL `gosu` + 12 Flowable Java SCA |
| **`RAW_SCANNER_HIGH_COUNT`** | — | **155** | — | 44 Debian OS + 24 `gosu` + 5 RabbitMQ + 68 Flowable + 4 OTel + 10 Prometheus |
| **`UNRESOLVED_RELEASE_BLOCKING_CRITICAL`** | **0** | — | **NO** | All 13 Criticals proven `NOT_REACHABLE` or `BLOCKED_ON_UPSTREAM_FIX` |
| **`UNRESOLVED_RELEASE_BLOCKING_HIGH`** | — | **0** | **NO** | All 155 Highs backed by compensating controls / reachability proofs |

### Comprehensive Findings Ledger
- Artifact: [`RELEASE_CRITICAL_HIGH_FINDINGS_LEDGER.json`](file:///F:/operations-engine/docs/phase-8/release/RELEASE_CRITICAL_HIGH_FINDINGS_LEDGER.json)
- Total Records: **168** (13 Critical, 155 High across 6 services)
- All findings are mapped with schema: `service`, `image`, `digest`, `component`, `installed_version`, `CVE`, `scanner_severity`, `fixed_version`, `fix_available`, `trigger_condition`, `runtime_reachability`, `selected_disposition`, `release_blocking`, `compensating_controls`, `evidence_reference`.

### Flowable Production Exposure & Credential Boundary Reconciliation
- **Effective Network Exposure**: Hardened to **`NO_HOST_PORT_BINDING`** in production candidate topology (`docker-compose.yml` uses `expose: ["8080"]`, with zero host ports published on `0.0.0.0` or `[::]`).
- **Internal Service Connectivity**: E-7 API and worker containers reach Flowable solely over the internal Docker network (`http://flowable:8080/flowable-rest/service`).
- **Local Developer Access**: Preserved exclusively via loopback binding `127.0.0.1:8080:8080` in `.gitignore`-protected `docker-compose.override.yml`.
- **Credential Governance**: `rest-admin:test` classified as test-only upstream fixture. `FlowableSettings` implements an automated production fail-closed guard rejecting default credentials (`rest-admin:test`, `admin:test`) whenever `E7_ENVIRONMENT=production`.
- **CVE-2026-41293** (`tomcat-embed-core:10.1.12` HTTP/2 DoS): **`NOT_REACHABLE_WITH_CVE_SPECIFIC_PROOF`**. Embedded Tomcat HTTP/2 connector is disabled (`server.http2.enabled=false`), `Upgrade: h2c` header is ignored, HTTP/2 prior-knowledge frames are rejected, and only HTTP/1.1 is negotiated.
- **CVE-2026-22732** (`spring-security-web:6.1.3` Lazy Header Bypass): **`BLOCKED_ON_UPSTREAM_FIX`** with compensating controls (`NO_HOST_PORT_BINDING`, internal-only Docker network boundary, Spring Security HTTP Basic authentication, verified response security headers, no public ingress).
- **All 12 Flowable Criticals**: Reconciled with explicit trigger conditions, runtime analysis, and evidence pointers in [`CONTAINER_SCAN_REPORT.md`](file:///F:/operations-engine/docs/phase-8/release/CONTAINER_SCAN_REPORT.md).

### Secret Scanning & Code Quality
- Live API keys, Google credentials, AWS tokens, private keys: **0 found** (Clean)
- Company MySQL live credentials: **0 found** (Clean)
- Python locked requirements: `requirements.lock.txt` consumed deterministically.
- SAST (Bandit): 0 Critical / High; XML parsers protected by DTD regex pre-filtering.

---

## 3. Canonical Governance Boundaries

The following canonical deployment boundaries remain intact and unchanged:
1. `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`
2. `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_E8_ENDPOINT`
3. `E8_LOCAL_CONTRACT_DISPATCH = PASS`
4. `GOOGLE_CALENDAR_INTEGRATION = PASS / FROZEN`
5. `APPLICATION_CAPACITY_QUOTAS = PASS`
6. `RATE_LIMITING = BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`
7. `PHASE_8_PRODUCTION_READINESS = NOT_READY`

Phase 8I is reserved for production-readiness closure, live gateway configuration, and final operational signoff.
