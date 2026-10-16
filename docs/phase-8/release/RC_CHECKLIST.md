# Phase 8H Release Candidate Checklist

- **Candidate Content ID**: `11c81e412c49b9f72edc16066be3599ab4d879ece67842be37ae0c36459fec4a`
- **Candidate Name**: `operations-engine`
- **Version**: `0.1.0`
- **Evaluation Date**: `2026-10-12T17:15:00Z`

---

## 1. Candidate Boundary & Integrity Verification

- [x] **Working Tree Safety**: `DIRTY / PRESERVED` (Uncommitted work from Phases 8A–8G retained intact; zero destructive git commands executed).
- [x] **Deterministic Manifest**: `docs/phase-8/release/RC_CONTENT_MANIFEST.json` generated.
- [x] **Integrity Hash**: `docs/phase-8/release/RC_CONTENT_MANIFEST.sha256` computed from actual file bytes.
- [x] **RC_CONTENT_ID**: `11c81e412c49b9f72edc16066be3599ab4d879ece67842be37ae0c36459fec4a` unified across all security scan reports.
- [x] **Candidate File Boundary**: 730 candidate files (547 tracked, 183 untracked) strictly inventoried.
- [x] **Non-Release Artifacts ExCLUDED**: `.git`, `.venv`, `scratch/`, `node_modules/`, `dist/`, caches.

---

## 2. Security Scanning & Hardening Verification

- [x] **SBOM Generated**: CycloneDX 1.5 JSON (`docs/phase-8/release/SBOM.cyclonedx.json`) covering 68 components.
- [x] **Python Dependency Scan**: `pip-audit 2.10.1` executed; 0 Critical/High findings; Starlette transitive findings triaged as `NOT_REACHABLE`.
- [x] **Node / UI Dependency Scan**: `npm audit --omit=dev` executed; **0 vulnerabilities** in production packages.
- [x] **Static Application Security Testing (SAST)**: `bandit 1.9.4` scanned 37,293 LOC; 0 Critical, 0 High; 3 Medium XML findings verified as protected by pre-parsing DTD filters.
- [x] **Repository Secret Scan**: Full scan over candidate files and git commits; 0 leaked secrets, 0 private keys, 0 API tokens.
- [x] **Company MySQL Safety**: Special pattern scan confirmed zero instances of company MySQL credentials.
- [x] **Container Misconfiguration Audit**: `docker/Dockerfile` verified for non-root execution (`USER e7user`), no privileged mode, no docker socket mounts; `docker-compose.yml` config verified.
- [x] **License / SBOM Reconciliation**: All dependencies comply with IR Infotech commercial distribution policy (MIT, Apache-2.0, BSD-3-Clause).
- [x] **Flowable Production Exposure**: Hardened to `NO_HOST_PORT_BINDING` (`expose: ["8080"]`, 0 host port mappings). Local dev access maintained via loopback `127.0.0.1:8080:8080` in `docker-compose.override.yml`.
- [x] **Flowable Credential Fail-Closed**: `rest-admin:test` classified as test fixture; `FlowableSettings` fails closed in production environment if default credentials configured.
- [x] **Flowable CVE-2026-41293 (Tomcat HTTP/2 DoS)**: Classified as `NOT_REACHABLE_WITH_CVE_SPECIFIC_PROOF` (no HTTP/2 / h2c / ALPN).
- [x] **Flowable CVE-2026-22732 (Spring Security Lazy Headers)**: Classified as `BLOCKED_ON_UPSTREAM_FIX` with compensating controls (`NO_HOST_PORT_BINDING`, internal-only Docker network, HTTP Basic auth).
- [x] **Flowable Criticals Reconciliation**: Exact 12-row table documented with individual evidence pointers.
- [x] **Release Findings Ledger**: `docs/phase-8/release/RELEASE_CRITICAL_HIGH_FINDINGS_LEDGER.json` created with 168 records (13 Critical, 155 High).
- [x] **Count Semantics Reconciliation**: RAW_SCANNER_CRITICAL_COUNT = 13, RAW_SCANNER_HIGH_COUNT = 155; UNRESOLVED_RELEASE_BLOCKING_CRITICAL = 0, UNRESOLVED_RELEASE_BLOCKING_HIGH = 0.

---

## 3. Database & Code Quality Gates

- [x] **Pip Dependencies**: `python -m pip check` -> `No broken requirements found.`
- [x] **Code Linter**: `python -m ruff check .` -> `All checks passed!`
- [x] **Type Checker**: `python -m mypy app` -> `Success: no issues found in 261 source files.`
- [x] **Alembic Migration Current**: `0014 (head)`.
- [x] **Alembic Migration Heads**: `0014 (head)`.
- [x] **Database Schema Tables**: 26 application tables, 27 total public tables verified in PostgreSQL.
- [x] **Engineering UAT Signoff**: `PASS` (Preserved from Phase 8G across 49 acceptance scenarios).

---

## 4. External Boundaries & Blockers (Preserved)

- [x] **MySQL Live Read Boundary**: `BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`
- [x] **E8 Notification Boundary**: `BLOCKED_ON_E8_ENDPOINT`
- [x] **E8 Local Contract Dispatch**: `PASS`
- [x] **Google Calendar Integration**: `PASS / FROZEN`
- [x] **Application Capacity Quotas**: `PASS`
- [x] **Production Rate Limiting**: `BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`

---

## 5. Release Candidate Governance Status

- **RC_CONTENT_SECURITY_STATUS**: `PASS`
- **RC_GIT_FREEZE**: `PENDING_USER_AUTHORIZED_CHECKPOINT`
- **PHASE_8H_RELEASE_SECURITY_SCANNING**: `PASS_PENDING_USER_GIT_RC_FREEZE`
- **PHASE_8_ENGINEERING_STATUS**: `8H_SECURITY_COMPLETE_PENDING_RC_GIT_FREEZE`
- **PHASE_8_PRODUCTION_READINESS**: `NOT_READY`

---

## 6. Execution Statements

- Phase 8I was not started.
- Phase 9 was not started.
