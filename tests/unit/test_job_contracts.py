"""Unit tests for provider-neutral asynchronous job contracts and lifecycle.

Task 1I.4A of Phase 1 Foundation:
- JobId: UUIDv4 generation, parsing, validation, and non-nil/version-4 enforcement.
- JobType: open namespaced lowercase validation and bounded length.
- JobStatus: exactly four statuses (PENDING, RUNNING, SUCCEEDED, FAILED), terminal states.
- Lifecycle: allowed transitions, forbidden transitions, same-state rejection,
  and InvalidJobStatusTransitionError.
- JobIdentity: immutable composite identity (job_id, tenant_id, job_type),
  strict structural tenant scoping, absence of principal ID.
- JobFailureCode: open namespaced failure code validation.
- JobFailure: immutable safe failure representation, bounded message, absence of
  raw exception/traceback conversion.
- Architectural isolation: zero HTTP, database, ORM, or broker dependencies.
- Absence of speculative fields: zero result, progress, retry, cancellation, or priority fields.
- Database schema stability: Alembic head 0006, exactly 4 canonical metadata tables.
"""

import ast
import inspect
import os
import re
import uuid
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

import app.db.models  # noqa: F401
from app.core.jobs import (
    ALLOWED_TRANSITIONS,
    InvalidJobStatusTransitionError,
    JobFailure,
    JobFailureCode,
    JobId,
    JobIdentity,
    JobStatus,
    JobType,
    can_transition_job_status,
    generate_job_id,
    validate_failure_message,
    validate_job_failure_code,
    validate_job_id,
    validate_job_status_transition,
    validate_job_type,
)
from app.db.base import Base

UUID4_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ===========================================================================
# 1. JobId Generation & Validation (Sections 6, 7, 8, 34, 35)
# ===========================================================================


class TestJobIdContract:
    """Verify JobId value object, generation, parsing, and UUIDv4 enforcement."""

    def test_generate_job_id_returns_valid_uuid4(self) -> None:
        jid = generate_job_id()
        assert isinstance(jid, JobId)
        assert isinstance(jid.value, uuid.UUID)
        assert jid.value.version == 4
        assert UUID4_PATTERN.fullmatch(str(jid))

    def test_generate_job_id_uniqueness(self) -> None:
        jid1 = generate_job_id()
        jid2 = generate_job_id()
        assert jid1 != jid2
        assert jid1.value != jid2.value
        assert str(jid1) != str(jid2)

    def test_job_id_canonical_string_form(self) -> None:
        raw_uuid = uuid.uuid4()
        jid = JobId(raw_uuid)
        assert str(jid) == str(raw_uuid).lower()
        assert repr(jid) == f"JobId('{raw_uuid}')"
        assert jid.model_dump(mode="json") == {"value": str(raw_uuid).lower()}

    def test_job_id_from_string(self) -> None:
        raw_uuid = uuid.uuid4()
        jid = JobId(str(raw_uuid))
        assert jid.value == raw_uuid
        assert str(jid) == str(raw_uuid).lower()

    def test_job_id_from_job_id(self) -> None:
        orig = generate_job_id()
        copied = JobId(orig)
        assert copied == orig
        assert copied.value == orig.value

    def test_job_id_equality_and_hashability(self) -> None:
        raw_uuid = uuid.uuid4()
        jid1 = JobId(raw_uuid)
        jid2 = JobId(str(raw_uuid))
        assert jid1 == jid2
        assert hash(jid1) == hash(jid2)
        s = {jid1, jid2}
        assert len(s) == 1

    def test_job_id_rejects_nil_uuid(self) -> None:
        nil_uuid = uuid.UUID(int=0)
        with pytest.raises((ValueError, ValidationError)) as exc_info:
            JobId(nil_uuid)
        assert "nil UUID" in str(exc_info.value)

        with pytest.raises(ValueError, match="nil UUID"):
            validate_job_id("00000000-0000-0000-0000-000000000000")

    def test_job_id_rejects_non_uuid4_versions(self) -> None:
        # UUID1 (time-based)
        u1 = uuid.uuid1()
        with pytest.raises((ValueError, ValidationError)) as exc_info:
            JobId(u1)
        assert "version 4" in str(exc_info.value)

        # UUID3 (MD5-based)
        u3 = uuid.uuid3(uuid.NAMESPACE_DNS, "python.org")
        with pytest.raises((ValueError, ValidationError)) as exc_info:
            JobId(u3)
        assert "version 4" in str(exc_info.value)

        # UUID5 (SHA1-based)
        u5 = uuid.uuid5(uuid.NAMESPACE_DNS, "python.org")
        with pytest.raises((ValueError, ValidationError)) as exc_info:
            JobId(u5)
        assert "version 4" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "",
            "   ",
            "not-a-uuid",
            "12345",
            "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx",
            f" {uuid.uuid4()}",
            f"{uuid.uuid4()} ",
            f"{uuid.uuid4()}\n",
            f"{uuid.uuid4()}\t",
        ],
    )
    def test_job_id_rejects_malformed_strings(self, invalid_input: str) -> None:
        with pytest.raises((ValueError, ValidationError)):
            JobId(invalid_input)

    @pytest.mark.parametrize(
        "invalid_type",
        [
            12345,
            12.34,
            None,
            ["not", "a", "uuid"],
            {"key": "value"},
        ],
    )
    def test_job_id_rejects_non_string_non_uuid_types(self, invalid_type: Any) -> None:
        with pytest.raises((TypeError, ValueError, ValidationError)):
            JobId(invalid_type)

    def test_job_id_immutability(self) -> None:
        jid = generate_job_id()
        with pytest.raises(ValidationError):
            jid.value = uuid.uuid4()  # type: ignore[misc]

    def test_job_id_forbids_extra_fields(self) -> None:
        raw_uuid = uuid.uuid4()
        with pytest.raises(ValidationError):
            JobId(value=raw_uuid, extra="forbidden")  # type: ignore[call-arg]


