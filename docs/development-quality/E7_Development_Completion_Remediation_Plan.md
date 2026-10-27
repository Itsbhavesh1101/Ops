# E-7 Operations Engine — Evidence-Gated Development Completion Plan

**Prepared:** 27 October 2026  
**Repository:** `https://github.com/Itsbhavesh1101/Ops`  
**Last remote baseline reviewed:** `master` / `main` at `7bd99f173af17cb2702f68e928d758921cd384e8`  
**Previously finalized Stage 8 commit:** `4db706c74ed95d659480ec2416b457dcdc8250c4`  
**Frozen Phase-8 release candidate:** `03e6305256db3d838b472aaac50893ddb513414e` — preserve unchanged.

## Purpose and governance

This plan **does not reopen or replace** the eight earlier production-readiness stages. It is a separate, bounded **development-quality remediation track**, numbered `DQ-1` through `DQ-6`. No production deployment, release authorization, remote push, new release tag, or alteration of historical commits is authorized by this plan.

Read the four immutable original PDFs in `docs/source-of-truth/`, then `docs/SOURCE_OF_TRUTH.md`, `docs/IMPLEMENTATION_GUARDRAILS.md`, accepted ADRs and their amendments, `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`, the implementation itself, and actual test evidence. Where they differ, honor documented precedence: the Complete Specification controls functional acceptance; the Complete Implementation Plan controls implementation sequence; the Open-Source Research Report controls technology selection; accepted ADRs record approved architectural decisions. Stop on an unresolved substantive conflict. Product acceptance depends on **actual behavior**, not labels in agent-authored reports.

**Original requirements:** 9 Task Intelligence (`FR-TI-01`–`09`), 7 Workflow (`FR-WF-01`–`07`), 6 Scheduling (`FR-SC-01`–`06`), 3 Integration (`FR-INT-01`–`03`) = **25 total**. Also verify nonfunctional requirements, security and audit, API consistency, and the four-pillar user experience. Specific claimed implementation paths and test outcomes from a previous Gemini audit are **leads, not proof**: that report contains inaccurate source filenames and references.

## Verified baseline issues and hypotheses

| Finding | Evidence status | Recommended handling |
|---|---|---|
| GitHub Actions fails `ruff check .` at `tests/performance/test_real_http_load.py` | **Confirmed in remote CI logs** | Fix import formatting and repeat exactly the CI lint command. |
| `tests/performance/test_real_http_load.py` imports nonexistent `app.scheduling.education.benchmark` | **Confirmed in remote source and tree** | Reconnect to existing `tests/fixtures/scheduling/education/dataset.py` only after checking exports and test isolation. |
| `FR-TI-09` recommendation engine/route | **Not found in inspected remote Task Intelligence and API modules** | Verify all implementations, then implement mandatory next-best-task guidance with rationale, appropriate confidence and tenant-safe tests. |
| Three `PlaceholderPage` routes (`/workflows/:instanceId`, `/analytics`, `/audit`) | **Confirmed in `ui/operations-ui/src/App.tsx`** | Deliver working role-authorized views against actual mounted endpoints. |
| Committed OpenAPI omits implemented `/api/v1/auth/me` | **Confirmed in remote source and committed schema** | Fix using existing `scripts/export_openapi.py`, test operation/security parity; don't fabricate endpoints. |
| Capacity fairness test mocks `.all()` but service calls `.tuples().all()` | **Confirmed static mismatch** | Verify trace and minimally correct fixture, preserve fairness assertion. |
| Earlier Gemini report: 21 unit failures (including 15 calendar tests) | **Claimed by agent, not independently rerun** | Re-run and classify individually; do not presume all are mocks. |
| `FR-TI-07` risk detector marks resource-capacity assessment `is_evaluable=False` | **Confirmed in module** | Determine if later code supplies real capacity signals; otherwise classify gap. |
| `FR-TI-04` historical effort recalibration | **Implementation has optional historical-median interface** | Verify actual persisted-history provider and closed-loop behavior before claiming missing. |
| ADR-012 requires notification-intent persistence within business transaction; runtime builds command after consuming domain event | **Text/sequence tension confirmed** | Trace crash and broker retention semantics; fix only if proved noncompliant and approved via ADR governance. |
| Business routes opt-in (`mount_domain_routers=False`), readiness health returns static ready | **Confirmed in config/main/router** | Verify production startup configuration and health/readiness contract; strengthen fail-closed where policy requires. |
| Frontend embedded mode | **Scope interpretation unresolved** | Source describes API-driven integration; do not require an iframe/postMessage SDK unless source/accepted design explicitly demands it. |

