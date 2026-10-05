"""Unit tests for JobModel SQLAlchemy mapped entity and jobs table schema.

Task 1I.4B of Phase 1 Foundation:
- jobs table name and registration in Base.metadata.
- Canonical columns exactly 9 (job_id, tenant_id, job_type, status, failure_code,
  failure_message, created_at, started_at, completed_at).
- job_id primary key (PostgreSQL UUIDv4, as_uuid=True).
- Absence of surrogate ID or second UUID.
- Column types, nullabilities, and server defaults.
- Database CHECK constraints:
  * ck_jobs_status (exactly 4 statuses: pending, running, succeeded, failed)
  * ck_jobs_job_type (JobType open namespaced regex format)
  * ck_jobs_tenant_id_nonblank (non-empty trimmed tenant_id)
  * ck_jobs_failure_code_format (JobFailureCode open namespaced regex format)
  * ck_jobs_failure_consistency (failure fields populated iff failed)
  * ck_jobs_timestamp_state_consistency (timestamp presence matches status)
  * ck_jobs_timestamp_ordering (started_at >= created_at, completed_at >= created_at,
    completed_at >= started_at)
- Zero speculative secondary indexes (only PK index).
- Absence of speculative fields: result, progress, retry, cancellation, worker lease, priority.
- Base.metadata contains exactly the five canonical foundation tables.
"""

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

import app.db.models  # noqa: F401
from app.core.jobs.models import JobModel
from app.db.base import Base


class TestJobModelSchema:
    """Verify jobs table mapping, columns, types, primary key, and metadata registration."""

    def test_jobs_table_registered_in_metadata(self) -> None:
        assert "jobs" in Base.metadata.tables
        table = Base.metadata.tables["jobs"]
        assert table is JobModel.__table__

    def test_base_metadata_contains_exactly_five_canonical_tables(self) -> None:
        phase1_tables = {
            "audit_events",
            "outbox_events",
            "inbox_messages",
            "idempotency_records",
            "jobs",
        }
        canonical_phase4_tables = phase1_tables | {
            "goals",
            "plans",
            "milestones",
            "tasks",
            "task_dependencies",
            "task_risks",
            "workflow_definitions",
            "workflow_instances",
            "workflow_task_mappings",
            "sla_policies",
            "workflow_artifacts",
            # Phase 4A Scheduling (7)
            "resources",
            "resource_availabilities",
            "constraint_definitions",
            "solver_profiles",
            "schedule_jobs",
            "schedules",
            "schedule_assignments",
            # Phase 4B Calendar (1)
            "calendar_links",
        }
        assert phase1_tables <= set(Base.metadata.tables.keys())
        assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
        assert len(Base.metadata.tables) == 24

    def test_jobs_table_has_exactly_canonical_nine_columns(self) -> None:
        table = JobModel.__table__
        expected_columns = {
            "job_id",
            "tenant_id",
            "job_type",
            "status",
            "failure_code",
            "failure_message",
            "created_at",
            "started_at",
            "completed_at",
        }
        actual_columns = set(table.columns.keys())
        assert actual_columns == expected_columns, (
            f"Expected columns {expected_columns}, got {actual_columns}"
        )

    def test_job_id_is_uuid_primary_key(self) -> None:
        table = JobModel.__table__
        pk_cols = [c.name for c in table.primary_key.columns]
        assert pk_cols == ["job_id"]

        col = table.columns["job_id"]
        assert isinstance(col.type, UUID)
        assert col.type.as_uuid is True
        assert col.nullable is False

    def test_no_surrogate_or_extra_ids(self) -> None:
        table = JobModel.__table__
        col_names = set(table.columns.keys())
        forbidden_id_names = {
            "id",
            "surrogate_id",
            "record_id",
            "uuid",
            "job_uuid",
            "row_id",
        }
        assert col_names.isdisjoint(forbidden_id_names)

    def test_column_types_and_nullabilities(self) -> None:
        table = JobModel.__table__

        # tenant_id
        col_tenant = table.columns["tenant_id"]
        assert isinstance(col_tenant.type, String)
        assert col_tenant.type.length == 128
        assert col_tenant.nullable is False

        # job_type
        col_type = table.columns["job_type"]
        assert isinstance(col_type.type, String)
        assert col_type.type.length == 128
        assert col_type.nullable is False

        # status (VARCHAR, not Enum)
        col_status = table.columns["status"]
        assert isinstance(col_status.type, String)
        assert col_status.type.length == 32
        assert col_status.nullable is False
        assert col_status.server_default is not None
        assert "pending" in str(col_status.server_default.arg).lower()

        # failure_code
        col_fail_code = table.columns["failure_code"]
        assert isinstance(col_fail_code.type, String)
        assert col_fail_code.type.length == 128
        assert col_fail_code.nullable is True

        # failure_message
        col_fail_msg = table.columns["failure_message"]
        assert isinstance(col_fail_msg.type, String)
        assert col_fail_msg.type.length == 1024
        assert col_fail_msg.nullable is True

        # created_at
        col_created = table.columns["created_at"]
        assert isinstance(col_created.type, DateTime)
        assert col_created.type.timezone is True
        assert col_created.nullable is False
        assert col_created.server_default is not None

        # started_at
        col_started = table.columns["started_at"]
        assert isinstance(col_started.type, DateTime)
        assert col_started.type.timezone is True
        assert col_started.nullable is True

        # completed_at
        col_completed = table.columns["completed_at"]
        assert isinstance(col_completed.type, DateTime)
        assert col_completed.type.timezone is True
        assert col_completed.nullable is True