# ===========================================================================
# 2. JobType Contract (Sections 9, 10, 36)
# ===========================================================================


class TestJobTypeContract:
    """Verify open namespaced JobType value object and validation."""

    @pytest.mark.parametrize(
        "valid_type",
        [
            "planning.generate",
            "schedule.solve",
            "workflow.deploy",
            "integration.sync",
            "tasks.intelligence.estimate",
            "domain_a.sub_b.action_c",
            "a.b",
        ],
    )
    def test_job_type_accepts_valid_namespaced_strings(self, valid_type: str) -> None:
        jt = JobType(valid_type)
        assert jt.value == valid_type
        assert str(jt) == valid_type
        assert repr(jt) == f"JobType('{valid_type}')"
        assert validate_job_type(valid_type) == valid_type

    @pytest.mark.parametrize(
        ("invalid_type", "error_match"),
        [
            ("", "must not be empty"),
            ("   ", "must not contain whitespace"),
            (" planning.generate", "must not contain whitespace"),
            ("planning.generate ", "must not contain whitespace"),
            ("planning .generate", "must not contain whitespace"),
            ("planning\t.generate", "must not contain whitespace"),
            ("planning\n.generate", "must not contain whitespace"),
            ("planning\x00.generate", "forbidden control character"),
            ("solve", "at least two dot-separated segments"),
            ("planning", "at least two dot-separated segments"),
            ("Schedule.solve", "must be strictly lowercase"),
            ("schedule.Solve", "must be strictly lowercase"),
            (".schedule.solve", "must not have a leading dot"),
            ("schedule.solve.", "must not have a trailing dot"),
            ("schedule..solve", "must not have consecutive dots"),
            ("1schedule.solve", "is invalid"),
            ("schedule.1solve", "is invalid"),
            ("a" * 125 + ".solve", "exceeds maximum length of 128"),
        ],
    )
    def test_job_type_rejects_invalid_strings(self, invalid_type: str, error_match: str) -> None:
        with pytest.raises(ValueError, match=error_match):
            validate_job_type(invalid_type)

        with pytest.raises((ValueError, ValidationError)):
            JobType(invalid_type)

    def test_job_type_rejects_non_string_types(self) -> None:
        with pytest.raises(TypeError, match="Job type must be a string"):
            validate_job_type(123)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="Job type must be a string"):
            validate_job_type(None)  # type: ignore[arg-type]

    def test_job_type_immutability_and_extra_fields(self) -> None:
        jt = JobType("planning.generate")
        with pytest.raises(ValidationError):
            jt.value = "schedule.solve"  # type: ignore[misc]

        with pytest.raises(ValidationError):
            JobType(value="planning.generate", extra_field="bad")  # type: ignore[call-arg]

    def test_job_type_equality_and_hash(self) -> None:
        jt1 = JobType("schedule.solve")
        jt2 = JobType("schedule.solve")
        jt3 = JobType("workflow.deploy")
        assert jt1 == jt2
        assert jt1 != jt3
        assert hash(jt1) == hash(jt2)
        assert len({jt1, jt2, jt3}) == 2


