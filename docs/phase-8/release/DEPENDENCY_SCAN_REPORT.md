# Phase 8H Dependency Vulnerability & Lockfile Audit Report

- **Candidate Content ID**: `6dca9bcec41e11ea6cba80515ca05bd68850cd5900be9fe04c1d78c1d421f391`
- **Candidate Name**: `operations-engine`
- **Version**: `0.1.0`
- **Git Branch**: `master`
- **Git HEAD**: `69349b093a71ab5b8bbb83ec9d97e4f7de6b90b5`
- **Working Tree State**: `DIRTY / PRESERVED`
- **Audit Timestamp**: `2026-10-11T21:15:00Z`
- **Scanner Tool**: `pip-audit 2.10.1` (PyPI Advisory Database via OSV / PyPA Advisory DB)
- **Node Scanner Tool**: `npm audit` (v11.16.0)

---

## 1. Executive Summary

| Scope | Direct Packages | Transitive Packages | Total Evaluated | Critical | High | Medium | Low | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Python Production Runtime** | 21 | 24 | 45 | 0 | 0 | 3 | 2 | **ACCEPTED_WITH_JUSTIFICATION** |
| **Python Dev / Tooling** | 6 | 12 | 18 | 0 | 0 | 1 | 0 | **DEV_TEST_ONLY** |
| **Node Production Runtime** | 6 | 103 | 109 | 0 | 0 | 0 | 0 | **PASS (0 Vulnerabilities)** |
| **Node Dev Tooling** | 13 | 315 | 328 | 2 | 9 | 1 | 0 | **DEV_TEST_ONLY** |

---

## 2. Lockfile & Dependency Reproducibility Audit

### 2.1 Python Dependency Specification
- **Manifest**: `pyproject.toml` (`[project.dependencies]`)
- **Resolution State**: **PARTIALLY_CONSTRAINED / FLOATING UPPER BOUNDS**
- **Analysis**:
  - Direct dependencies specify lower bounds (e.g., `fastapi>=0.115.0`, `uvicorn[standard]>=0.30.0`, `pydantic>=2.8.0`, `sqlalchemy[asyncio]>=2.0.0`).
  - `aio-pika>=9.4.0,<10.0.0` is range-constrained.
  - The repository does not currently check in a root `uv.lock` or `requirements.lock.txt`.
  - In Docker builds (`docker/Dockerfile`), `pip install -e .` dynamically resolves packages against PyPI.
- **Finding**: While this allows automatic adoption of patched bug-fix releases, production builds should generate an immutable pinned lockfile prior to Phase 8I production deployment to guarantee byte-for-byte reproducibility.

### 2.2 Node / UI Dependency Specification
- **Manifest**: `ui/operations-ui/package.json`
- **Lockfile**: `ui/operations-ui/package-lock.json` (208 kB)
- **Resolution State**: **FULLY LOCKED**
- **Analysis**: The Node frontend is deterministically pinned with exact integrity hashes via `package-lock.json`.

---

## 3. Python Dependency Vulnerability Findings

### Finding RC-SEC-004: Starlette Multipart Form Large File Rollover Thread Blocking
- **Package**: `starlette` (Installed: `0.41.3`, transitive via `fastapi 0.115.6`)
- **Advisory ID**: `GHSA-2c2j-9gv5-cj73` / `CVE-2025-54121` / `PYSEC-2026-1941`
- **Severity**: `LOW` (CVSS: 3.7)
- **Direct/Transitive**: Transitive
- **Runtime Relevance**: Low
- **Reachability / Context**: **NOT_REACHABLE**. E-7 Operations Engine does not implement multipart form file upload endpoints. All API payloads are JSON REST or webhook payloads.
- **Fix Availability**: Fixed in `starlette>=0.47.2`.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` (requires Phase 8I signoff).

### Finding RC-SEC-005: Starlette FileResponse Range Header Quadratic Parsing DoS
- **Package**: `starlette` (Installed: `0.41.3`, transitive via `fastapi 0.115.6`)
- **Advisory ID**: `GHSA-7f5h-v6xp-fcq8` / `CVE-2025-62727` / `PYSEC-2026-1942`
- **Severity**: `MEDIUM` (CVSS: 5.3)
- **Direct/Transitive**: Transitive
- **Runtime Relevance**: None
- **Reachability / Context**: **NOT_REACHABLE**. E-7 does not serve files via `FileResponse` or `StaticFiles`.
- **Fix Availability**: Fixed in `starlette>=0.47.2`.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` (requires Phase 8I signoff).

