# Phase 2B: Privacy-Safe Context Builder Runtime

**Document Identifier:** `E7-DOC-P2B-002`  
**Version:** `1.0.0` (Phase 2B Baseline)  
**Status:** COMPLETE / FROZEN (Phase 2B Core Engineering)  
**Module:** `app.task_intelligence.context`  

---

## 1. Scope & Frozen Contract Compliance

The Context Builder implements the Phase-0 contract `E7-CTR-CB-001` (`CONTEXT_BUILDER_CONTRACT.json` / `.md`):
- Exactly **7 supported categories**:
  1. `organization_context`
  2. `academic_context`
  3. `subject_context`
  4. `staff_resource_context`
  5. `commitment_context`
  6. `calendar_context`
  7. `aggregate_context`
- Exactly **4 assembly statuses**:
  - `READY`
  - `DEGRADED`
  - `BLOCKED`
  - `REQUIRES_CLARIFICATION`

---

## 2. Field Allowlist & Data Minimization (28 Fields)

Model context exposure is constrained to exactly 28 `PLANNER_CONTEXT_ALLOWED` fields across evaluated tables:
1. `annual_calendar.from_date`
2. `annual_calendar.to_date`
3. `annual_calendar.description`
4. `classes.class`
5. `department.department_name`
6. `events.event_title`
7. `events.event_description`
8. `events.start_date`
9. `events.end_date`
10. `events.event_type`
11. `sections.section`
12. `sessions.session`
13. `staff.qualification`
14. `staff.work_exp`
15. `staff.name` (sanitized/masked to opaque reference)
16. `staff.surname` (sanitized/masked to opaque reference)
17. `staff_designation.designation`
18. `subject_group_class_sections.description`
19. `subject_groups.name`
20. `subject_groups.description`
21. `subject_timetable.day`
22. `subject_timetable.time_from`
23. `subject_timetable.time_to`
24. `subject_timetable.start_time`
25. `subject_timetable.end_time`
26. `subjects.name`
27. `subjects.type`
28. `subjects.code`

All other fields are subject to **default deny**.

---

## 3. Strict Student & Staff Privacy Rules

- **Student PII (`students`, `student_session`):** STRICTLY `AGGREGATE_ONLY`. Zero individual student rows, names, emails, contact details, or attendance history enter model context. Only derived cohort headcounts are provided (e.g. `cohort_student_headcounts: {'Grade 10 - Section A': 42}`).
- **Staff Resources (`staff`):** Mapped to opaque reference tokens (e.g. `StaffRef_1`, `StaffRef_2`). Raw upstream primary keys are strictly isolated in `validation_context.raw_entity_id_map`.
- **Descriptive Staff Semantics:** Staff attributes do NOT imply task eligibility, capability, or invigilation certification.

---

## 4. Calendar & Timetable Semantics

- **Calendar Dates:** Informational context only. Holidays and events do not enforce automated hard blackouts without explicit business policy.
- **Timetable Slots:** Represent known busy windows. A free slot does NOT imply complete staff availability.
- **Historical Attendance:** Never interpreted as forward availability.

---

## 5. Governance Gates (BLK-01 & BLK-02)

- **BLK-01:** `LIVE MYSQL PRODUCTION CONTEXT RETRIEVAL BLOCKED PENDING AUTHORITATIVE TENANT/ORGANIZATION SCOPING`.
  - `MYSQL_PRODUCTION_CONTEXT_RETRIEVAL = DISABLED_BY_GOVERNANCE`.
  - Zero live connections to company production MySQL. Verified with synthetic test adapters (`SyntheticContextAdapter`).
- **BLK-02:** Physical room scheduling gate. Does not block general academic planning or WBS generation.