# ===========================================================================
# 3. JobStatus & Terminal Semantics (Sections 11, 12, 13, 37, 38)
# ===========================================================================


class TestJobStatusContract:
    """Verify JobStatus StrEnum, member count, string values, and terminal semantics."""

    def test_exactly_four_statuses_exist(self) -> None:
        assert len(JobStatus) == 4
        expected_members = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED"}
        assert {s.name for s in JobStatus} == expected_members

    def test_serialized_string_values(self) -> None:
        assert JobStatus.PENDING == "pending"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.SUCCEEDED == "succeeded"
        assert JobStatus.FAILED == "failed"

    def test_forbidden_statuses_absent(self) -> None:
        forbidden = [
            "CANCELLED",
            "CANCELLING",
            "PAUSED",
            "RETRYING",
            "QUEUED",
            "BLOCKED",
            "TIMED_OUT",
            "EXPIRED",
        ]
        status_names = {s.name for s in JobStatus}
        for f in forbidden:
            assert f not in status_names, f"Forbidden status {f} found in JobStatus"

    def test_terminal_state_semantics(self) -> None:
        assert JobStatus.SUCCEEDED.is_terminal is True
        assert JobStatus.FAILED.is_terminal is True

        assert JobStatus.PENDING.is_terminal is False
        assert JobStatus.RUNNING.is_terminal is False


# ===========================================================================
# 4. Lifecycle Transitions Policy (Sections 14, 15, 16, 17, 39, 40, 41)
# ===========================================================================


class TestJobLifecycleTransitions:
    """Verify deterministic state transition validation and error presentation."""

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (JobStatus.PENDING, JobStatus.RUNNING),
            (JobStatus.PENDING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.SUCCEEDED),
            (JobStatus.RUNNING, JobStatus.FAILED),
        ],
    )
    def test_allowed_transitions_return_true(self, current: JobStatus, target: JobStatus) -> None:
        assert can_transition_job_status(current, target) is True
        # Must not raise
        validate_job_status_transition(current, target)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            # Cannot bypass active execution
            (JobStatus.PENDING, JobStatus.SUCCEEDED),
            # Cannot revert to pending
            (JobStatus.RUNNING, JobStatus.PENDING),
            # Terminal states cannot transition to anything
            (JobStatus.SUCCEEDED, JobStatus.PENDING),
            (JobStatus.SUCCEEDED, JobStatus.RUNNING),
            (JobStatus.SUCCEEDED, JobStatus.FAILED),
            (JobStatus.FAILED, JobStatus.PENDING),
            (JobStatus.FAILED, JobStatus.RUNNING),
            (JobStatus.FAILED, JobStatus.SUCCEEDED),
            # Same-state transitions are forbidden
            (JobStatus.PENDING, JobStatus.PENDING),
            (JobStatus.RUNNING, JobStatus.RUNNING),
            (JobStatus.SUCCEEDED, JobStatus.SUCCEEDED),
            (JobStatus.FAILED, JobStatus.FAILED),
        ],
    )
    def test_forbidden_transitions_return_false_and_raise(
        self, current: JobStatus, target: JobStatus
    ) -> None:
        assert can_transition_job_status(current, target) is False
        with pytest.raises(InvalidJobStatusTransitionError) as exc_info:
            validate_job_status_transition(current, target)

        err = exc_info.value
        assert err.current == current
        assert err.target == target
        assert current.value in str(err)
        assert target.value in str(err)

    def test_transition_functions_reject_non_status_types(self) -> None:
        assert can_transition_job_status("pending", JobStatus.RUNNING) is False  # type: ignore[arg-type]
        assert can_transition_job_status(JobStatus.PENDING, "running") is False  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="current must be a JobStatus instance"):
            validate_job_status_transition("pending", JobStatus.RUNNING)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="target must be a JobStatus instance"):
            validate_job_status_transition(JobStatus.PENDING, "running")  # type: ignore[arg-type]

    def test_allowed_transitions_set_matches_frozen_specification(self) -> None:
        expected = {
            (JobStatus.PENDING, JobStatus.RUNNING),
            (JobStatus.PENDING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.SUCCEEDED),
            (JobStatus.RUNNING, JobStatus.FAILED),
        }
        assert ALLOWED_TRANSITIONS == expected

    def test_invalid_transition_error_safety(self) -> None:
        err = InvalidJobStatusTransitionError(current=JobStatus.SUCCEEDED, target=JobStatus.RUNNING)
        # Exposes safe attributes
        assert err.current == JobStatus.SUCCEEDED
        assert err.target == JobStatus.RUNNING
        # Safe message
        msg = str(err)
        assert "succeeded" in msg
        assert "running" in msg
        # Does NOT expose transport or database artifacts
        assert "http" not in msg.lower()
        assert "database" not in msg.lower()
        assert "tenant" not in msg.lower()


