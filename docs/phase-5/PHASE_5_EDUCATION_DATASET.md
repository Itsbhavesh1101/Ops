# Phase 5 Education Benchmark Dataset Specification

## 1. Overview & Provenance
The Phase 5 Education Scheduling Benchmark utilizes a canonical, provider-neutral dataset to evaluate educational scheduling engines under realistic institutional constraints. The dataset strictly excludes real student Personally Identifiable Information (PII) and models a high-school / secondary-college examination session scenario.

All dataset artifacts are version-controlled, immutable, and cryptographically verified via SHA-256 digests in `E7-PHASE5-EDUCATION-BENCHMARK-MANIFEST@1.0.0`.

### Cryptographic Manifest & Hashes
| Dataset ID | Version | SHA-256 Checksum | Description |
| :--- | :---: | :---: | :--- |
| `RS-SYNTH-001` | 1.0.0 | `a85fed2b6069000bcbaa27c897483bff5a70aee9c00f5f25568a812df1609fb6` | Resource catalog (Rooms, Invigilators, Cohorts, Subjects) |
| `SC-EXAM-FEASIBLE-001` | 1.0.0 | `afa37a4c329cd84285d014b57d6fa6904bc1c64be1c817c78d94430840e98781` | 10 Exam sessions baseline across 5 days (Feasible) |
| `SC-EXAM-INFEASIBLE-001` | 1.0.0 | `f9f7d824a43ff67cacad389f887416d683c11580afc4a411d102277cb2c99279` | Over-constrained 3-day window violating major exam gap |
| `SC-EXAM-REPAIR-001` | 1.0.0 | `0b7f1cd1446ed0feb2876af107e86716e62f8e09786332d983dc55004e5a4748` | Disruption scenario with 9 locked published assignments |

---

## 2. Temporal Horizon & Operating Windows
- **Timezone**: `Asia/Kolkata` (`+05:30`)
- **Horizon Start**: `2026-11-09T08:00:00+05:30` (Monday morning)
- **Horizon End**: `2026-11-13T17:00:00+05:30` (Friday evening)
- **Daily Operating Windows**:
  - Morning Exam Window: 09:00 – 11:00 (120 minutes)
  - Afternoon Exam Window: 14:00 – 16:00 (120 minutes)
- **Granularity**: 1-minute discrete time grid.

---

## 3. Entity Inventory

### 3.1 Student Cohorts
Each student cohort is modeled as an atomic scheduling unit requiring synchronized examination sessions:
1. **Grade 10A** (`cohort-g10-a`):
   - Student Count: 24
   - Grade Level: 10
   - Enrolled Subjects: `subj-math-10`, `subj-sci-10`, `subj-soc-10`, `subj-eng-10`, `subj-comp-10`
2. **Grade 10B** (`cohort-g10-b`):
   - Student Count: 30
   - Grade Level: 10
   - Enrolled Subjects: `subj-math-10`, `subj-sci-10`, `subj-soc-10`, `subj-eng-10`, `subj-comp-10`

### 3.2 Academic Subjects
| Subject ID | Name | Code | Is Major | Required Room Features |
| :--- | :--- | :--- | :---: | :--- |
| `subj-math-10` | Mathematics 10 | MATH101 | Yes | Standard Desks |
| `subj-sci-10` | Science & Physics 10 | SCI101 | Yes | Lab Benches / Lab Safety |
| `subj-soc-10` | Social Studies 10 | SOC101 | Yes | Standard Desks |
| `subj-eng-10` | English Language & Literature | ENG101 | No | Standard Desks |
| `subj-comp-10` | Computer Applications 10 | CS101 | No | Workstations / Standard Desks |

### 3.3 Physical Examination Rooms
| Room ID | Name | Capacity | Room Features | Preferred For |
| :--- | :--- | :---: | :--- | :--- |
| `res-room-001` | Exam Room Alpha | 32 | Standard Desks, Projector | `cohort-g10-a` (24 students) |
| `res-room-002` | Exam Room Beta | 40 | Standard Desks | `cohort-g10-b` (30 students) |
| `res-room-003` | Science Lab Hall | 35 | Lab Benches, Lab Safety, Workstations | `subj-sci-10`, `subj-comp-10` |

