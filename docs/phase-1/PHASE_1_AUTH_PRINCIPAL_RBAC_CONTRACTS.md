# Phase 1 Authentication Principal, Tenant & RBAC Contracts Foundation

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1C.1 — Authentication Principal, Tenant & RBAC Contract Foundation  
**Status**: `COMPLETE`  
**Package Root**: `app/core/auth/`  

---

## 1. Overview & Objectives

Task 1C.1 establishes the canonical security contracts, identity representations, tenant authorization boundaries, and deterministic Role-Based Access Control (RBAC) foundation for the **IR Infotech E-7 Operations Engine**.

This deliverable establishes:
1. **Provider-Neutral Principal Model**: A decoupled, immutable `AuthenticatedPrincipal` representing an authenticated human or service identity possessing a canonical E-7 principal ID, tenant context, roles, and permissions.
2. **Verified External Identity vs. Authorized Principal**: An explicit architectural separation between cryptographically verified external tokens (`VerifiedOidcIdentity`, `VerifiedServiceCredentialIdentity`) and authorized internal principals (`AuthenticatedPrincipal`).
3. **Tenant Authority Invariant**: A strict security guardrail ensuring tenant context originates exclusively from the authenticated principal or verified external identity resolution, never from unverified client inputs (headers, URL parameters, or request payloads).
4. **Asynchronous Security Context Lifecycle**: A dedicated `ContextVar` maintaining asynchronous request-scoped principal isolation with token-based cleanup and zero silent fallback to anonymous principals.
5. **Deterministic RBAC & FastAPI Dependencies**: Reusable dependency factories enforcing role and permission constraints with standardized HTTP 401 Unauthorized vs. HTTP 403 Forbidden semantics.
6. **Abstract Identity Resolver Protocols**: Asynchronous protocol interfaces (`ExternalIdentityResolver`, `ServiceIdentityResolver`) decoupling security contracts from concrete persistence or provider verification implementations (deferred to Tasks 1C.2 and 1C.3).

---

## 2. Canonical Security Architecture & Flow

The authentication and authorization pipeline enforces strict separation between token verification, identity resolution, tenant authorization, and RBAC evaluation:

```
[ External Client Request ]
           │
           ▼
[ Cryptographic Token / Credential Verification ]
           │ (Task 1C.2 for Google OIDC / Task 1C.3 for Service API Key)
           ▼
[ Verified External Identity ] (VerifiedOidcIdentity / VerifiedServiceCredentialIdentity)
           │
           ▼
[ ExternalIdentityResolver / ServiceIdentityResolver ]
           │ (Async mapping against approved directory / PostgreSQL)
           ▼
[ AuthenticatedPrincipal ] (Canonical principal_id, tenant_id, roles, permissions)
           │
           ▼
[ Security ContextVar ] (set_current_principal with Token reset semantics)
           │
           ▼
[ FastAPI RBAC Authorization Hooks ] (require_roles, require_permissions)
           │
     ┌─────┴────────────────────────────────┐
     ▼                                      ▼
[ HTTP 401 Unauthorized ]            [ HTTP 403 Forbidden ]
(Missing / Unauthenticated)          (Insufficient Roles / Permissions)
```

---

## 3. Core Models & Provider Neutrality (`app.core.auth.models`)

### 3.1 Authentication Method (`AuthenticationMethod`)
Bounded string enumeration of supported authentication mechanisms:
- `OIDC`: OpenID Connect token authentication (initial provider: Google Workspace / Cloud Identity).
- `SERVICE_API_KEY`: Cryptographically hashed machine-to-machine service credential authentication.

Speculative mechanisms (passwords, SAML, LDAP, session cookies) are strictly excluded.

### 3.2 Principal Type (`PrincipalType`)
Distinguishes between human operators and machine accounts:
- `HUMAN`: Human user identity authenticated via OIDC.
- `SERVICE`: Automated subsystem, daemon, or integration worker authenticated via service credentials.