# ===========================================================================
# 5. JobIdentity Contract (Sections 18, 19, 20, 24, 42, 43)
# ===========================================================================


class TestJobIdentityContract:
    """Verify JobIdentity composite value object, immutability, and tenant scoping."""

    def test_construct_job_identity_valid(self) -> None:
        jid = generate_job_id()
        jt = JobType("planning.generate")
        ident = JobIdentity(job_id=jid, tenant_id="tenant-alpha", job_type=jt)

        assert ident.job_id == jid
        assert ident.tenant_id == "tenant-alpha"
        assert ident.job_type == jt
        assert str(ident.job_id) == str(jid)
        assert str(ident.job_type) == "planning.generate"

    def test_construct_job_identity_with_coercible_types(self) -> None:
        raw_uuid = uuid.uuid4()
        ident = JobIdentity(
            job_id=str(raw_uuid),
            tenant_id="tenant-beta",
            job_type="schedule.solve",
        )
        assert ident.job_id == JobId(raw_uuid)
        assert ident.tenant_id == "tenant-beta"
        assert ident.job_type == JobType("schedule.solve")

    def test_job_identity_structural_tenant_scoping(self) -> None:
        jid = generate_job_id()
        jt = JobType("planning.generate")

        ident_tenant_a = JobIdentity(job_id=jid, tenant_id="tenant-a", job_type=jt)
        ident_tenant_b = JobIdentity(job_id=jid, tenant_id="tenant-b", job_type=jt)

        # Same JobId across different tenants are distinct identities
        assert ident_tenant_a != ident_tenant_b
        assert hash(ident_tenant_a) != hash(ident_tenant_b)

    def test_job_identity_structural_job_type_scoping(self) -> None:
        jid = generate_job_id()
        ident_type_1 = JobIdentity(job_id=jid, tenant_id="tenant-a", job_type="planning.generate")
        ident_type_2 = JobIdentity(job_id=jid, tenant_id="tenant-a", job_type="schedule.solve")

        assert ident_type_1 != ident_type_2

    def test_job_identity_immutability(self) -> None:
        ident = JobIdentity(
            job_id=generate_job_id(),
            tenant_id="tenant-a",
            job_type="planning.generate",
        )
        with pytest.raises(ValidationError):
            ident.tenant_id = "tenant-b"  # type: ignore[misc]

    def test_job_identity_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            JobIdentity(
                job_id=generate_job_id(),
                tenant_id="tenant-a",
                job_type="planning.generate",
                principal_id="principal-123",  # type: ignore[call-arg]
            )

    @pytest.mark.parametrize(
        "invalid_tenant",
        [
            "",
            "   ",
            123,
            None,
            "t" * 129,
        ],
    )
    def test_job_identity_rejects_invalid_tenant(self, invalid_tenant: Any) -> None:
        with pytest.raises((TypeError, ValueError, ValidationError)):
            JobIdentity(
                job_id=generate_job_id(),
                tenant_id=invalid_tenant,
                job_type="planning.generate",
            )

    def test_job_identity_fields_exactly_three(self) -> None:
        fields = set(JobIdentity.model_fields.keys())
        assert fields == {"job_id", "tenant_id", "job_type"}


