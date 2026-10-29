# E-7 Project Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every repository-controlled gap and provide the executable evidence and handoffs required to move E-7 from internal acceptance to truthful production authorization.

**Architecture:** Use an evidence-first staged closure. Repository defects and verification tooling are completed before external probes and governance gates; production authorization remains fail-closed until real infrastructure and stakeholder evidence exists.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Settings, pytest, PostgreSQL 16, RabbitMQ 3.13, Flowable 7, Playwright, GitHub Actions, Trivy, CycloneDX.

**Spec:** `docs/superpowers/specs/2026-10-30-project-completion-design.md`

## Global Constraints

- Do not modify any PDF under `docs/source-of-truth/`.
- Preserve strict tenant isolation, append-only audit, transactional outbox/inbox, and idempotency.
- Preserve non-authoritative AI and deterministic workflow/scheduling boundaries.
- Never print or persist production secrets in verification evidence.
- Tests precede production code changes and must be observed failing for the intended reason.
- External blockers and human approvals remain open until real evidence is supplied.
- Do not merge, push, deploy, or authorize release without explicit authorization.

## Review Focus

- Windows `USERNAME` must never satisfy Flowable service credentials; covered in Task 1.
- Verification must distinguish unavailable infrastructure from product failure without silently skipping mandatory gates; covered in Tasks 2 and 3.
- External probes must be read-only/non-destructive and redact credentials and tokens; covered in Task 4.
- Security scanning must fail closed for production authorization when tooling or vulnerability data is unavailable; covered in Task 5.
- Generated status evidence must not overwrite historical/frozen release artifacts or claim external completion; covered in Task 6.

---

### Task 1: Cross-platform Flowable authentication configuration

**Files:**
- Modify: `app/workflow/adapters/flowable/config.py:49-58`
- Modify: `tests/unit/workflow/test_flowable_config.py`

**Interfaces:**
- Consumes: `FlowableSettings` Pydantic settings contract and `FLOWABLE_USERNAME` / `FLOWABLE_USER` environment variables.
- Produces: explicit constructor field `username` plus service-specific environment aliases that cannot collide with Windows `USERNAME`.

- [x] Add `test_windows_username_does_not_supply_flowable_basic_auth` using `patch.dict(os.environ, {"USERNAME": "desktop-user"}, clear=True)` and assert missing Flowable auth raises `ValidationError` containing `Flowable authentication is required`.
- [x] Run the new test and observe failure caused by Windows `USERNAME` being treated as Flowable authentication.
- [x] Change the username field aliases so constructor input remains `username` while environment loading accepts only `FLOWABLE_USERNAME` and `FLOWABLE_USER`.
- [x] Run `pytest tests/unit/workflow/test_flowable_config.py -q` and require 16 passing tests with `USERNAME` present.
- [x] Run Ruff and mypy for the changed files.
- [x] Commit as `fix(workflow): isolate Flowable username configuration on Windows`.

### Task 2: Verification manifest and prerequisite classification

**Files:**
- Create: `scripts/verification/run_verification.py`
- Create: `scripts/verification/verification_manifest.json`
- Create: `tests/unit/verification/test_verification_runner.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: subprocess commands and environment prerequisites declared in the JSON manifest.
- Produces: `VerificationReport` JSON with `PASS`, `FAIL`, or `BLOCKED_PREREQUISITE` per mandatory gate and an overall fail-closed exit code.

- [x] Write tests for successful commands, failed commands, missing executables, unavailable declared services, secret redaction, and mandatory blocked gates.
- [x] Observe the tests fail because the runner does not exist.
- [x] Implement typed manifest loading, prerequisite checks, bounded subprocess execution, redacted output, and atomic JSON evidence writing.
- [x] Define groups for static checks, backend suites, frontend suites, browser acceptance, recovery, UAT, security scans, and release packaging.
- [x] Run the runner unit tests, Ruff, and mypy.
- [x] Commit as `feat(verification): add manifest-driven full verification runner`.

### Task 3: CI and cross-platform full-suite enforcement

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/full-verification.yml`
- Modify: `tests/recovery/conftest.py` or add it if absent
- Modify: `tests/uat/conftest.py` or add it if absent
- Test: `tests/unit/verification/test_ci_contract.py`

**Interfaces:**
- Consumes: Task 2 verification runner and manifest.
- Produces: Linux full-verification job, Windows configuration job, and explicit infrastructure prerequisite handling.

- [x] Add CI contract tests asserting no hard-coded collection count and required Linux/Windows verification gates.
- [x] Observe contract tests fail against the existing workflow.
- [x] Rename the stale collection step and add Windows Flowable configuration coverage.
- [x] Add a dedicated full-verification workflow that provisions PostgreSQL, RabbitMQ, Flowable and required CLI tools before running recovery/UAT groups.
- [x] Ensure infrastructure tests skip only when their explicit opt-in is absent and fail when opted in but unavailable.
- [x] Run CI contract tests and YAML validation.
- [x] Commit as `ci: enforce cross-platform complete verification gates`.

### Task 4: External dependency verification probes

**Files:**
- Create: `scripts/verification/verify_mysql_connection.py`
- Create: `scripts/verification/verify_oidc_provider.py`
- Create: `scripts/verification/verify_e8_broker.py`
- Create: `scripts/verification/verify_gateway_ingress.py`
- Create: `tests/unit/verification/test_external_probes.py`

**Interfaces:**
- Consumes: operator-supplied endpoints and credentials via environment/CLI without persisting them.
- Produces: redacted JSON evidence matching the four canonical blocker identifiers.

