# Phase 5 Education Benchmark Dataset Specification

## 1. Overview & Provenance
The Phase 5 Education Scheduling Benchmark utilizes a canonical, provider-neutral dataset to evaluate educational scheduling engines under realistic institutional constraints. The dataset strictly excludes real student Personally Identifiable Information (PII) and models a high-school / secondary-college examination session scenario.

All dataset artifacts are version-controlled, immutable, and cryptographically verified via SHA-256 digests in `E7-PHASE5-EDUCATION-BENCHMARK-MANIFEST@2.0.0`.

> [!IMPORTANT]
> **Source-of-Truth Isolation**: The frozen Phase-0 baseline fixtures (`tests/fixtures/resources/RS-SYNTH-001.json`, `tests/fixtures/scheduling/exam/SC-EXAM-*.json`, and `tests/fixtures/MANIFEST.json`) remain strictly immutable and frozen. Phase 5 maintains its dedicated, provider-neutral education benchmark dataset in `tests/fixtures/scheduling/education/phase5/` with its own manifest (`tests/fixtures/scheduling/education/phase5/MANIFEST.json` and `docs/phase-5/PHASE_5_BENCHMARK_MANIFEST.json`).

### Cryptographic Manifest & Hashes
| Dataset ID | Version | SHA-256 Checksum | Description |
| :--- | :---: | :---: | :--- |
| `PHASE5-RS-EDU-001` | 2.0.0 | `4a1ca0d0fcccbd53c336d15b1efea75340bc745bbfa390fb217e07f2f57ef02a` | Resource catalog (Rooms, Invigilators, Equipment, Cohorts, Subjects) |
| `PHASE5-SC-EXAM-FEASIBLE-001` | 2.0.0 | `96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a` | 10 Exam sessions baseline with equipment requirements across 5 days (Feasible) |
| `PHASE5-SC-EXAM-INFEASIBLE-001` | 2.0.0 | `ed40a105179764f6794c3a2e1a7543c34fee85a12439a756a6661a2c7de79f67` | Over-constrained 3-day window violating major exam gap |
| `PHASE5-SC-EXAM-REPAIR-001` | 2.0.0 | `46cb07ef7e6f089dfa32850a5df23cab1c355509c05f028d74e8536259e0e8a4` | Disruption scenario with 9 locked published assignments and equipment binding |

*Combined Canonical Benchmark Hash*: `e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b`

---

## 2. Temporal Horizon & Operating Windows
- **Timezone**: `Asia/Kolkata` (`+05:30`)
- **Horizon Start**: `2026-11-09T08:00:00+05:30` (Monday morning)
- **Horizon End**: `2026-11-13T17:00:00+05:30` (Friday evening)
- **Daily Operating Windows**:
  - Morning Exam Window: 09:00 – 11:00 (120 minutes)
  - Afternoon Exam Window: 13:00 – 15:00 (120 minutes)
- **Granularity**: 1-minute discrete time grid.

---

## 3. Entity Inventory

### 3.1 Student Cohorts
Each student cohort is modeled as an atomic scheduling unit requiring synchronized examination sessions:
1. **Grade 10A** (`cohort-g10-a`):
   - Student Count: 24
   - Grade Level: 10
   - Enrolled Subjects: `exam-g10-math`, `exam-g10-science`, `exam-g10-social`, `exam-g10-computer`, `exam-g10-language`
2. **Grade 10B** (`cohort-g10-b`):
   - Student Count: 30
   - Grade Level: 10
   - Enrolled Subjects: `exam-g10-math`, `exam-g10-science`, `exam-g10-social`, `exam-g10-computer`, `exam-g10-language`

### 3.2 Academic Subjects
| Subject ID | Name | Duration | Is Major | Required Invigilator Skills | Required Equipment |
| :--- | :--- | :---: | :---: | :--- | :--- |
| `exam-g10-math` | Mathematics | 120m | Yes | `INVIGILATION_GENERAL` | None |
| `exam-g10-science` | Science | 120m | Yes | `INVIGILATION_GENERAL` | `LAB_KIT` |
| `exam-g10-social` | Social Studies | 120m | No | `INVIGILATION_GENERAL` | None |
| `exam-g10-computer` | Computer Applications | 120m | No | `INVIGILATION_GENERAL` | None |
| `exam-g10-language` | Language | 120m | Yes | `INVIGILATION_GENERAL` | None |

### 3.3 Physical Examination Rooms
| Room ID | Name | Capacity | Room Features | Preferred For |
| :--- | :--- | :---: | :--- | :--- |
| `res-room-001` | Exam Room Alpha | 40 | `PROJECTOR`, `CLASSROOM` | `cohort-g10-b` (30 students) |
| `res-room-002` | Exam Room Beta | 32 | `CLASSROOM` | `cohort-g10-a` (24 students) |
| `res-room-003` | Science Lab Hall | 35 | `SCIENCE_LAB`, `EQUIPPED_LAB` | `exam-g10-science` |