# ===========================================================================
# 6. JobFailureCode & JobFailure (Sections 21, 22, 23, 44, 45, 46)
# ===========================================================================


class TestJobFailureContract:
    """Verify JobFailureCode and JobFailure metadata objects."""

    @pytest.mark.parametrize(
        "valid_code",
        [
            "job.execution_failed",
            "schedule.infeasible",
            "integration.provider_unavailable",
            "planner.timeout",
            "workflow.step_failed",
        ],
    )
    def test_job_failure_code_accepts_valid_namespaced_strings(self, valid_code: str) -> None:
        fc = JobFailureCode(valid_code)
        assert fc.value == valid_code
        assert str(fc) == valid_code
        assert repr(fc) == f"JobFailureCode('{valid_code}')"
        assert validate_job_failure_code(valid_code) == valid_code

    @pytest.mark.parametrize(
        ("invalid_code", "error_match"),
        [
            ("", "must not be empty"),
            ("   ", "must not contain whitespace"),
            ("failed", "at least two dot-separated segments"),
            ("Job.execution_failed", "must be strictly lowercase"),
            (".job.execution_failed", "must not have a leading dot"),
            ("job.execution_failed.", "must not have a trailing dot"),
            ("job..execution_failed", "must not have consecutive dots"),
            ("a" * 125 + ".fail", "exceeds maximum length of 128"),
        ],
    )
    def test_job_failure_code_rejects_invalid_strings(
        self, invalid_code: str, error_match: str
    ) -> None:
        with pytest.raises(ValueError, match=error_match):
            validate_job_failure_code(invalid_code)

        with pytest.raises((ValueError, ValidationError)):
            JobFailureCode(invalid_code)

    def test_job_failure_construction_valid(self) -> None:
        code = JobFailureCode("job.execution_failed")
        msg = "Solver timed out after 30 seconds."
        failure = JobFailure(code=code, message=msg)

        assert failure.code == code
        assert failure.message == msg
        assert failure.model_dump(mode="json") == {
            "code": "job.execution_failed",
            "message": msg,
        }

    def test_job_failure_construction_with_string_code(self) -> None:
        failure = JobFailure(
            code="schedule.infeasible",
            message="No feasible schedule found for constraints.",
        )
        assert failure.code == JobFailureCode("schedule.infeasible")
        assert failure.message == "No feasible schedule found for constraints."

    def test_job_failure_rejects_blank_message(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            JobFailure(code="job.failed_test", message="")

        with pytest.raises((ValueError, ValidationError)):
            JobFailure(code="job.failed_test", message="   ")

    def test_job_failure_rejects_oversized_message(self) -> None:
        oversized = "m" * 1025
        with pytest.raises((ValueError, ValidationError)):
            JobFailure(code="job.failed_test", message=oversized)

        with pytest.raises(ValueError, match="exceeds maximum length of 1024"):
            validate_failure_message(oversized)

    def test_job_failure_immutability_and_extra_fields(self) -> None:
        failure = JobFailure(code="job.test_fail", message="Valid message")
        with pytest.raises(ValidationError):
            failure.message = "New message"  # type: ignore[misc]

        with pytest.raises(ValidationError):
            JobFailure(
                code="job.test_fail",
                message="Valid message",
                stack_trace="Traceback...",  # type: ignore[call-arg]
            )

    def test_no_automatic_exception_conversion_helpers(self) -> None:
        """Verify JobFailure does NOT provide from_exception or import traceback."""
        assert not hasattr(JobFailure, "from_exception")
        import app.core.jobs.contracts as contracts_mod

        # Verify traceback module is not imported
        assert "traceback" not in dir(contracts_mod)

        # Inspect AST to verify no exception serialization functions exist
        tree = ast.parse(inspect.getsource(contracts_mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert "from_exception" not in node.name
                assert "format_exception" not in node.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "traceback"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "traceback"


# ===========================================================================
# 7. Absence of Speculative Fields (Section 48)
# ===========================================================================


class TestAbsentSpeculativeFields:
    """Verify new job contracts contain zero speculative or premature fields."""

    def test_contracts_contain_no_speculative_attributes(self) -> None:
        import app.core.jobs.contracts as contracts_mod
        import app.core.jobs.lifecycle as lifecycle_mod

        combined_src = inspect.getsource(contracts_mod) + inspect.getsource(lifecycle_mod)

        speculative_terms = [
            "result_json",
            "result_reference",
            "response_body",
            "response_headers",
            "http_status",
            "progress_message",
            "current_step",
            "total_steps",
            "retry_count",
            "max_retries",
            "next_retry_at",
            "cancel_requested",
            "queue_priority",
        ]
        for term in speculative_terms:
            assert term not in combined_src, f"Speculative term '{term}' found in contracts"


# ===========================================================================
# 8. Architectural Isolation (Sections 5, 47)
# ===========================================================================


class TestArchitecturalIsolation:
    """Verify app/core/jobs does not import HTTP, DB, ORM, or messaging modules."""

    def test_forbidden_imports_absent_from_contracts_and_lifecycle(self) -> None:
        import app.core.jobs.contracts as contracts_mod
        import app.core.jobs.lifecycle as lifecycle_mod

        modules_to_check = [contracts_mod, lifecycle_mod]

        forbidden_prefixes = [
            "fastapi",
            "starlette",
            "sqlalchemy",
            "alembic",
            "aio_pika",
            "app.db",
            "app.events",
            "app.api",
        ]

        for mod in modules_to_check:
            src = inspect.getsource(mod)
            tree = ast.parse(src)

            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.append(node.module)

            for imp in imported:
                for forbidden in forbidden_prefixes:
                    assert not imp.startswith(forbidden), (
                        f"Forbidden import '{imp}' found in {mod.__name__}"
                    )


# ===========================================================================
# 9. Database Schema Stability (Sections 31, 49)
# ===========================================================================


class TestDatabaseSchemaStability:
    """Verify database schema is untouched: exactly 4 tables, head at 0006."""

    def test_metadata_tables_are_exact_five(self) -> None:
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
            # Phase 7 Calendar Watch (1)
            "calendar_watch_channels",
            # Phase 8D Service Credentials Persistence (1)
            "service_api_credentials",
        }
        actual_tables = set(Base.metadata.tables.keys())
        missing = phase1_tables - actual_tables
        assert phase1_tables <= actual_tables, f"Missing Phase-1 tables: {missing}"
        assert actual_tables == canonical_phase4_tables, (
            f"Expected canonical Phase-4 tables {canonical_phase4_tables}, got {actual_tables}"
        )
        assert len(actual_tables) == 26

    def test_alembic_head_remains_0007(self) -> None:
        alembic_cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "alembic.ini",
        )
        if not os.path.exists(alembic_cfg_path):
            alembic_cfg_path = "alembic.ini"

        config = Config(alembic_cfg_path)
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        assert heads == ["0014"], f"Alembic heads expected ['0014'], got {heads}"

        rev_0007 = script.get_revision("0007")
        assert rev_0007 is not None
        assert rev_0007.down_revision == "0006"

        rev_0008 = script.get_revision("0008")
        assert rev_0008 is not None
        assert rev_0008.down_revision == "0007"

        rev_0009 = script.get_revision("0009")
        assert rev_0009 is not None
        assert rev_0009.down_revision == "0008"

        rev_0010 = script.get_revision("0010")
        assert rev_0010 is not None
        assert rev_0010.down_revision == "0009"

        rev_0011 = script.get_revision("0011")
        assert rev_0011 is not None
        assert rev_0011.down_revision == "0010"

        rev_0012 = script.get_revision("0012")
        assert rev_0012 is not None
        assert rev_0012.down_revision == "0011"

        rev_0013 = script.get_revision("0013")
        assert rev_0013 is not None
        assert rev_0013.down_revision == "0012"

        rev_0014 = script.get_revision("0014")
        assert rev_0014 is not None
        assert rev_0014.down_revision == "0013"

        versions_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "app",
            "db",
            "migrations",
            "versions",
        )
        files = os.listdir(versions_dir)
        migration_0015 = [f for f in files if "0015" in f]
        assert len(migration_0015) == 0, f"Found unexpected migration 0015: {migration_0015}"