Both principal types share the identical canonical authorization and tenant isolation contract.

### 3.3 Verified External Identity (`VerifiedOidcIdentity`)
Represents identity claims that have **already** passed cryptographic signature, expiry, issuer, and audience validation:
- `issuer: str`: Identity provider URL (validated non-empty string).
- `subject: str`: Unique subject identifier at the provider (validated non-empty string).
- `audiences: frozenset[str]`: Normalized, immutable collection of validated target client audiences. Rejects empty collections and whitespace-only entries.
- `email: str | None`: Informational email attribute (optional, descriptive only).
- `hosted_domain: str | None`: Provider domain attribute (e.g. Google Workspace `hd`).

> [!IMPORTANT]
> **Zero Intrinsic Authorization**: A `VerifiedOidcIdentity` carries zero intrinsic tenant authorization or permission grants in E-7. An external subject cannot establish tenant membership simply by asserting an email or domain claim; it must pass through an authoritative `ExternalIdentityResolver`.

### 3.4 Verified Service Credential Metadata (`VerifiedServiceCredentialIdentity`)
Represents safe metadata of an authenticated service account:
- `credential_id: str`: Unique credential identifier (validated non-empty string).
- `service_subject: str`: Machine/subsystem service name (validated non-empty string).
- `issuer: str | None`: Optional credential issuer reference.
- **Invariant**: Raw API keys, secret hashes, or bearer tokens are strictly forbidden in this model (`extra="forbid"`).

### 3.5 Tenant Context (`TenantContext`)
- `tenant_id: str`: Canonical E-7 organization/tenant identifier (validated non-empty string).
- `name: str | None`: Optional descriptive organization name.

### 3.6 Authenticated Principal (`AuthenticatedPrincipal`)
Canonical E-7 security representation:
- `principal_id: str`: Canonical internal identifier (validated non-empty string).
- `principal_type: PrincipalType`: `HUMAN` or `SERVICE`.
- `authentication_method: AuthenticationMethod`: `OIDC` or `SERVICE_API_KEY`.
- `tenant_id: str`: Immutable organization boundary (validated non-empty string).
- `roles: frozenset[str]`: Coarse responsibility groupings. Rejects empty or whitespace-only entries.
- `permissions: frozenset[str]`: Fine-grained deterministic capabilities. Rejects empty or whitespace-only entries.
- `issuer: str | None`: External identity reference.
- `external_subject: str | None`: External subject reference.
- **Config & Immutability**: Enforces `frozen=True` and `extra="forbid"`. Secrets, passwords, or tokens cannot be injected into the principal.

---

## 4. Tenant Authority Invariant

In accordance with [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md) Section 4:
1. **No Client-Supplied Tenant Trust**: The application never trusts client-supplied headers (e.g., `X-Tenant-ID`), query parameters (`?tenant_id=...`), or request body fields to establish tenant scope.
2. **Principal Origin**: Tenant scope is derived exclusively from the verified `AuthenticatedPrincipal.tenant_id`.
3. **Resource Validation**: If a subsequent API request provides a tenant identifier to select or filter resources, that input is strictly authorized against the authenticated principal's resolved `tenant_id`.

---

## 5. Security Context Lifecycle (`app.core.auth.context`)

Request-scoped authentication context is isolated using Python's standard `contextvars`:
- **Context Variable**: `_current_principal_ctx: ContextVar[AuthenticatedPrincipal | None]`.
- **Separation of Concerns**: Maintained completely separate from `correlation_id_ctx` in `app.core.correlation`.
- **Token/Reset Pattern**:
  ```python
  token = set_current_principal(principal)
  try:
      # Request dispatch / execution
      tenant_id = get_current_tenant_id()
  finally:
      reset_current_principal(token)
  ```
- **No Anonymous Principal**: If unauthenticated, `get_current_principal()` strictly returns `None`. No synthetic "guest" or "anonymous" object is constructed.
- **Asynchronous Isolation**: Coroutines executing across asynchronous worker tasks maintain strict context isolation without data leakage.

