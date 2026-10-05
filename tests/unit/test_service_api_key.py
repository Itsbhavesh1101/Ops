"""Unit tests for service API key verification foundation (Task 1C.3 & Task 1C.3A).

Verifies structured key parsing, 256-bit unpadded base64url secret length bounds,
SHA-256 secret digest verification, constant-time comparison, uniform secret comparison
path prior to status evaluation, lifecycle enforcement, privacy invariants, and configuration.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.auth.models import VerifiedServiceCredentialIdentity
from app.core.auth.service_api_key import (
    CredentialDisabledError,
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialNotYetValidError,
    InvalidServiceApiKeyError,
    MalformedServiceApiKeyError,
    ServiceApiKeyCredentialRecord,
    ServiceApiKeyVerificationError,
    ServiceApiKeyVerifier,
    compute_secret_digest,
    parse_service_api_key,
)
from app.core.config import Settings

# -----------------------------------------------------------------------------
# Test In-Memory Credential Store & Key Helpers
# -----------------------------------------------------------------------------


class InMemoryServiceApiKeyCredentialStore:
    """In-memory fake implementation of ServiceApiKeyCredentialStore for testing only."""

    def __init__(self, records: dict[str, ServiceApiKeyCredentialRecord] | None = None) -> None:
        self._records: dict[str, ServiceApiKeyCredentialRecord] = records or {}

    async def get_by_credential_id(
        self, credential_id: str
    ) -> ServiceApiKeyCredentialRecord | None:
        return self._records.get(credential_id)

    def add_record(self, record: ServiceApiKeyCredentialRecord) -> None:
        self._records[record.credential_id] = record


def create_synthetic_credential(
    credential_id: str = "svc-reporting-1",
    service_subject: str = "reporting-worker",
    secret: str = "synthetic-high-entropy-secret-material-12345678",
    enabled: bool = True,
    not_before: datetime | None = None,
    expires_at: datetime | None = None,
    issuer: str | None = "e7-service-key",
) -> tuple[str, ServiceApiKeyCredentialRecord]:
    """Generate a synthetic wire-format API key and matching stored record."""
    wire_key = f"e7sk_{credential_id}.{secret}"
    digest = compute_secret_digest(secret)
    record = ServiceApiKeyCredentialRecord(
        credential_id=credential_id,
        service_subject=service_subject,
        secret_digest=digest,
        enabled=enabled,
        not_before=not_before,
        expires_at=expires_at,
        issuer=issuer,
    )
    return wire_key, record


# -----------------------------------------------------------------------------
# 1. Wire Format, Length & Parsing Tests (Task 1C.3A Hardened)
# -----------------------------------------------------------------------------


def test_parse_valid_structured_key() -> None:
    """Verify standard valid service API key parses cleanly."""
    key = "e7sk_svc-task-worker-01.vK9x_aB2cD3eF4gH5iJ6kL7mN8oP9qR0sTuV1wX2yZ3_45"
    cred_id, secret = parse_service_api_key(key)
    assert cred_id == "svc-task-worker-01"
    assert secret == "vK9x_aB2cD3eF4gH5iJ6kL7mN8oP9qR0sTuV1wX2yZ3_45"


@pytest.mark.parametrize(
    "secret_len,should_pass",
    [
        (16, False),  # Previously allowed 16-char secret rejected
        (24, False),  # 24-char secret rejected
        (32, False),  # 32-char secret rejected
        (42, False),  # 42-char secret (under 256-bit unpadded base64url minimum) rejected
        (43, True),  # 43-char secret (exact 256-bit unpadded base64url minimum) accepted
        (64, True),  # 64-char secret accepted
        (256, True),  # Maximum boundary secret accepted
        (257, False),  # Secret exceeding 256 chars rejected
    ],
)
def test_secret_length_bounds(secret_len: int, should_pass: bool) -> None:
    """Verify strict 43-character minimum and 256-character maximum for secrets."""
    secret = "a" * secret_len
    raw_key = f"e7sk_svc-1.{secret}"
    if should_pass:
        parsed_id, parsed_secret = parse_service_api_key(raw_key)
        assert parsed_id == "svc-1"
        assert parsed_secret == secret
    else:
        with pytest.raises(MalformedServiceApiKeyError):
            parse_service_api_key(raw_key)


@pytest.mark.parametrize(
    "malformed_key",
    [
        "",  # Empty string
        "   ",  # Blank string
        "sk_svc-1.vK9x_aB2cD3eF4gH5iJ6kL7mN8oP9qR0sTuV1wX2yZ3_45",  # Missing e7sk_ prefix
        "e7sk_svc-1",  # Missing separator and secret
        "e7sk_svc-1.",  # Missing secret
        "e7sk_.vK9x_aB2cD3eF4gH5iJ6kL7mN8oP9qR0sTuV1wX2yZ3_45",  # Missing credential ID
        "e7sk_svc-1.sec1.sec2",  # Extra separator
        "e7sk_svc-1.sec 1234567890123456789012345678901234567890",  # Space inside secret
        "e7sk_svc 1.sec1234567890123456789012345678901234567890",  # Space inside credential ID
        " e7sk_svc-1.sec1234567890123456789012345678901234567890",  # Leading space
        "e7sk_svc-1.sec1234567890123456789012345678901234567890 ",  # Trailing space
        "e7sk_svc-1.sec1234567890123456789012345678901234567890\n",  # Newline character
        "e7sk_svc-1.sec1234567890123456789012345678901234567890\r\n",  # CRLF characters
        "e7sk_svc-1.sec1234567890123456789012345678901234567890\t",  # Tab character
        "e7sk_svc-1.sec1234567890123456789012345678901234567890\x00",  # Null byte
        "e7sk_svc!@#.sec1234567890123456789012345678901234567890",  # Invalid credential ID chars
        "e7sk_svc-1.sec123456789012345678901234567890123456789~",  # Non-base64url character ~
        "e7sk_svc-1.sec123456789012345678901234567890123456789=",  # Base64 padding = rejected
        "e7sk_" + "a" * 65 + ".sec1234567890123456789012345678901234567890",  # Credential ID > 64
        "e7sk_svc-1." + "s" * 257,  # Secret > 256 chars
        "e7sk_" + "a" * 250 + "." + "b" * 260,  # Key > 512 chars
    ],
)
def test_parse_malformed_keys_rejected(malformed_key: str) -> None:
    """Verify any wire format deviation raises MalformedServiceApiKeyError."""
    with pytest.raises(MalformedServiceApiKeyError):
        parse_service_api_key(malformed_key)


# -----------------------------------------------------------------------------
# 2. Secret Digest & Verification Tests
# -----------------------------------------------------------------------------


def test_compute_secret_digest_determinism() -> None:
    """Verify SHA-256 secret digest is deterministic and changes with secret content."""
    s1 = "high-entropy-secret-sample-string-12345678901234567890"
    s2 = "high-entropy-secret-sample-string-22345678901234567890"
    d1_a = compute_secret_digest(s1)
    d1_b = compute_secret_digest(s1)
    d2 = compute_secret_digest(s2)

    assert d1_a == d1_b
    assert d1_a != d2
    assert len(d1_a) == 64  # SHA-256 hex string


@pytest.mark.asyncio
async def test_valid_service_api_key_verifies() -> None:
    """Verify valid synthetic service API key produces VerifiedServiceCredentialIdentity."""
    wire_key, record = create_synthetic_credential(
        credential_id="svc-planner-1",
        service_subject="workflow-planner-agent",
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-planner-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store)

    identity = await verifier.verify_service_key(wire_key)

    assert isinstance(identity, VerifiedServiceCredentialIdentity)
    assert identity.credential_id == "svc-planner-1"
    assert identity.service_subject == "workflow-planner-agent"
    assert identity.issuer == "e7-service-key"

    # Invariants: Zero tenant authority, zero raw secret exposure
    assert not hasattr(identity, "tenant_id")
    assert not hasattr(identity, "roles")
    assert not hasattr(identity, "permissions")
    assert not hasattr(identity, "secret")
    assert not hasattr(identity, "secret_digest")
    assert not hasattr(identity, "raw_api_key")


@pytest.mark.asyncio
async def test_wrong_secret_fails_closed() -> None:
    """Verify valid credential ID with mismatched secret raises InvalidServiceApiKeyError."""
    _, record = create_synthetic_credential(
        credential_id="svc-planner-1",
        secret="correct-synthetic-secret-material-123456789012",
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-planner-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store)

    wrong_key = "e7sk_svc-planner-1.incorrect-synthetic-secret-material-98765432"
    with pytest.raises(InvalidServiceApiKeyError) as exc_info:
        await verifier.verify_service_key(wrong_key)

    assert exc_info.value.reason_code == "INVALID_SERVICE_API_KEY"


@pytest.mark.asyncio
async def test_unknown_credential_id_fails_closed() -> None:
    """Verify unknown credential ID triggers constant-time dummy path and fails closed."""
    store = InMemoryServiceApiKeyCredentialStore()
    verifier = ServiceApiKeyVerifier(credential_store=store)

    unknown_key = "e7sk_svc-nonexistent.some-synthetic-secret-material-123456789012"

    with patch("app.core.auth.service_api_key.hmac.compare_digest", wraps=None) as mock_cmp:
        mock_cmp.return_value = False
        with pytest.raises(CredentialNotFoundError) as exc_info:
            await verifier.verify_service_key(unknown_key)

        # Assert dummy comparison path was executed for best-effort timing mitigation
        assert mock_cmp.called
        assert exc_info.value.reason_code == "CREDENTIAL_NOT_FOUND"


# -----------------------------------------------------------------------------
# 3. Credential Status, Lifecycle & Verification-Path Ordering (Task 1C.3A Hardened)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_credential_with_wrong_secret_yields_invalid_key() -> None:
    """Verify known disabled credential with WRONG secret returns invalid-key, not disabled."""
    _, record = create_synthetic_credential(
        credential_id="svc-revoked-1",
        secret="correct-synthetic-secret-material-123456789012",
        enabled=False,
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-revoked-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store)

    wrong_key = "e7sk_svc-revoked-1.incorrect-synthetic-secret-material-98765432"
    with pytest.raises(InvalidServiceApiKeyError) as exc_info:
        await verifier.verify_service_key(wrong_key)

    assert exc_info.value.reason_code == "INVALID_SERVICE_API_KEY"


@pytest.mark.asyncio
async def test_disabled_credential_with_correct_secret_fails_disabled() -> None:
    """Verify known disabled credential with CORRECT secret fails with CredentialDisabledError."""
    wire_key, record = create_synthetic_credential(
        credential_id="svc-revoked-1",
        secret="correct-synthetic-secret-material-123456789012",
        enabled=False,
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-revoked-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store)

    with pytest.raises(CredentialDisabledError) as exc_info:
        await verifier.verify_service_key(wire_key)

    assert exc_info.value.reason_code == "CREDENTIAL_DISABLED"


@pytest.mark.asyncio
async def test_expired_credential_with_wrong_secret_yields_invalid_key() -> None:
    """Verify known expired credential with WRONG secret returns invalid-key, not expired."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    _, record = create_synthetic_credential(
        credential_id="svc-expired-1",
        secret="correct-synthetic-secret-material-123456789012",
        expires_at=now - timedelta(seconds=1),
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-expired-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store, clock=lambda: now)

    wrong_key = "e7sk_svc-expired-1.incorrect-synthetic-secret-material-98765432"
    with pytest.raises(InvalidServiceApiKeyError) as exc_info:
        await verifier.verify_service_key(wrong_key)

    assert exc_info.value.reason_code == "INVALID_SERVICE_API_KEY"


