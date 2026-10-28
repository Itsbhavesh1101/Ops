"""Unit tests for idempotency identity, key-digest, and request-fingerprint contracts.

Part of Phase 1 Foundation Task 1H.1.
Validates:
- OperationName: namespaced lowercase format, bounds, segment rules, no silent lowercasing.
- validate_raw_idempotency_key: opacity, non-empty, whitespace rejection,
  control character rejection, length bounds, no UUID/ULID format restriction.
- digest_idempotency_key: deterministic SHA-256, 64-char lowercase hex, no random salt.
- IdempotencyKeyDigest: validation of 64-char lowercase hex, rejection of errors.
- RequestFingerprint: validation of 64-char lowercase hex, rejection of malformed values.
- fingerprint_bytes: deterministic SHA-256 on exact bytes, valid empty bytes (b"") support.
- IdempotencyIdentity: immutable composite identity (tenant_id + operation_name + key_digest).
- Raw key exclusion: raw client key is never retained in identity repr, str, or model_dump.
- Principal exclusion: identity does not contain principal ID, email, role, or permissions.
- Structural equality and inequality across tenant, operation, and digest dimensions.
- Architectural isolation: zero imports of FastAPI, Starlette, SQLAlchemy, or RabbitMQ.
- Database stability: Base.metadata remains {audit_events, outbox_events, inbox_messages};
  Alembic head remains 0005.
"""

import re
from inspect import getsource
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.idempotency import (
    MAX_RAW_KEY_LENGTH,
    IdempotencyIdentity,
    IdempotencyKeyDigest,
    OperationName,
    RequestFingerprint,
    create_idempotency_identity,
    digest_idempotency_key,
    fingerprint_bytes,
    validate_operation_name,
    validate_raw_idempotency_key,
)
from app.db.base import Base

VALID_HEX_64 = "a" * 64
SAMPLE_RAW_KEY = "client-mutation-key-12345"


# ==============================================================================
# 1. Operation Name Contract Tests
# ==============================================================================


def test_operation_name_valid_formats() -> None:
    """Verify valid namespaced lowercase operation identifiers are accepted."""
    valid_names = [
        "tasks.create",
        "tasks.delete",
        "workflow.start",
        "workflow.task.assign",
        "schedule.publish",
        "billing.invoice.finalize",
        "tenant_admin.users.invite",
        "a.b",
        "a_b.c_d",
        "op.sub1.sub2.sub3",
    ]
    for name in valid_names:
        assert validate_operation_name(name) == name
        op = OperationName(name)
        assert str(op) == name
        assert repr(op) == f"OperationName('{name}')"
        assert op.value == name


def test_operation_name_rejection_rules() -> None:
    """Verify malformed or invalid operation names are rejected without silent normalization."""
    # Empty and whitespace
    with pytest.raises(ValueError, match="Operation name must not be empty"):
        validate_operation_name("")

    with pytest.raises(ValueError, match="must not contain whitespace"):
        validate_operation_name("   ")

    with pytest.raises(ValueError, match="must not contain whitespace"):
        validate_operation_name("tasks. create")

    with pytest.raises(ValueError, match="must not contain whitespace"):
        validate_operation_name("tasks.create ")

    with pytest.raises(ValueError, match="must not contain whitespace"):
        validate_operation_name(" tasks.create")

    # Uppercase letters (no silent lowercasing)
    with pytest.raises(ValueError, match="must be strictly lowercase"):
        validate_operation_name("Tasks.create")

    with pytest.raises(ValueError, match="must be strictly lowercase"):
        validate_operation_name("tasks.Create")

    # Single segment (must have at least two dot-separated segments)
    with pytest.raises(ValueError, match="at least two dot-separated segments"):
        validate_operation_name("create")

    with pytest.raises(ValueError, match="at least two dot-separated segments"):
        validate_operation_name("tasks")

    # Leading, trailing, and consecutive dots
    with pytest.raises(ValueError, match="must not have a leading dot"):
        validate_operation_name(".tasks.create")

    with pytest.raises(ValueError, match="must not have a trailing dot"):
        validate_operation_name("tasks.create.")

    with pytest.raises(ValueError, match="must not have consecutive dots"):
        validate_operation_name("tasks..create")

    # Oversized input (> 128 characters)
    oversized = "a" * 125 + ".create"
    assert len(oversized) > 128
    with pytest.raises(ValueError, match="exceeds maximum length of 128 characters"):
        validate_operation_name(oversized)

    # Segment starting with non-letter
    with pytest.raises(ValueError, match="is invalid"):
        validate_operation_name("1tasks.create")

    with pytest.raises(ValueError, match="is invalid"):
        validate_operation_name("tasks.1create")

    # Non-string input
    with pytest.raises(TypeError, match="Operation name must be a string"):
        validate_operation_name(123)

    with pytest.raises(TypeError, match="Operation name must be a string"):
        validate_operation_name(None)


