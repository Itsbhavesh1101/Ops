"""Service API key verification foundation for IR Infotech E-7 Operations Engine.

Implements high-entropy structured service API key validation, SHA-256 secret digest
verification with constant-time comparison, async storage lookup protocol, and lifecycle
enforcement (enabled/disabled, expiration). Returns VerifiedServiceCredentialIdentity.
Does NOT derive tenant_id, construct AuthenticatedPrincipal, or assign roles/permissions.
"""

import hashlib
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, field_validator

from app.core.auth.models import VerifiedServiceCredentialIdentity

logger = structlog.get_logger("app.core.auth.service_api_key")

SERVICE_KEY_PREFIX: str = "e7sk_"
MIN_SECRET_LENGTH: int = 43
MAX_SECRET_LENGTH: int = 256
MAX_KEY_LENGTH: int = 512

CREDENTIAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SECRET_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{43,256}$")

# Pre-computed dummy digest for constant-time comparison when credential ID is not found.
# Provides a best-effort reduction of obvious timing differences without claiming
# full network-level timing indistinguishability.
_DUMMY_SECRET_DIGEST: str = hashlib.sha256(
    b"e7-operations-engine-dummy-credential-secret-digest-timing-mitigation"
).hexdigest()


# -----------------------------------------------------------------------------
# Internal Error Taxonomy
# -----------------------------------------------------------------------------