### Finding RC-SEC-006: Starlette Windows StaticFiles UNC Path SSRF / NTLM Leak
- **Package**: `starlette` (Installed: `0.41.3`, transitive via `fastapi 0.115.6`)
- **Advisory ID**: `GHSA-wqp7-x3pw-xc5r` / `CVE-2026-48818` / `PYSEC-2026-2281`
- **Severity**: `MEDIUM` (CVSS: 6.5)
- **Direct/Transitive**: Transitive
- **Runtime Relevance**: None
- **Reachability / Context**: **NOT_REACHABLE**. E-7 does not mount `StaticFiles`, and the production image executes on Linux container (`python:3.11-slim`), where Windows UNC path behavior is impossible.
- **Fix Availability**: Fixed in `starlette>=1.1.0`.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` (requires Phase 8I signoff).

### Finding RC-SEC-007: Starlette HTTPEndpoint Method Dispatch Flaw
- **Package**: `starlette` (Installed: `0.41.3`, transitive via `fastapi 0.115.6`)
- **Advisory ID**: `GHSA-x746-7m8f-x49c` / `CVE-2026-48817` / `PYSEC-2026-2280`
- **Severity**: `MEDIUM` (CVSS: 5.3)
- **Direct/Transitive**: Transitive
- **Runtime Relevance**: None
- **Reachability / Context**: **NOT_REACHABLE**. E-7 routes are implemented via FastAPI `APIRouter` with explicit HTTP method decorators (`@router.get`, `@router.post`). No raw `HTTPEndpoint` subclasses with open methods are declared.
- **Fix Availability**: Fixed in `starlette>=1.1.0`.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` (requires Phase 8I signoff).

### Finding RC-SEC-008: Pip Local Tooling Advisories
- **Package**: `pip` (Installed: `25.0.1` in developer `.venv`)
- **Advisory ID**: `GHSA-4xh5-x5gv-qwph` / `CVE-2025-8869`, `CVE-2026-1703`, `CVE-2026-3219`, `CVE-2026-6357`, `CVE-2026-8643`, `CVE-2026-13346`
- **Severity**: `MEDIUM`
- **Direct/Transitive**: Local environment tooling
- **Runtime Relevance**: None
- **Reachability / Context**: **DEV_TEST_ONLY**. `pip` is an ephemeral installation tool inside `.venv`, not exposed or invoked during production server runtime.
- **Fix Availability**: Fixed in `pip>=26.2.1`.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` (dev/test only).

### Finding RC-SEC-013: Production Python Dependencies Lack Deterministic Lockfile
- **Location**: `pyproject.toml:23-45`, `docker/Dockerfile:11`
- **Category**: Supply Chain / Build Reproducibility
- **Severity**: `MEDIUM`
- **Direct/Transitive**: Direct & Transitive
- **Analysis**:
  - `pyproject.toml` defines production dependencies using floating minimum-version bounds (`>=`).
  - No immutable lockfile (`requirements.lock.txt`, `poetry.lock`, `uv.lock`) exists in the repository.
  - `docker/Dockerfile` executes `pip install -e .` directly against PyPI at image build time.
  - While candidate image `e7-operations-engine:rc-6dca9bcec41e` captured the packages resolved at build time, future builds without a lockfile could resolve newer minor/patch releases with unexpected regressions.
- **Triage Decision**: `ACCEPTED_WITH_JUSTIFICATION` for Phase 8H candidate scanning; flagged as `OPEN_REQUIRES_PHASE_8I_SIGNOFF` to generate an immutable lockfile with SHA-256 hashes prior to production deployment.

---

## 4. Node / UI Dependency Findings

- **Command**: `npm audit --omit=dev --json`
- **Result**:
  - Production Dependencies: 109
  - Vulnerabilities in Production: **0** (clean)
- **Dev Tooling Findings** (`npm audit --json`):
  - `vitest` (3.0.5), `tinypool` (2.1.0), `@typescript-eslint/parser`: 12 vulnerabilities reported in development test runner and linter dependencies.
  - **Triage**: Classified as `RC-SEC-009` (`DEV_TEST_ONLY`). None of these dev packages are bundled into production runtime artifacts.

---

## 5. Dependency Scan Conclusion

- **CRITICAL Unresolved Application Findings**: **0**
- **HIGH Unresolved Application Findings**: **0**
- **MEDIUM Unresolved Findings**: 3 (Starlette transitive advisories, confirmed `NOT_REACHABLE` in E-7 API)
- **Gate Recommendation**: **PASS_FOR_8H_PENDING_PHASE_8I_SIGNOFF**