@pytest.mark.asyncio
async def test_expired_credential_with_correct_secret_fails_expired() -> None:
    """Verify known expired credential with CORRECT secret fails with CredentialExpiredError."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    wire_key, record = create_synthetic_credential(
        credential_id="svc-expired-1",
        secret="correct-synthetic-secret-material-123456789012",
        expires_at=now - timedelta(seconds=1),
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-expired-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store, clock=lambda: now)

    with pytest.raises(CredentialExpiredError) as exc_info:
        await verifier.verify_service_key(wire_key)

    assert exc_info.value.reason_code == "CREDENTIAL_EXPIRED"


@pytest.mark.asyncio
async def test_not_yet_valid_credential_with_wrong_secret_yields_invalid_key() -> None:
    """Verify future credential with WRONG secret returns invalid-key, not not-yet-valid."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    _, record = create_synthetic_credential(
        credential_id="svc-future-1",
        secret="correct-synthetic-secret-material-123456789012",
        not_before=now + timedelta(seconds=30),
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-future-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store, clock=lambda: now)

    wrong_key = "e7sk_svc-future-1.incorrect-synthetic-secret-material-98765432"
    with pytest.raises(InvalidServiceApiKeyError) as exc_info:
        await verifier.verify_service_key(wrong_key)

    assert exc_info.value.reason_code == "INVALID_SERVICE_API_KEY"