## Execution discipline for every DQ phase

1. Verify local `master` HEAD against remote current HEAD, stop on unexpected dirty tracked files; preserve existing unrelated files.
2. Record exact requirements, actual code locations, reproducer, tests, risk and impact before editing.
3. Make the **minimum coherent change** with meaningful negative/positive regression tests; never weaken test expectations or authorization for green results.
4. Run relevant test suites, `ruff check .`, `mypy app`, frontend checks as applicable, and the source's relevant integration gates. Classify unavailable infrastructure honestly.
5. Never mutate institution-owned MySQL, company services, non-disposable DBs, or historical frozen RC/tag.
6. Leave each phase's changes **uncommitted** for independent review unless the user separately authorizes a local commit; never push automatically.
7. For each phase, report files changed, old/new test results, leftover defects, source references, exact SHA, status `PASS / PARTIAL / BLOCKED`, and next phase recommendation. Do not start the next DQ phase automatically.

## DQ-1 — CI and test-collection recovery **START HERE**

**Goal:** restore reliable source-quality and test discovery before changing business logic.

**Tasks:**
- Confirm current `master`, working tree, frozen RC. Review `.github/workflows/ci.yml`, `pyproject.toml`, `pytest.ini`/config, performance-test fixtures and existing CI scope.
- Reproduce current `ruff check .` failure; fix the actual import ordering and nonexistent benchmark import without manipulating test discovery. Use the already existing dataset function; check whether performance test imports other missing dependencies after repair.
- Run **non-destructive** `pytest --collect-only -q`; inventory total, skipped/xfail and environmental/import errors. Do not execute live HTTP load tests automatically.
- Verify and regenerate stale `docs/production/openapi.json` through **existing** `scripts/export_openapi.py` if drift is reproduced; compare actual mounted route operation and security declarations. Do not alter API behavior just to make a schema test pass.
- Identify and rerun the 21 previously reported failing unit tests **if safe and feasible**, preserving exact trace data. Fix narrowly proven test-harness defects, not production behavior, and stop if a genuine app defect or architecture change requires a separate decision.
- Check `ruff check .`, `mypy app`, and `pytest tests/unit/ -q` only with safe local DB/test isolation. GitHub Actions must not silently skip mandatory tests; propose CI-matrix improvements separately rather than widening phase scope without need.
- Where local unit tests show confirmed production defects, leave those failures visible and classify them for DQ-2/DQ-4 instead of changing production code under DQ-1.

**Exit gate:** Global collection no longer fails due to code imports; repository-wide Ruff passes; OpenAPI documented operation parity is restored if the mismatch is reproducible; all *safely executable* unit tests run and each failure is fixed or fully diagnosed, not suppressed. CI scope and remaining environment-only tests are truthfully documented. This phase may be PARTIAL if an application defect remains for a subsequent DQ work package.

## DQ-2 — Original requirements and Task Intelligence completion

**Goal:** implement exactly the missing required plan capabilities, with correct source IDs.

**Tasks:**
- Rebuild an evidence-backed 25-item RTM from the immutable Complete Specification, matching each to actual code/test/API/UI evidence; mark `implemented/tested/externally blocked` separately.
- Implement `FR-TI-09` **after confirming absence**: deterministic next-best-task prioritization based on authenticated tenant, valid task readiness, predecessors, risk, slack, resource eligibility and policy constraints; explain reasons and meaningful uncertainty/confidence. No LLM-owned approvals or unvalidated state changes. Add stable read-only API under canonical plan routes and tenant/RBAC/negative tests.
- Verify `FR-TI-04` estimation overrides, historical medians and actual feedback; `FR-TI-07` risk capacity, blockers, recalculation and downstream consumers. Implement genuinely missing parts only.
- Verify the remaining Plan features with materialized data and UI, not mocked success alone.