def test_operation_name_immutability() -> None:
    """Verify OperationName is frozen and forbids modification or extra fields."""
    op = OperationName("tasks.create")
    with pytest.raises(ValidationError):
        op.value = "workflow.start"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        OperationName(value="tasks.create", extra_field=True)  # type: ignore[call-arg]


# ==============================================================================
# 2. Raw Idempotency Key Validation Tests
# ==============================================================================


def test_raw_idempotency_key_valid_formats() -> None:
    """Verify opaque raw idempotency keys are accepted without format restrictions."""
    valid_keys = [
        str(uuid4()),  # UUID format
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",  # ULID format
        "client-req-987654321",  # custom hyphenated
        "token_123_abc",  # alphanumeric with underscore
        "SAFE_KEY:ABC-123.XYZ",  # printable ASCII
        "k" * MAX_RAW_KEY_LENGTH,  # max length
    ]
    for key in valid_keys:
        assert validate_raw_idempotency_key(key) == key


def test_raw_idempotency_key_rejections() -> None:
    """Verify empty, whitespace-padded, oversized, or control-character keys are rejected."""
    # Empty
    with pytest.raises(ValueError, match="Raw idempotency key must not be empty"):
        validate_raw_idempotency_key("")

    # Leading / trailing whitespace ambiguity
    with pytest.raises(ValueError, match="must not contain leading or trailing whitespace"):
        validate_raw_idempotency_key("   ")

    with pytest.raises(ValueError, match="must not contain leading or trailing whitespace"):
        validate_raw_idempotency_key(" key-123")

    with pytest.raises(ValueError, match="must not contain leading or trailing whitespace"):
        validate_raw_idempotency_key("key-123 ")

    with pytest.raises(
        ValueError,
        match=r"(must not contain leading or trailing whitespace|forbidden control character)",
    ):
        validate_raw_idempotency_key("key-123\t")

    with pytest.raises(
        ValueError,
        match=r"(must not contain leading or trailing whitespace|forbidden control character)",
    ):
        validate_raw_idempotency_key("key-123\n")

    # Length bounds
    oversized = "k" * (MAX_RAW_KEY_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds maximum length of 255 characters"):
        validate_raw_idempotency_key(oversized)

    # Control characters
    with pytest.raises(ValueError, match="forbidden control character"):
        validate_raw_idempotency_key("key\x00abc")

    with pytest.raises(ValueError, match="forbidden control character"):
        validate_raw_idempotency_key("key\x1f")

    with pytest.raises(ValueError, match="forbidden control character"):
        validate_raw_idempotency_key("key\x7f")

    # Non-string types
    with pytest.raises(TypeError, match="Raw idempotency key must be a string"):
        validate_raw_idempotency_key(12345)

    with pytest.raises(TypeError, match="Raw idempotency key must be a string"):
        validate_raw_idempotency_key(b"byte-key")

    with pytest.raises(TypeError, match="Raw idempotency key must be a string"):
        validate_raw_idempotency_key(None)


# ==============================================================================
# 3. Key Digest & SHA-256 Tests
# ==============================================================================


def test_digest_idempotency_key_determinism() -> None:
    """Verify SHA-256 key digest is deterministic and returns 64-char lowercase hex."""
    key = "user-mutation-42"
    digest1 = digest_idempotency_key(key)
    digest2 = digest_idempotency_key(key)

    assert isinstance(digest1, IdempotencyKeyDigest)
    assert digest1 == digest2
    assert len(digest1.value) == 64
    assert str(digest1) == digest1.value
    assert repr(digest1) == f"IdempotencyKeyDigest('{digest1.value}')"
    assert re.fullmatch(r"^[0-9a-f]{64}$", digest1.value)


def test_digest_idempotency_key_distinct_inputs() -> None:
    """Verify different raw keys produce distinct SHA-256 digests."""
    digest_a = digest_idempotency_key("client-key-aaa")
    digest_b = digest_idempotency_key("client-key-bbb")
    assert digest_a != digest_b
    assert digest_a.value != digest_b.value


def test_digest_idempotency_key_validates_raw_key_first() -> None:
    """Verify digest helper rejects invalid raw keys prior to hashing."""
    with pytest.raises(ValueError, match="Raw idempotency key must not be empty"):
        digest_idempotency_key("")

    with pytest.raises(ValueError, match="must not contain leading or trailing whitespace"):
        digest_idempotency_key(" key ")


def test_idempotency_key_digest_validation() -> None:
    """Verify IdempotencyKeyDigest strictly validates 64-char lowercase hex."""
    # Valid
    digest = IdempotencyKeyDigest(VALID_HEX_64)
    assert digest.value == VALID_HEX_64

    # Length errors
    with pytest.raises(ValueError, match="must be exactly 64 characters"):
        IdempotencyKeyDigest("a" * 63)

    with pytest.raises(ValueError, match="must be exactly 64 characters"):
        IdempotencyKeyDigest("a" * 65)

    # Uppercase hex (rejected without silent lowercasing)
    uppercase_hex = "A" * 64
    with pytest.raises(ValueError, match="must be lowercase hexadecimal only"):
        IdempotencyKeyDigest(uppercase_hex)

    # Non-hex characters
    non_hex = "z" * 64
    with pytest.raises(ValueError, match="contains non-hexadecimal characters"):
        IdempotencyKeyDigest(non_hex)

    # Non-string input
    with pytest.raises((TypeError, ValidationError)):
        IdempotencyKeyDigest(123)  # type: ignore[arg-type]


# ==============================================================================
# 4. Request Fingerprint Tests
# ==============================================================================


def test_fingerprint_bytes_determinism() -> None:
    """Verify fingerprint_bytes computes deterministic SHA-256 lowercase hex."""
    data = b'{"subject_id": "sub-123", "amount": 100}'
    fp1 = fingerprint_bytes(data)
    fp2 = fingerprint_bytes(data)

    assert isinstance(fp1, RequestFingerprint)
    assert fp1 == fp2
    assert len(fp1.value) == 64
    assert str(fp1) == fp1.value
    assert repr(fp1) == f"RequestFingerprint('{fp1.value}')"
    assert re.fullmatch(r"^[0-9a-f]{64}$", fp1.value)


def test_fingerprint_bytes_distinct_inputs() -> None:
    """Verify differing payload bytes produce distinct fingerprints."""
    fp1 = fingerprint_bytes(b"payload-one")
    fp2 = fingerprint_bytes(b"payload-two")
    assert fp1 != fp2


def test_fingerprint_bytes_empty_payload() -> None:
    """Verify empty bytes (b'') produce valid canonical SHA-256 fingerprint."""
    fp_empty = fingerprint_bytes(b"")
    # Canonical SHA-256 of empty byte string
    expected_empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert fp_empty.value == expected_empty_sha256
    assert isinstance(fp_empty, RequestFingerprint)


def test_fingerprint_bytes_rejects_non_bytes() -> None:
    """Verify fingerprint_bytes rejects non-bytes inputs (strings, dicts)."""
    with pytest.raises(TypeError, match="Payload data must be bytes"):
        fingerprint_bytes("string-payload")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Payload data must be bytes"):
        fingerprint_bytes({"key": "value"})  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Payload data must be bytes"):
        fingerprint_bytes(None)  # type: ignore[arg-type]


def test_request_fingerprint_validation() -> None:
    """Verify RequestFingerprint validates 64-char lowercase hex."""
    # Valid
    fp = RequestFingerprint(VALID_HEX_64)
    assert fp.value == VALID_HEX_64

    # Length error
    with pytest.raises(ValueError, match="must be exactly 64 characters"):
        RequestFingerprint("b" * 60)

    # Uppercase error
    with pytest.raises(ValueError, match="must be lowercase hexadecimal only"):
        RequestFingerprint("B" * 64)

    # Non-hex error
    with pytest.raises(ValueError, match="contains non-hexadecimal characters"):
        RequestFingerprint("g" * 64)


# ==============================================================================
# 5. Idempotency Identity Tests
# ==============================================================================


def test_idempotency_identity_construction_and_immutability() -> None:
    """Verify IdempotencyIdentity construction with validation and immutability."""
    digest = IdempotencyKeyDigest(VALID_HEX_64)
    identity = IdempotencyIdentity(
        tenant_id="tenant-alpha",
        operation_name="tasks.create",
        key_digest=digest,
    )

    assert identity.tenant_id == "tenant-alpha"
    assert identity.operation_name == "tasks.create"
    assert identity.key_digest == digest

    # Immutability
    with pytest.raises(ValidationError):
        identity.tenant_id = "tenant-beta"  # type: ignore[misc]

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        IdempotencyIdentity(
            tenant_id="tenant-alpha",
            operation_name="tasks.create",
            key_digest=digest,
            extra_field="disallowed",  # type: ignore[call-arg]
        )


def test_idempotency_identity_coercion_and_string_digest_acceptance() -> None:
    """Verify IdempotencyIdentity accepts valid hex string and OperationName object."""
    op_name = OperationName("workflow.start")
    identity = IdempotencyIdentity(
        tenant_id="tenant-bravo",
        operation_name=op_name,  # type: ignore[arg-type]
        key_digest=VALID_HEX_64,  # type: ignore[arg-type]
    )

    assert identity.operation_name == "workflow.start"
    assert isinstance(identity.key_digest, IdempotencyKeyDigest)
    assert identity.key_digest.value == VALID_HEX_64


def test_idempotency_identity_factory_methods() -> None:
    """Verify IdempotencyIdentity.create and create_idempotency_identity factories."""
    identity1 = IdempotencyIdentity.create(
        tenant_id="tenant-charlie",
        operation_name="schedule.publish",
        raw_key=SAMPLE_RAW_KEY,
    )
    identity2 = create_idempotency_identity(
        tenant_id="tenant-charlie",
        operation_name="schedule.publish",
        raw_key=SAMPLE_RAW_KEY,
    )

    assert identity1 == identity2
    assert identity1.tenant_id == "tenant-charlie"
    assert identity1.operation_name == "schedule.publish"
    expected_digest = digest_idempotency_key(SAMPLE_RAW_KEY)
    assert identity1.key_digest == expected_digest


def test_no_raw_key_retention_in_identity() -> None:
    """Verify raw client key is never stored, retained, or leaked in identity."""
    synthetic_raw_key = "super-secret-client-token-998877"
    identity = create_idempotency_identity(
        tenant_id="tenant-delta",
        operation_name="tasks.create",
        raw_key=synthetic_raw_key,
    )

    # Raw key absent from attributes
    assert not hasattr(identity, "raw_key")

    # Raw key absent from str, repr, and model_dump
    assert synthetic_raw_key not in repr(identity)
    assert synthetic_raw_key not in str(identity)

    dumped = identity.model_dump()
    assert synthetic_raw_key not in str(dumped)
    assert set(dumped.keys()) == {"tenant_id", "operation_name", "key_digest"}
    assert dumped["key_digest"] == identity.key_digest.value


def test_principal_exclusion_from_identity() -> None:
    """Verify canonical identity excludes principal ID, email, role, and permissions."""
    fields = set(IdempotencyIdentity.model_fields.keys())
    assert fields == {"tenant_id", "operation_name", "key_digest"}

    for forbidden_field in [
        "principal_id",
        "user_id",
        "email",
        "role",
        "roles",
        "permission",
        "permissions",
        "claims",
        "google_hd",
        "request_fingerprint",
        "fingerprint",
        "status",
        "response",
    ]:
        assert forbidden_field not in fields


def test_structural_identity_comparisons() -> None:
    """Verify structural equality and inequality across all 3 identity dimensions."""
    digest_1 = digest_idempotency_key("key-1")
    digest_2 = digest_idempotency_key("key-2")

    base = IdempotencyIdentity(
        tenant_id="tenant-1",
        operation_name="tasks.create",
        key_digest=digest_1,
    )
    same = IdempotencyIdentity(
        tenant_id="tenant-1",
        operation_name="tasks.create",
        key_digest=digest_1,
    )
    diff_tenant = IdempotencyIdentity(
        tenant_id="tenant-2",
        operation_name="tasks.create",
        key_digest=digest_1,
    )
    diff_op = IdempotencyIdentity(
        tenant_id="tenant-1",
        operation_name="tasks.delete",
        key_digest=digest_1,
    )
    diff_digest = IdempotencyIdentity(
        tenant_id="tenant-1",
        operation_name="tasks.create",
        key_digest=digest_2,
    )

    # Reflexive & symmetrical equality
    assert base == same
    assert hash(base) == hash(same)

    # Distinct tenant produces distinct identity
    assert base != diff_tenant

    # Distinct operation produces distinct identity
    assert base != diff_op

    # Distinct digest produces distinct identity
    assert base != diff_digest


def test_identity_tenant_id_validation() -> None:
    """Verify tenant_id validation in IdempotencyIdentity."""
    digest = IdempotencyKeyDigest(VALID_HEX_64)

    # Empty
    with pytest.raises(ValueError, match="tenant_id must not be empty"):
        IdempotencyIdentity(tenant_id="", operation_name="tasks.create", key_digest=digest)

    # Whitespace only
    with pytest.raises(ValueError, match="tenant_id must not be empty"):
        IdempotencyIdentity(tenant_id="   ", operation_name="tasks.create", key_digest=digest)

    # Oversized (> 128 chars)
    with pytest.raises(ValueError, match="exceeds maximum length of 128 characters"):
        IdempotencyIdentity(tenant_id="t" * 129, operation_name="tasks.create", key_digest=digest)

    # Non-string
    with pytest.raises((TypeError, ValidationError)):
        IdempotencyIdentity(
            tenant_id=123,
            operation_name="tasks.create",
            key_digest=digest,  # type: ignore[arg-type]
        )


# ==============================================================================
# 6. Architectural Isolation & Stability Tests
# ==============================================================================


def test_architectural_isolation() -> None:
    """Verify idempotency package imports zero transport, HTTP, database, or broker libraries."""
    import app.core.idempotency.contracts as contracts_mod
    import app.core.idempotency.hashing as hashing_mod

    contracts_src = getsource(contracts_mod)
    hashing_src = getsource(hashing_mod)
    all_src = contracts_src + "\n" + hashing_src

    forbidden_tokens = [
        "fastapi",
        "FastAPI",
        "starlette",
        "Starlette",
        "HTTPException",
        "sqlalchemy",
        "AsyncSession",
        "aio_pika",
        "RabbitMq",
        "json.dumps",
        "dict_to_fingerprint",
        "import Request",
        "import Response",
    ]

    for forbidden in forbidden_tokens:
        assert forbidden not in all_src, (
            f"Forbidden token '{forbidden}' found in idempotency contracts"
        )


def test_database_schema_stability() -> None:
    """Verify database models and migrations remain stable at Alembic head 0008."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import app.db.models  # noqa: F401

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
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26

    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script_dir.get_current_head()
    assert head == "0016"

    version_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in version_dir.glob("*.py")]
    assert any("0007" in f for f in migration_files)
    assert any("0009" in f for f in migration_files)
    assert any("0010" in f for f in migration_files)
    assert any("0011" in f for f in migration_files)
    assert any("0012" in f for f in migration_files)
    assert any("0013" in f for f in migration_files)
    assert any("0014" in f for f in migration_files)
    assert any("0015" in f for f in migration_files)
    assert any("0016" in f for f in migration_files)
    assert not any("0017" in f for f in migration_files)