---

## 6. Provider-Neutral Resolver Interfaces (`app.core.auth.resolvers`)

Task 1C.1 defines pure protocol interfaces for identity resolution:

### 6.1 `ExternalIdentityResolver`
```python
@runtime_checkable
class ExternalIdentityResolver(Protocol):
    async def resolve_oidc_identity(
        self, identity: VerifiedOidcIdentity
    ) -> AuthenticatedPrincipal | None: ...
```
- **Deferred Implementation**: Concrete resolution against PostgreSQL or an identity directory is scheduled for subsequent tasks.
- **Zero Mock Hardcoding**: Production code contains zero hardcoded user/tenant mappings.

### 6.2 `ServiceIdentityResolver`
```python
@runtime_checkable
class ServiceIdentityResolver(Protocol):
    async def resolve_service_identity(
        self, identity: VerifiedServiceCredentialIdentity
    ) -> AuthenticatedPrincipal | None: ...
```

---

## 7. Deterministic RBAC & FastAPI Dependencies (`app.core.auth.rbac`)

Role and permission evaluations are deterministic boolean set operations:

| Dependency | Signature | Semantics |
| :--- | :--- | :--- |
| `require_authenticated_principal` | `() -> AuthenticatedPrincipal` | Requires active authenticated principal; raises `401 Unauthorized` if unauthenticated. |
| `require_roles(*roles)` | `(*roles: str) -> Callable` | Requires principal to possess **all** listed roles; raises `403 Forbidden` if roles are missing. |
| `require_any_role(*roles)` | `(*roles: str) -> Callable` | Requires principal to possess **at least one** listed role; raises `403 Forbidden` if none match. |
| `require_permissions(*permissions)` | `(*permissions: str) -> Callable` | Requires principal to possess **all** listed permissions; raises `403 Forbidden` if permissions are missing. |
| `require_any_permission(*permissions)` | `(*permissions: str) -> Callable` | Requires principal to possess **at least one** listed permission; raises `403 Forbidden` if none match. |

### Error Semantics
- **401 Unauthorized**: Emitted when an unauthenticated request attempts to access an endpoint protected by `require_authenticated_principal` or downstream RBAC dependencies (`detail="Authentication required"`).
  - **Authentication-Scheme Neutral**: Generic 401 handling strictly emits **no** scheme-specific challenge header (`WWW-Authenticate: Bearer` is omitted). Because the canonical security model supports multiple authentication methods (`OIDC` and `SERVICE_API_KEY`), generic authorization dependencies do not pre-commit to a specific transport or challenge scheme.
  - **Concrete Authenticators**: Scheme-specific challenges belong strictly to concrete ingress authenticators (e.g. Bearer challenges in Task 1C.2, service key challenges in Task 1C.3).
- **403 Forbidden**: Emitted when the principal is authenticated but lacks required roles or permissions (`detail="Forbidden"`). Internal role/permission names and policy rules are not leaked in error details. No `WWW-Authenticate` header is emitted on 403 responses.

---

## 8. Summary of Files Created & Modified