class ServiceApiKeyVerificationError(Exception):
    """Base exception for all service API key verification failures."""

    reason_code: str = "SERVICE_API_KEY_VERIFICATION_FAILED"

    def __init__(
        self,
        message: str = "Service API key verification failed",
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        if reason_code:
            self.reason_code = reason_code


class MalformedServiceApiKeyError(ServiceApiKeyVerificationError):
    """Raised when the raw service API key does not adhere to the structured format."""

    reason_code: str = "MALFORMED_SERVICE_API_KEY"


class InvalidServiceApiKeyError(ServiceApiKeyVerificationError):
    """Raised when secret verification fails or credential lookup fails."""

    reason_code: str = "INVALID_SERVICE_API_KEY"


class CredentialNotFoundError(InvalidServiceApiKeyError):
    """Raised when credential ID does not exist in store."""

    reason_code: str = "CREDENTIAL_NOT_FOUND"


class CredentialDisabledError(ServiceApiKeyVerificationError):
    """Raised when credential is revoked or marked disabled."""

    reason_code: str = "CREDENTIAL_DISABLED"


class CredentialExpiredError(ServiceApiKeyVerificationError):
    """Raised when credential expiration timestamp is in the past."""

    reason_code: str = "CREDENTIAL_EXPIRED"


class CredentialNotYetValidError(ServiceApiKeyVerificationError):
    """Raised when credential not_before timestamp is in the future."""

    reason_code: str = "CREDENTIAL_NOT_YET_VALID"


# -----------------------------------------------------------------------------
# Pure Functions & Utilities
# -----------------------------------------------------------------------------


def compute_secret_digest(secret: str) -> str:
    """Compute deterministic SHA-256 hexadecimal digest over high-entropy secret.

    SECURITY ARCHITECTURE NOTE:
    Production service-key issuance MUST generate at least 32 bytes of cryptographically
    secure random secret material. The wire-format minimum length is selected to support
    that requirement when using unpadded base64url encoding. The accepted format is sized
    to accommodate at least 32 bytes (256 bits) of cryptographically random secret material.
    Actual entropy depends on secure generation.

    Slow password KDFs are primarily intended to compensate for low-entropy human-chosen
    passwords. E-7 service keys are required to be machine-generated high-entropy secrets,
    so a cryptographic digest plus constant-time comparison is the selected design.
    This design MUST NOT be reused for human passwords.
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("Secret must be a non-empty string")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_service_api_key(api_key: str) -> tuple[str, str]:
    """Parse and strictly validate a structured service API key into (credential_id, secret).

    Canonical Wire Format:
        e7sk_<credential_id>.<secret>

    Strictly rejects whitespace, control characters, missing components, extra separators,
    and out-of-bounds lengths. Sized to accommodate at least 32 bytes (256 bits) of
    cryptographically random secret material (unpadded base64url minimum 43 chars).
    Never echoes raw secret material in error messages.
    """
    if not isinstance(api_key, str) or not api_key:
        raise MalformedServiceApiKeyError("Service API key must be a non-empty string")

    if len(api_key) > MAX_KEY_LENGTH:
        raise MalformedServiceApiKeyError("Service API key exceeds maximum permitted length")

    # Strictly reject any whitespace (including CR, LF, spaces, tabs) or control characters
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise MalformedServiceApiKeyError(
            "Service API key contains whitespace or control characters"
        )

    if not api_key.startswith(SERVICE_KEY_PREFIX):
        raise MalformedServiceApiKeyError("Service API key missing required 'e7sk_' prefix")

    remainder = api_key[len(SERVICE_KEY_PREFIX) :]
    if remainder.count(".") != 1:
        raise MalformedServiceApiKeyError("Service API key must contain exactly one '.' separator")

    credential_id, secret = remainder.split(".", 1)

    if not credential_id or not CREDENTIAL_ID_PATTERN.match(credential_id):
        raise MalformedServiceApiKeyError("Service API key contains invalid credential identifier")

    if not secret or not SECRET_PATTERN.match(secret):
        raise MalformedServiceApiKeyError(
            "Service API key secret contains invalid characters or length out of bounds"
        )

    return credential_id, secret


# -----------------------------------------------------------------------------
# Credential Store Protocol & Record
# -----------------------------------------------------------------------------


class ServiceApiKeyCredentialRecord(BaseModel):
    """Internal verification record for a registered service API key credential.

    Stores cryptographic digest and metadata needed for verification.
    NEVER stores raw API keys or plain secrets.
    Does NOT contain tenant_id, roles, or permissions (RBAC separation).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    credential_id: str
    service_subject: str
    secret_digest: str
    enabled: bool = True
    not_before: datetime | None = None
    expires_at: datetime | None = None
    issuer: str | None = None

    @field_validator("credential_id", "service_subject", "secret_digest")
    @classmethod
    def validate_required_strings(cls, v: str, info: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()

    @field_validator("not_before", "expires_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("Timestamp must be a timezone-aware UTC datetime")
        return v


@runtime_checkable
class ServiceApiKeyCredentialStore(Protocol):
    """Asynchronous protocol for looking up service API key credential records."""

    async def get_by_credential_id(
        self, credential_id: str
    ) -> ServiceApiKeyCredentialRecord | None:
        """Retrieve credential verification record by lookup credential ID, or None if absent."""
        ...


# -----------------------------------------------------------------------------
# Service API Key Verifier
# -----------------------------------------------------------------------------


class ServiceApiKeyVerifier:
    """Verifier for structured service API keys.

    Validates key wire format, retrieves credential record via async store,
    performs constant-time secret digest verification, and enforces validity/status rules.
    Returns a VerifiedServiceCredentialIdentity on success.
    Does NOT derive tenant_id, construct AuthenticatedPrincipal, or assign roles/permissions.
    """

    def __init__(
        self,
        credential_store: ServiceApiKeyCredentialStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.credential_store = credential_store
        self.clock = clock

    async def verify_service_key(self, api_key: str) -> VerifiedServiceCredentialIdentity:
        """Verify raw service API key and return VerifiedServiceCredentialIdentity."""
        try:
            return await self._verify_service_key_internal(api_key)
        except ServiceApiKeyVerificationError as exc:
            logger.warning(
                "service_api_key_verification_failed",
                reason_code=exc.reason_code,
                provider="service_api_key",
            )
            raise

    async def _verify_service_key_internal(self, api_key: str) -> VerifiedServiceCredentialIdentity:
        # 1. Format and character validation
        credential_id, secret = parse_service_api_key(api_key)

        # 2. Compute digest over supplied secret
        supplied_digest = compute_secret_digest(secret)

        # 3. Retrieve record from async credential store
        record = await self.credential_store.get_by_credential_id(credential_id)

        # 4. Unknown credential: run dummy comparison for best-effort timing mitigation
        # and fail closed
        if record is None:
            hmac.compare_digest(supplied_digest, _DUMMY_SECRET_DIGEST)
            raise CredentialNotFoundError(
                "Invalid service API key credential",
                reason_code="CREDENTIAL_NOT_FOUND",
            )

        # 5. Constant-time digest comparison against stored digest.
        # Performed BEFORE evaluating status so wrong secrets never learn credential usability.
        if not hmac.compare_digest(supplied_digest, record.secret_digest):
            raise InvalidServiceApiKeyError(
                "Invalid service API key credential",
                reason_code="INVALID_SERVICE_API_KEY",
            )

        # 6. Status check (evaluated only after correct secret possession is established)
        if not record.enabled:
            raise CredentialDisabledError(
                "Service API key credential is disabled",
                reason_code="CREDENTIAL_DISABLED",
            )

        # 7. Time validity checks (UTC; evaluated only after secret possession is established)
        now = self.clock()
        if record.not_before is not None:
            nb = (
                record.not_before
                if record.not_before.tzinfo
                else record.not_before.replace(tzinfo=UTC)
            )
            if now < nb:
                raise CredentialNotYetValidError(
                    "Service API key credential is not yet valid",
                    reason_code="CREDENTIAL_NOT_YET_VALID",
                )

        if record.expires_at is not None:
            exp = (
                record.expires_at
                if record.expires_at.tzinfo
                else record.expires_at.replace(tzinfo=UTC)
            )
            if now >= exp:
                raise CredentialExpiredError(
                    "Service API key credential has expired",
                    reason_code="CREDENTIAL_EXPIRED",
                )

        # 8. Return frozen canonical VerifiedServiceCredentialIdentity
        return VerifiedServiceCredentialIdentity(
            credential_id=record.credential_id,
            service_subject=record.service_subject,
            issuer=record.issuer or "e7-service-key",
        )
