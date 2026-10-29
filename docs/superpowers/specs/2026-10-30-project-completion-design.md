# Project Completion and Source-of-Truth Alignment Design

## Intent

Bring the E-7 Operations Engine from its current internally verified, pilot-capable state to a truthfully verified production-release candidate aligned with the authoritative specification, implementation plan, traceability matrix, and release gates.

## Goals

- Close every repository-controlled defect and verification gap.
- Make the full verification path reproducible on Windows developer machines and Linux CI.
- Provide executable probes for each external production dependency.
- Keep documentation, test counts, SBOM evidence, requirement status, and release authorization synchronized with current evidence.
- Preserve fail-closed production gates: no external dependency or human approval may be marked complete without real evidence.

## Non-goals

- Do not fabricate institutional credentials, production endpoints, stakeholder signatures, or enterprise recovery evidence.
- Do not enable Phase 10 conditional components without a separate evidence-backed ADR.
- Do not merge, push, deploy, or authorize production release without explicit user and institutional authorization.

## Constraints

- The authoritative PDFs under `docs/source-of-truth/` remain immutable.
- PostgreSQL remains the canonical system of record.
- AI remains non-authoritative; workflows and scheduling remain deterministic.
- External runtimes remain behind IR-owned adapters.
- All production code changes follow test-driven development.
- Completion claims require fresh executable evidence.

## Approaches considered

### 1. Evidence-first staged closure (selected)

Fix deterministic repository defects first, then strengthen local/CI verification, add external probes, reconcile current-state documentation, and finally execute external provisioning and institutional sign-off. This produces useful, independently verifiable increments and never confuses internal readiness with production authorization.

### 2. Big-bang production rollout

Provision every external system and attempt a single final acceptance run. This is faster only if every external team is already ready; otherwise it couples unrelated blockers and makes failures difficult to diagnose.

### 3. Documentation-only reconciliation

Update status documents without changing verification or runtime behavior. This would improve presentation but leave the Windows configuration defect, missing probes, and incomplete release gates unresolved, so it is rejected.

## Design

Work is divided into four independently gated tracks:

1. **Repository correctness:** fix cross-platform configuration, eliminate test-isolation defects, and keep existing behavior fail-closed.
2. **Verification and security:** define a single manifest-driven verification entry point, run all suites in appropriate environments, and make mandatory scans fail closed.
3. **External-boundary readiness:** implement non-destructive probes for MySQL, OIDC, E-8 messaging, and gateway/TLS/WAF, with machine-readable evidence and secret-safe output.
4. **Governance closure:** reconcile living documentation, obtain real external evidence and signatures, merge the reviewed branch, and authorize release only when every gate is green.

## Completion definition

The project is complete only when all 25 canonical requirements are verified, the complete test manifest is green in its declared environments, mandatory security scans have no unresolved critical/high findings, all four external probes pass against real production-equivalent endpoints, enterprise recovery and human UAT are signed, documentation matches generated evidence, the reviewed work is merged, and `PRODUCTION_RELEASE_AUTHORIZED` is truthfully set to `TRUE` by the release authority.