**Exit gate:** FR-TI-01 through -09 match source meaning and have targeted unit, API/integration and where appropriate UI verification. No incorrect ID substitution.

## DQ-3 — Frontend and complete user journeys

**Goal:** eliminate visible unfinished functionality and mismatched API contracts.

**Tasks:** Implement `/workflows/:instanceId`, `/analytics`, `/audit` using existing or verified new backend operations; enforce verified permissions/tenant filtering and ensure empty/error/loading states. Add plan recommendation UI. Trace all reachable routes and protected actions against the actual OpenAPI/runtime. Verify keyboard/accessibility and a representative Plan → Automate → Schedule → Monitor browser journey. Distinguish API-driven Embedded Mode from an additional iframe requirement (do not invent latter).

**Exit gate:** No mandatory user-facing stub for delivered product scope; real API-backed data; positive and forbidden/tenant-negative UI tests; build/typecheck/Vitest/browser proof where safe.

## DQ-4 — Integrations, calendar, events and messaging correctness

**Goal:** verify durable external boundaries and resolve real failures without redesign by assertion.

**Tasks:** trace ADR-005/ADR-012 through domain transaction→outbox→RabbitMQ→inbox→`NotificationCommand`→E-8. Check commit/ACK ordering, DLQ/retry/retention, publisher confirms, `command_id` idempotence, state under broker/E-8 failures, and the literal ADR notification intent. If a substantive architectural mismatch exists, stop for approved decision. Diagnose Calendar test failures with real traceback and test DB isolation; check iCalendar/CalDAV compatibility and provider mapping. Verify ERP SELECT-only/11-table restrictions, domain event schema compatibility, workflow recovery and solver paths.

**Exit gate:** Integration tests honestly pass in authorized disposable infrastructure, or remain explicitly external-blocked. No unapproved ADR change.

## DQ-5 — Security, readiness, performance and contract hardening

**Goal:** close remaining internal correctness/security/operational concerns.

**Tasks:** verify production route enablement and fail-closed environment validation; make readiness accurately represent required dependencies or distinguish shallow and deep probes; check backend authorization and frontend tenant fallback behavior, Google OIDC boundaries, API idempotency, outbox/inbox transactions, negative tenant tests, worker lifecycle, real telemetry, and resource limits. Reverify OpenAPI drift repair from DQ-1 and add sustainable API contract-drift prevention to CI, compare actual live operations/security schemas. Run authorized targeted performance/solver/AI evaluations. Use Trivy and SBOM if installed/available; classify missing scans as missing, not passed.

**Exit gate:** contracts consistent, internal security and readiness controls tested, critical/high internal defects zero; external production approvals remain separate.

## DQ-6 — Full verification and evidence-based closure

**Goal:** make a justified engineering-complete decision, not claim perfection.

**Tasks:** repeat full safe backend collection/suites; unit+integration+contract+security+recovery+frontend+E2E; lint, mypy, typecheck, build, approved scans; cross-check all 25 original requirements and measurable acceptance criteria; align RTM, OpenAPI, Stage-8 handover and release gate claims only after evidence. Record test/CI SHA and blocked environments. Do not change historical Stage 8 or frozen RC. Prepare optional reviewable follow-up release candidate *proposal*, not create a tag or authorize release.

**Exit gate:** All mandatory internal requirements have working source and sufficient tests; no unresolved P0/P1 engineering defects; CI gate objectively green; known blocked external integrations classified honestly. Otherwise verdict `DEVELOPMENT_REMEDIATION_IN_PROGRESS`.

## Canonical external blockers (unchanged)

- `MYSQL_LIVE_READ_VERIFICATION`
- `PRODUCTION_COMPANY_OIDC`
- `E8_NOTIFICATION_INTEGRATION`
- `PRODUCTION_GATEWAY`

Additional external activities: signed institutional UAT, live Prometheus/Alertmanager delivery, enterprise recovery RPO/RTO and vulnerability scanning where not locally executable. None may be presumed accepted from unit mocks.

## First action

Give the coding agent **DQ-1 only**. Require exact baseline SHA, reproduced failures, smallest safe repair, `ruff check .`, test collection, separately categorized 21 historical unit failures, and an evidence report. Review its results before issuing DQ-2.