*Room Capacity Invariant*: Rooms host at most one exam session concurrently. Student cohort size cannot exceed room seating capacity.

### 3.4 Invigilators (Proctors)
| Invigilator ID | Name | Certified Skills | Capacity | Preferred Cohort/Subject |
| :--- | :--- | :--- | :---: | :--- |
| `res-inv-001` | Invigilator Alpha | `INVIGILATION_GENERAL`, `SCIENCE`, `INVIGILATOR` | 1 | `exam-g10-science` |
| `res-inv-002` | Invigilator Beta | `INVIGILATION_GENERAL`, `MATHEMATICS`, `INVIGILATOR` | 1 | `exam-g10-math` |
| `res-inv-003` | Invigilator Gamma | `INVIGILATION_GENERAL`, `LANGUAGE`, `INVIGILATOR` | 1 | `exam-g10-language` |
| `res-inv-004` | Invigilator Delta | `INVIGILATION_GENERAL`, `ACCESSIBILITY_SUPPORT`, `INVIGILATOR` | 1 | `exam-g10-computer`, `exam-g10-social` |

### 3.5 Equipment Inventory (Provider-Neutral Resource)
| Equipment ID | Name | Type | Certified Capability | Scheduling Capacity | Daily Availability |
| :--- | :--- | :---: | :--- | :---: | :--- |
| `res-eq-004` | Mobile Science Lab Kit | `EQUIPMENT` | `LAB_KIT` | 1 | Nov 9–13, 08:00–17:00 daily |

*Equipment Invariant*: The mobile science lab kit is a discrete non-shareable physical apparatus required by `exam-g10-science` sessions (`session-science-g10a` and `session-science-g10b`). At most one session may utilize the equipment at any point in time.

---

## 4. Examination Sessions (10 Total)
Each cohort sits for each of the 5 subjects in 120-minute examination slots:
- `session-math-g10a`: Grade 10A Mathematics (Major)
- `session-math-g10b`: Grade 10B Mathematics (Major)
- `session-science-g10a`: Grade 10A Science (Major, requires `LAB_KIT`)
- `session-science-g10b`: Grade 10B Science (Major, requires `LAB_KIT`)
- `session-social-g10a`: Grade 10A Social Studies (Non-major)
- `session-social-g10b`: Grade 10B Social Studies (Non-major)
- `session-computer-g10a`: Grade 10A Computer Applications (Non-major)
- `session-computer-g10b`: Grade 10B Computer Applications (Non-major)
- `session-language-g10a`: Grade 10A Language (Major)
- `session-language-g10b`: Grade 10B Language (Major)

---

## 5. Constraint Catalog & Policies

### 5.1 Hard Constraints (Zero Violation Tolerance)
1. **`NO_COHORT_DOUBLE_BOOKING`**: A student cohort can attend at most one examination session at any point in time.
2. **`NO_ROOM_DOUBLE_BOOKING`**: An examination room can host at most one examination session at any point in time.
3. **`NO_INVIGILATOR_DOUBLE_BOOKING`**: An invigilator can proctor at most one examination session at any point in time.
4. **`NO_EQUIPMENT_DOUBLE_BOOKING`**: An equipment resource can be allocated to at most one examination session at any point in time.
5. **`REQUIRED_EQUIPMENT_AVAILABLE`**: Every session specifying mandatory equipment must be allocated an eligible equipment resource or room possessing the required capability.
6. **`ROOM_CAPACITY`**: Room seating capacity must be strictly $\ge$ cohort student count.
7. **`INVIGILATOR_ROLE_ELIGIBILITY`**: Invigilator must possess the required qualification/skill for the scheduled session.
8. **`DURATION_WITHIN_WINDOW`**: Sessions must be placed entirely within allowed daily time windows (09:00-11:00 or 13:00-15:00).
9. **`MAJOR_SUBJECT_MIN_GAP`**: For major subjects (`major_subject=True`), the same cohort must have at least 1 clear calendar day between major exams.
10. **`LOCK_PRESERVATION`**: Published assignments marked with `locked=True` are immutable and must not be moved or altered in repair scenarios.

### 5.2 Soft Constraints (Normalized Weights)
1. **Preferred Invigilator** (Weight 2): Assign sessions to the designated preferred invigilator where possible.
2. **Preferred Room** (Weight 1): Assign sessions to cohort-preferred examination rooms.
3. **Invigilator Workload Balance** (Weight 1): Minimize variance in assigned proctoring load across available faculty.