| File Path | Action | Description |
| :--- | :---: | :--- |
| `app/core/auth/models.py` | **Modified** | Provider-neutral models (`AuthenticationMethod`, `PrincipalType`, `VerifiedOidcIdentity` with multi-audience `audiences: frozenset[str]`, `VerifiedServiceCredentialIdentity`, `TenantContext`, `AuthenticatedPrincipal`). Enforces non-empty/whitespace validation on identifiers, roles, and permissions. |
| `app/core/auth/context.py` | **Created** | Asynchronous security `ContextVar` management and accessors (`set_current_principal`, `get_current_principal`, `reset_current_principal`, `get_current_tenant_id`). |
| `app/core/auth/resolvers.py` | **Created** | Protocols `ExternalIdentityResolver` and `ServiceIdentityResolver` for asynchronous identity mapping. |
| `app/core/auth/rbac.py` | **Modified** | FastAPI authorization dependencies (`require_authenticated_principal`, `require_roles`, `require_any_role`, `require_permissions`, `require_any_permission`). Scheme-neutral generic 401 without hardcoded Bearer challenge. |
| `app/core/auth/__init__.py` | **Modified** | Exported canonical public interface for the `app.core.auth` module. |
| `tests/unit/test_auth.py` | **Modified** | 20 focused unit tests covering principal construction, immutability, context isolation, resolver protocols, tenant authority, multi-audience validation, identifier/role/permission safety, scheme-neutral 401, and RBAC hooks. |
| `docs/phase-1/PHASE_1_AUTH_PRINCIPAL_RBAC_CONTRACTS.md` | **Modified** | Architecture, design specifications, and implementation deliverable document updated for Task 1C.1A corrections. |

---

## 9. Test Verification & Inventory Reconciliation

All quality and test suites passed locally with zero warnings and zero errors:

| Command | Scope | Result | Status |
| :--- | :--- | :--- | :---: |
| `python -m compileall app` | Bytecode syntax across all 60 source files in `app/` | Clean | **PASS** |
| `ruff check .` | Ruff linting and import formatting | 0 errors | **PASS** |
| `mypy app` | Static type analysis across 60 source files | 0 errors | **PASS** |
| `pytest --collect-only -q` | Pytest test collection verification | 48 tests collected | **PASS** |
| `pytest -q` | Pytest execution across all unit test suites | 48 passed in 0.42s | **PASS** |

### 9.1 Unit Test Suite Inventory

| Test Suite | Collected Tests | Passed Tests | Tested Capabilities |
| :--- | :---: | :---: | :--- |
| [`tests/unit/test_auth.py`](file:///f:/operations-engine/tests/unit/test_auth.py) | **20** | **20** | Principal construction, service principal, secret-field prohibition, verified OIDC contract, verified service credential contract, tenant authority rule, context lifecycle, async isolation, external resolver protocol, service resolver protocol, scheme-neutral 401 unauthenticated, 200 authenticated, require_roles, require_any_role, require_permissions, require_any_permission, service/human authorization parity, OIDC audiences normalization & validation, identifier safety & blank role/permission rejection, generic 401 scheme-neutrality across hooks |
| [`tests/unit/test_config.py`](file:///f:/operations-engine/tests/unit/test_config.py) | **7** | **7** | Defaults, cached accessor, env override, invalid env, invalid log level, invalid log format, CORS parsing |
| [`tests/unit/test_correlation.py`](file:///f:/operations-engine/tests/unit/test_correlation.py) | **8** | **8** | Correlation ID validation, UUID generation, malformed/oversized replacement, header propagation, contextvar isolation |
| [`tests/unit/test_health.py`](file:///f:/operations-engine/tests/unit/test_health.py) | **3** | **3** | `/api/v1/health` liveness, `/api/v1/ready` readiness, root `/` metadata |
| [`tests/unit/test_logging.py`](file:///f:/operations-engine/tests/unit/test_logging.py) | **5** | **5** | Idempotent setup, context binding, sensitive key censor, unmatched route path/query privacy, route template resolution |
| [`tests/unit/test_smoke.py`](file:///f:/operations-engine/tests/unit/test_smoke.py) | **5** | **5** | Import safety, FastAPI instance, root endpoint, health check, zero external dependency requirement |
| **Total** | **48** | **48** | **100% Pass Rate across all 6 unit test suites** |

---

## 10. Deferred Work & Non-Goals

The following implementation steps remain sequenced for subsequent tasks:
- **Task 1C.2**: Google OIDC token verification (JWKS caching, Google discovery, RSA token validation, audience/issuer checks, external subject extraction).
- **Task 1C.3**: Service API-key authentication, key hashing, and global authentication middleware.
- **Task 1D**: Persistence-backed identity and tenant directory mapping tables.