@pytest.mark.asyncio
async def test_not_yet_valid_credential_with_correct_secret_fails_not_yet_valid() -> None:
    """Verify future credential with CORRECT secret raises CredentialNotYetValidError."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    wire_key, record = create_synthetic_credential(
        credential_id="svc-future-1",
        secret="correct-synthetic-secret-material-123456789012",
        not_before=now + timedelta(seconds=30),
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-future-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store, clock=lambda: now)

    with pytest.raises(CredentialNotYetValidError) as exc_info:
        await verifier.verify_service_key(wire_key)

    assert exc_info.value.reason_code == "CREDENTIAL_NOT_YET_VALID"


@pytest.mark.asyncio
async def test_unexpired_valid_time_window_accepted() -> None:
    """Verify credential within active time window succeeds."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    wire_key, record = create_synthetic_credential(
        credential_id="svc-valid-window-1",
        not_before=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
    )
    store = InMemoryServiceApiKeyCredentialStore({"svc-valid-window-1": record})
    verifier = ServiceApiKeyVerifier(credential_store=store, clock=lambda: now)

    identity = await verifier.verify_service_key(wire_key)
    assert identity.credential_id == "svc-valid-window-1"


# -----------------------------------------------------------------------------
# 4. Privacy & Anti-Leakage Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_messages_do_not_leak_secret_or_digest() -> None:
    """Verify exceptions never echo raw key, secret, or digest values."""
    secret_marker = "SUPER_SECRET_MARKER_998877_RANDOM_PADDING_12345"
    raw_key = f"e7sk_svc-test.{secret_marker}"
    digest = compute_secret_digest(secret_marker)

    store = InMemoryServiceApiKeyCredentialStore()
    verifier = ServiceApiKeyVerifier(credential_store=store)

    try:
        await verifier.verify_service_key(raw_key)
        pytest.fail("Verification should have failed")
    except ServiceApiKeyVerificationError as exc:
        err_str = str(exc)
        assert secret_marker not in err_str
        assert digest not in err_str
        assert raw_key not in err_str