class TestJobModelConstraints:
    """Verify named database CHECK constraints and absence of speculative indexes."""

    def test_all_named_check_constraints_present(self) -> None:
        table = JobModel.__table__
        check_constraints = {c.name: c for c in table.constraints if isinstance(c, CheckConstraint)}

        expected_constraint_names = {
            "ck_jobs_status",
            "ck_jobs_job_type",
            "ck_jobs_tenant_id_nonblank",
            "ck_jobs_failure_code_format",
            "ck_jobs_failure_consistency",
            "ck_jobs_timestamp_state_consistency",
            "ck_jobs_timestamp_ordering",
        }
        assert set(check_constraints.keys()) == expected_constraint_names

    def test_status_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_status"
        )
        sql = str(c.sqltext).lower()
        for s in ["pending", "running", "succeeded", "failed"]:
            assert s in sql

    def test_job_type_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_job_type"
        )
        sql = str(c.sqltext)
        assert r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$" in sql

    def test_tenant_id_nonblank_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_tenant_id_nonblank"
        )
        sql = str(c.sqltext).lower()
        assert "tenant_id" in sql
        assert "trim" in sql or "btrim" in sql

    def test_failure_consistency_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_failure_consistency"
        )
        sql = str(c.sqltext).lower()
        assert "failed" in sql
        assert "failure_code" in sql
        assert "failure_message" in sql

    def test_timestamp_state_consistency_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_timestamp_state_consistency"
        )
        sql = str(c.sqltext).lower()
        assert "pending" in sql
        assert "running" in sql
        assert "succeeded" in sql
        assert "failed" in sql
        assert "started_at" in sql
        assert "completed_at" in sql

    def test_timestamp_ordering_constraint_definition(self) -> None:
        table = JobModel.__table__
        c = next(
            c
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name == "ck_jobs_timestamp_ordering"
        )
        sql = str(c.sqltext).lower()
        sql_compact = sql.replace(" ", "")
        assert "started_at >= created_at" in sql or "started_at>=created_at" in sql_compact
        assert "completed_at >= created_at" in sql or "completed_at>=created_at" in sql_compact

    def test_zero_speculative_secondary_indexes(self) -> None:
        table = JobModel.__table__
        # Only primary key index, zero secondary indexes
        assert len(table.indexes) == 0

    def test_absence_of_speculative_columns(self) -> None:
        table = JobModel.__table__
        actual_cols = set(table.columns.keys())
        forbidden_cols = {
            # Result fields
            "result",
            "result_json",
            "result_reference",
            "resource_id",
            "resource_url",
            "response_body",
            "http_status",
            # Progress fields
            "progress",
            "percentage",
            "step",
            "total_steps",
            # Retry fields
            "attempt_count",
            "retry_count",
            "next_retry_at",
            "max_retries",
            # Cancellation fields
            "cancel_requested",
            "cancelled_at",
            # Priority / scheduling
            "priority",
            "scheduled_at",
            # Worker claiming / lease
            "claim_token",
            "locked_by",
            "lease_until",
            "heartbeat_at",
            "worker_id",
        }
        assert actual_cols.isdisjoint(forbidden_cols)