*Room Capacity Invariant*: Rooms host at most one exam session concurrently. Student cohort size cannot exceed room seating capacity.

### 3.4 Invigilators (Proctors)
| Invigilator ID | Name | Certified Roles / Skills | Max Daily Exams | Preferred Cohort/Subject |
| :--- | :--- | :--- | :---: | :--- |
| `res-inv-001` | Dr. Sharma (Faculty Alpha) | `CHIEF_INVIGILATOR`, `STEM_QUALIFIED` | 2 | `subj-math-10`, `cohort-g10-a` |
| `res-inv-002` | Prof. Rao (Faculty Beta) | `INVIGILATOR`, `STEM_QUALIFIED` | 2 | `subj-sci-10`, `cohort-g10-b` |
| `res-inv-003` | Ms. Anita (Faculty Gamma) | `INVIGILATOR`, `HUMANITIES_QUALIFIED` | 2 | `subj-soc-10`, `subj-eng-10` |
| `res-inv-004` | Mr. Vikram (Faculty Delta) | `INVIGILATOR`, `GENERAL_QUALIFIED` | 2 | `subj-comp-10` |

---

## 4. Examination Sessions (10 Total)
Each cohort sits for each of the 5 subjects in 120-minute examination slots:
- `sess-10a-math`: Grade 10A Mathematics (Major)
- `sess-10b-math`: Grade 10B Mathematics (Major)
- `sess-10a-sci`: Grade 10A Science (Major, requires `res-room-003`)
- `sess-10b-sci`: Grade 10B Science (Major, requires `res-room-003`)
- `sess-10a-soc`: Grade 10A Social Studies (Major)
- `sess-10b-soc`: Grade 10B Social Studies (Major)
- `sess-10a-eng`: Grade 10A English (Non-major)
- `sess-10b-eng`: Grade 10B English (Non-major)
- `sess-10a-comp`: Grade 10A Computer Applications (Non-major)
- `sess-10b-comp`: Grade 10B Computer Applications (Non-major)

---

## 5. Constraint Catalog & Policies

### 5.1 Hard Constraints (Zero Violation Tolerance)
1. **`NO_COHORT_DOUBLE_BOOKING`**: A student cohort can attend at most one examination session at any point in time.
2. **`NO_ROOM_DOUBLE_BOOKING`**: An examination room can host at most one examination session at any point in time.
3. **`NO_INVIGILATOR_DOUBLE_BOOKING`**: An invigilator can proctor at most one examination session at any point in time.
4. **`ROOM_CAPACITY`**: Room seating capacity must be strictly $\ge$ cohort student count.
5. **`INVIGILATOR_ROLE_ELIGIBILITY`**: Invigilator must possess the required qualification/role for the scheduled session (e.g. `STEM_QUALIFIED` for Science).
6. **`DURATION_WITHIN_WINDOW`**: Sessions must be placed entirely within allowed daily time windows (09:00-11:00 or 14:00-16:00).
7. **`MAJOR_SUBJECT_MIN_GAP`**: For major subjects (`is_major=True`), the same cohort must have at least 1 clear calendar day between major exams (2460 minutes between session end and subsequent session start across day boundaries).
8. **`LOCK_PRESERVATION`**: Published assignments marked with `is_locked=True` are immutable and must not be moved or altered in repair scenarios.

### 5.2 Soft Constraints (Normalized Weights)
1. **Preferred Invigilator** (Weight 2): Assign sessions to the designated preferred invigilator where possible.
2. **Preferred Room** (Weight 1): Assign sessions to cohort-preferred examination rooms.
3. **Invigilator Workload Balance** (Weight 1): Minimize variance in daily assigned proctoring load across available faculty.