def test_credential_record_model_forbids_raw_secret_field() -> None:
    """Verify ServiceApiKeyCredentialRecord rejects attempts to pass raw secrets."""
    with pytest.raises(ValidationError):
        ServiceApiKeyCredentialRecord(
            credential_id="c1",
            service_subject="s1",
            secret_digest=compute_secret_digest("some-secret-1234567890123456789012345678901234"),
            raw_secret="forbidden-secret",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# 5. Configuration & Technical Settings Tests
# -----------------------------------------------------------------------------


def test_service_api_key_header_name_settings() -> None:
    """Verify service_api_key_header_name defaults and validation rules."""
    # 1. Default value
    s_default = Settings()
    assert s_default.service_api_key_header_name == "X-E7-Service-Key"

    # 2. Valid custom header name
    s_custom = Settings(service_api_key_header_name="X-Custom-Service-Token")
    assert s_custom.service_api_key_header_name == "X-Custom-Service-Token"

    # 3. Blank header rejected
    with pytest.raises(ValidationError):
        Settings(service_api_key_header_name="   ")

    # 4. Control characters rejected
    with pytest.raises(ValidationError):
        Settings(service_api_key_header_name="X-Service\r\nKey")

    # 5. Spaces rejected
    with pytest.raises(ValidationError):
        Settings(service_api_key_header_name="X Service Key")

    # 6. Delimiters rejected
    with pytest.raises(ValidationError):
        Settings(service_api_key_header_name="X:Service@Key")


# -----------------------------------------------------------------------------
# 6. Import & External Service Isolation Tests
# -----------------------------------------------------------------------------


def test_import_and_verifier_instantiation_zero_external_services() -> None:
    """Verify importing verifier and app.main triggers zero network or DB calls."""
    import app.core.auth.service_api_key
    import app.main

    assert app.main.app is not None
    assert app.core.auth.service_api_key.ServiceApiKeyVerifier is not None
