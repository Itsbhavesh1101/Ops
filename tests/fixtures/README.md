# E-7 Versioned Test Fixtures

---

## 1. Purpose and Scope

These fixtures provide stable, reproducible, synthetic inputs for the automated testing and validation of the **IR Infotech E-7 Operations Engine**, specifically supporting:
* **Task Intelligence Evaluation**: Benchmarking prompt decomposition accuracy, PydanticAI structured output conformance, and DAG cycle prevention against gold-set baselines.
* **Workflow End-to-End Testing**: Validating Flowable BPMN sequence flows, parallel user tasks, DMN routing logic, and durable SLA timer transitions.
* **Generic Constraint Scheduling Regression**: Verifying multi-skill technician rostering, equipment binding, temporal windows, and shift constraints under Google OR-Tools CP-SAT.
* **Education Timetabling Benchmarking**: Verifying Grade 10 examination scheduling, room capacity bounds, invigilator certifications, and pedagogical gap rules across OR-Tools, UniTime, and FET.
* **Disruption and Targeted Schedule Repair**: Testing localized rescheduling upon resource unavailability, locked assignment immutability, and objective movement penalties.
* **Adapter Contract Testing**: Validating external ERP/SIS synchronization contracts, CalDAV/Exchange calendar integrations, and E-8 notification event mapping.
* **Integration and Recovery Testing**: Validating transactional outbox durability, RabbitMQ delivery recovery, and deduplication across broker outages.

> [!IMPORTANT]
> **TEST ASSETS ONLY — NOT PRODUCTION CONFIGURATION**  
> All files located within `tests/fixtures/` are strictly regression testing assets. They are not production defaults, not tenant initializations, and not application configurations.

---

## 2. Fixture Governance Rules

All test fixture datasets must strictly comply with the following 18 governance rules:

1. **Synthetic Identities Only**: All human names, employee IDs, student cohorts, course codes, facility names, and equipment IDs must be 100% synthetic.
2. **Zero Personally Identifiable Information (PII)**: No real-world student, teacher, employee, or customer data is permitted in any fixture file.
3. **Zero Production Credentials**: Never include real API tokens, client secrets, passwords, private keys, or non-mock provider URLs.
4. **Mandatory Metadata Attributes**: Every fixture dataset must declare:
   * `dataset_id`: Unique identifier for the dataset family instance (e.g., `RS-SYNTH-001`).
   * `dataset_version`: Semantic version string (e.g., `1.0.0`).
5. **Stable Dataset Identifiers**: Dataset IDs are permanently reserved and immutable once frozen.
6. **Semantic Versioning of Fixtures**: Fixture modifications follow semantic versioning (`MAJOR.MINOR.PATCH`).
7. **Provider-Neutral Canonical Format**: Canonical fixtures are defined in pure domain terms, independent of third-party execution runtimes.
8. **Adapter Translation Boundary**: Provider-specific adapters translate canonical fixture structures into vendor representations at runtime; vendor formats are never stored as canonical fixtures.
9. **Strict Production Code Isolation**: Production application packages under `app/` are strictly forbidden from importing from `tests/fixtures/` or `tests/`.
10. **Zero Production Default Pollution**: Fixture values must never become fallback defaults or hardcoded values in production code.
11. **SLA Independence**: Fixture SLA thresholds (warning offsets, breach durations) are test scenarios and must never become production SLA defaults.
12. **Solver Budget Independence**: Fixture solve-time budgets are regression boundaries and must never become production solver timeout defaults.
13. **Synthetic ID Isolation**: Fixture resource IDs (e.g., `res-inv-001`) must never become production database IDs or hardcoded seeds.
14. **Controlled Version Elevation**: Any modification to a fixture that alters an expected objective score, critical path, or solver diagnostic requires an explicit dataset version bump and peer review.
15. **Deterministic Data Preference**: Fixed, deterministic datasets are preferred over runtime random generation to guarantee reproducible test outcomes.
16. **Seeded Generator Persistence**: If procedural generation tools are ever employed, the random seed and the generated static dataset must both be version-controlled.
17. **Bounded Test Footprint**: Correctness regression fixtures must remain compact enough to execute routinely during developer pre-commit loops.
18. **Workload Separation**: High-volume stress, load, and benchmark scale datasets are segregated from correctness regression fixtures.

