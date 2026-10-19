# Phase 9C — Pilot Feedback, Schedule Overrides & Workflow Exception Report

**Phase**: `9C`  
**Execution Status**: `PASS`  
**Pilot Tenant ID**: `tenant-pilot-001`  
**Pilot Domain**: `Semester Examination`  
**Schema Status**: `0014 (head)` — No Migration 0015 Required  

---

## 1. Executive Summary

Phase 9C evaluated and proved structured pilot feedback capture, operational schedule override mechanisms, and workflow exception handling for the restricted pilot of tenant `tenant-pilot-001` under the Semester Examination domain.

Key operational findings and drill results:
1. **Structured Feedback Persistence**:
   - The repository's canonical audit event system (`audit_events` table) natively supports structured operational feedback and annotations via `event_type="pilot.feedback_captured"` with arbitrary JSON payloads, actor principal attribution, and correlation tracking.
   - **Finding**: Creating migration 0015 for a dedicated feedback table is **not required** (`PHASE_9C_SCHEMA_CHANGE_REQUIRED = false`). The database schema remains strictly at `0014 (head)` with 26 application tables and 27 public tables.
2. **Schedule Override Contract Audit**:
   - Direct, arbitrary slot or assignment mutation is **`NOT_SUPPORTED_BY_EXISTING_CONTRACT`** because unconstrained manual changes violate hard business invariants (e.g., room capacity, double-booking, and cohort rest gap policies).
   - The supported operational override contract is `POST /api/v1/schedules/{id}/repair` via `SchedulingService.repair_schedule`. Exercising this contract with an invigilator absence disruption (`res-inv-001`) generated candidate schedule version 2 (`REVIEW`, superseding version 1) with **0 hard constraint violations**.
3. **Workflow Exception Drill**:
   - The deployed `exam-operations-v1` BPMN and `exam_readiness_rules_v1` DMN decision table natively support conditional exception routing.
   - Ingesting `curriculum_coverage = 85` evaluated DMN Rule 2 (`curriculum_coverage < 100`), returning status `BLOCKED` and recommendation `CURRICULUM_REMEDIATION_REQUIRED`.
   - The BPMN engine routed the process to `curriculum_remediation` user task, completely bypassing `readiness_moderation`.
   - Completing the remediation user task cleanly transitioned the instance to `end_remediation`.

---

## 2. Structured Pilot Feedback Capture

### Architecture & Contract Audit

Existing schema and repository audit:
- The database schema is frozen at migration `0014`.
- The transactional `audit_events` table stores immutable, hash-chained events with `details` JSONB column.
- The `AuditRepository.append` method records structured operational events with actor principal, tenant ID, and correlation tracking.

### Captured Feedback Evidence

| Field | Value |
|---|---|
| **Feedback ID** | `fb-drill-9c-20261019113808` |
| **Correlation ID** | `corr-drill-9c-20261019113808` |
| **Reporter Principal** | `cred-pilot-exam-admin` |
| **Reporter Role** | `pilot_exam_coordinator` |
| **Category** | `USABILITY_ASSESSMENT` |
| **Severity** | `LOW` |
| **Comment** | `Automated Grade 10 exam schedule verified against room capacities and invigilator qualifications.` |
| **Operational Status** | `ACKNOWLEDGED` |
| **Timestamp** | `2026-10-19T11:38:08.816286+00:00` |
| **Schema Change Required** | `false` |
| **Migration 0015 Needed** | `false` |

---

## 3. Schedule Override / Correction Drill

### Contract Evaluation

- **Direct Slot Mutation Contract**: `NOT_SUPPORTED_BY_EXISTING_CONTRACT`
  - In a CP-SAT constraint programming environment, allowing arbitrary database row mutation for time slots or assignments violates global constraints, invigilator qualification matching, and student cohort gap policies.
- **Supported Operational Repair Contract**: `POST /api/v1/schedules/{id}/repair`
  - Domain service: `SchedulingService.repair_schedule`
  - Implements bounded incremental rescheduling while minimizing disruption to unchanged assignments using a movement penalty weight.

### Drill Execution Evidence

1. **Baseline Schedule**:
   - ID: `a619dda2-b612-4917-87f8-9b13ed879df6`
   - Lifecycle Status: `PUBLISHED`
   - Version: 1
2. **Disruption Scenario**:
   - Disrupted Resource: `res-inv-001` (Invigilator 1 simulated absence)
   - Interval: 3-hour exam block
   - Movement Weight: 10.0
3. **Repair Solve Outcome**:
   - Status: `PASS`
   - Candidate Schedule ID: `087b86aa-2b5e-40b8-b6b4-851ed6e35379`
   - Candidate Version: 2
   - Supersedes ID: `a619dda2-b612-4917-87f8-9b13ed879df6`
   - Candidate Status: `REVIEW`
   - Hard Constraint Violations: **0**

---

## 4. Workflow Exception Drill

### Exception Routing Architecture

In `workflows/dmn/exam_readiness_rules_v1.dmn13.xml`:
- **Rule 1**: `curriculum_coverage >= 100` and `science_approved == true` and `humanities_approved == true` $\rightarrow$ `PROCEED`, `ALL_SYSTEMS_GO`
- **Rule 2**: `curriculum_coverage < 100` $\rightarrow$ `BLOCKED`, `CURRICULUM_REMEDIATION_REQUIRED`
- **Rule 3**: `science_approved == false` or `humanities_approved == false` $\rightarrow$ `REJECTED`, `DEPARTMENT_APPROVAL_DENIED`

In `workflows/bpmn/semester_exam_readiness.bpmn20.xml`:
- Conditional gateway evaluates DMN decision variable `readiness_status`:
  - `PROCEED` $\rightarrow$ `readiness_moderation`
  - `BLOCKED` $\rightarrow$ `curriculum_remediation`
  - `REJECTED` $\rightarrow$ `end_rejected`

### Exception Execution Evidence

1. **Instance Initialization**:
   - Process Definition: `exam-operations-v1`
   - Engine Instance ID: `8efc10e2-cbb1-11f1-9c9c-5e7867d890d2`
   - Ingested Variables: `curriculum_coverage = 85`, SLA warning timer (+10m), SLA breach timer (+60m)
2. **Parallel Department Reviews**:
   - `science_dept_review`: completed with `science_approved = true`
   - `humanities_dept_review`: completed with `humanities_approved = true`
3. **DMN Decision Evaluation**:
   - Evaluated status: `BLOCKED`
   - Evaluated recommendation: `CURRICULUM_REMEDIATION_REQUIRED`
4. **Exception Path Verification**:
   - Active task captured: `curriculum_remediation`
   - Moderation task bypassed: `readiness_moderation` not created
5. **Remediation Completion**:
   - Action: `COMPLETE` with variables `remediation_notes = "Curriculum gaps logged for syllabus review"`
   - Engine instance transitioned cleanly to `end_remediation`

---

## 5. Artifact Verification & Sign-off

- **Artifact JSON**: `docs/phase-9/pilot_feedback_and_exceptions.json`
- **Audit Immutability**: All actions recorded in PostgreSQL `audit_events`.
- **Database Schema**: Preserved at `0014 (head)`, 26 app tables, 27 public tables.