- [x] Write tests for read-only MySQL enforcement, OIDC discovery/JWKS/issuer validation, AMQPS topology and idempotency handshake, gateway TLS/header/rate-limit/WAF checks, timeout behavior, and redaction.
- [x] Observe failures because the documented probes do not exist.
- [x] Implement non-destructive probes with bounded timeouts and no secret-bearing output.
- [x] Add `--output` evidence files using atomic writes and stable schemas.
- [x] Run probe tests, Ruff, and mypy.
- [x] Commit as `feat(verification): implement production boundary probes`.

### Task 5: Mandatory security and supply-chain gates

**Files:**
- Modify: `.github/workflows/full-verification.yml`
- Modify: `scripts/release/build_production_release.py`
- Modify: `tests/unit/test_build_production_release.py`
- Create: `tests/unit/verification/test_security_gate_contract.py`

**Interfaces:**
- Consumes: Trivy filesystem/container reports, secret scan, SAST, SBOM and findings ledger.
- Produces: release authorization input that is false when scanning is missing, stale, malformed, or contains unresolved critical/high findings.

- [ ] Add failing tests for missing scanner, stale report, malformed report, and unresolved critical/high findings.
- [ ] Implement fail-closed parsing and provenance checks.
- [ ] Add pinned Trivy installation and scan steps to the full-verification workflow.
- [ ] Generate CycloneDX SBOM and verify component counts from the artifact rather than prose.
- [ ] Run release/security tests and dry-run packaging.
- [ ] Commit as `security: enforce production supply-chain gates`.

### Task 6: Living status evidence and documentation reconciliation

**Files:**
- Create: `scripts/verification/update_current_status.py`
- Create: `docs/CURRENT_PROJECT_STATUS.md`
- Modify: `README.md`
- Modify: `docs/PROJECT_COMPLETE_PROGRESS_REPORT.md`
- Modify: `docs/development-quality/DQ6_Full_Verification_Closure_Report.md`
- Modify: `docs/production/DOCUMENTATION_INDEX.md`
- Create: `tests/unit/verification/test_current_status.py`

**Interfaces:**
- Consumes: Task 2 report, release manifest, SBOM, Git SHA and requirement matrix.
- Produces: one current-status document and synchronized generated metrics while preserving frozen historical evidence.

- [ ] Add tests for stale phase labels, contradictory requirement counts, stale test totals, stale SBOM totals, and false release authorization.
- [ ] Observe failures against current documents.
- [ ] Implement deterministic status generation with an explicit generated section.
- [ ] Update living documents to reference the generated status and distinguish internal acceptance from production authorization.
- [ ] Run documentation-link, OpenAPI, and current-status tests.
- [ ] Commit as `docs: synchronize current project completion evidence`.

### Task 7: Execute complete internal verification and remediate findings

**Files:**
- Modify only files implicated by failing gates.
- Update: `docs/development-quality/DQ6_Full_Verification_Closure_Report.md`

**Interfaces:**
- Consumes: Tasks 1-6 and fully provisioned local/CI infrastructure.
- Produces: a fresh full verification report with zero unexplained failures and exact skipped/blocked accounting.

- [ ] Run every manifest group with PostgreSQL, RabbitMQ, Flowable, browser and recovery prerequisites available.
- [ ] For each product failure, use systematic debugging and add a failing regression test before fixing.
- [ ] Rerun all groups until zero product failures remain.
- [ ] Record exact pass/skip/block counts and artifact hashes.
- [ ] Commit as `test: close full internal verification findings`.

### Task 8: Resolve external production blockers

**Files:**
- Update: `docs/production/FINAL_EXTERNAL_DEPENDENCIES.md`
- Update: `docs/production/handover/*.md`
- Add redacted evidence under an approved external-evidence location only after operator review.

**Interfaces:**
- Consumes: real MySQL grants, corporate OIDC registration, E-8 broker/endpoint, and gateway/TLS/WAF provisioning.
- Produces: four passing Task 4 probe reports signed by the responsible teams.

- [ ] DBA provisions the read-only account and approves the 11-table scope; run the MySQL probe.
- [ ] IAM provisions the corporate OIDC client and hosted-domain policy; run the OIDC probe.
- [ ] E-8 provisions transport credentials and idempotent consumer topology; run the E-8 probe.
- [ ] SRE provisions DNS, TLS 1.3, WAF and rate limiting; run the gateway probe.
- [ ] Review and sign each redacted evidence package; never commit credentials.
- [ ] Commit only approved, non-secret status/evidence updates.

### Task 9: Institutional recovery, UAT, merge and release authorization

**Files:**
- Update: `docs/phase-8/uat/UAT_SIGNOFF.md`
- Update: `docs/phase-9/PILOT_ACCEPTANCE.md`
- Update: `docs/phase-9/ROLLOUT_DECISION.md`
- Update: `docs/production/FINAL_RELEASE_GATE_MATRIX.md`
- Update: `docs/production/FINAL_PRODUCTION_READINESS_REPORT.md`

**Interfaces:**
- Consumes: all green internal gates, four passing external probes, enterprise recovery evidence, and signed stakeholder approval.
- Produces: reviewed merge and truthful release authorization.

- [ ] Execute enterprise multi-node recovery and certify measured RPO/RTO.
- [ ] Execute role-based institutional UAT and obtain named, dated signatures.
- [ ] Run final verification from a clean clone at the exact release commit.
- [ ] Request final code review and fix all critical/important findings with RED-GREEN tests.
- [ ] Merge only after explicit authorization; tag the immutable release candidate.
- [ ] Have the release authority set `PRODUCTION_RELEASE_AUTHORIZED = TRUE` only when every gate is evidenced.
- [ ] Deploy canary, monitor SLOs, and retain the documented rollback path.