---

## 3. Canonical Interchange Schema vs. Production Database Schema

The JSON structures within `tests/fixtures/` mirror **Canonical Domain Concepts** as established in the authoritative *Complete Implementation Plan* and *Canonical Ownership and Service Boundaries Specification*.

> [!WARNING]
> **NOT AN ORM SCHEMA SPECIFICATION**  
> Fixture JSON files represent stable, provider-neutral interchange payloads. They do **NOT** define the final PostgreSQL database schema, SQLAlchemy models, Alembic migrations, or column constraints.
> 
> * Do **NOT** infer SQL column types or nullability rules directly from JSON types.
> * Do **NOT** infer final database enum representations from JSON string values.
> * Do **NOT** infer relational foreign keys, indexes, or join tables from JSON object hierarchies.
> * Do **NOT** infer database normalization levels or storage partitioning from fixture layout.

---

## 4. Synthetic External Identifier Policy

To test external system integration and mapping semantics without coupling to vendor databases, fixtures implement a strict two-tier identification scheme:

| Level | Prefix Convention | Purpose | Example |
| :--- | :--- | :--- | :--- |
| **Canonical Resource ID** | `res-*` | E-7 authoritative internal domain identifier used across planning, workflows, and scheduling. | `res-inv-001`, `res-tech-001` |
| **Synthetic External ID** | `SYN-*` | Upstream provider/ERP identifier used to test adapter mapping, external reference tracking, and synchronization. | `SYN-ERP-INV-001`, `SYN-ERP-TECH-001` |

* **Mapping Role Only**: Synthetic external IDs exist solely to verify adapter resolution and idempotent deduplication.
* **No Canonical Substitution**: External provider IDs must never be treated as canonical E-7 resource IDs within domain services.

---

## 5. Directory Architecture and Staged Realization Plan

The test fixture directory structure is planned across controlled implementation phases. Future directories are documented below for architectural clarity and will be realized in subsequent substeps:

```
tests/fixtures/
├── README.md                     # Fixture framework documentation and governance rules (Task 0F.2A)
├── resources/                    # Shared synthetic resource and availability catalog (Task 0F.2A)
│   └── RS-SYNTH-001.json         # Base feasible resource roster & availability intervals
├── scheduling/
│   ├── maintenance/              # Generic facility maintenance scheduling fixtures (Task 0F.2B)
│   └── exam/                     # Canonical Grade 10 semester exam scheduling fixtures (Task 0F.2C)
├── workflows/                    # Flowable BPMN/DMN test inputs and readiness payloads (Task 0F.2D)
├── ai_gold_set/                  # Task Intelligence prompts, expected schemas, and DAG baselines (Task 0F.2E)
└── integrations/                 # Calendar sync callbacks, E-8 notifications, webhook payloads (Task 0F.2F)
```

*(Note: In accordance with Task 0F.2A constraints, only `tests/fixtures/resources/` is instantiated during this task).*

---

## 6. Provider-Neutrality Invariants

Canonical fixtures contain zero dependencies on runtime technologies, vendor libraries, or engine-specific structures. Specifically, fixture JSON files must contain **NO**:
* Google OR-Tools CP-SAT variable names, interval variables, or `CpModel` structures.
* UniTime Java class names, XML element names, or solver parameters.
* FET XML elements or timetable tags.
* Flowable BPMN process definition IDs, execution tokens, or engine-internal variables.
* RabbitMQ delivery tags, exchange routing keys, or AMQP envelope headers.
* CalDAV, Google Calendar, or Microsoft Exchange SDK payloads.
* PydanticAI internal context structures or LLM system prompt fragments.

Provider-specific adapters translate canonical fixtures into the appropriate runtime formats in downstream test runners.
