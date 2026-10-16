# Rate Limiting & Tenant Quota Architecture (Phase 8E)

**Status Governance**:
- `APPLICATION_CAPACITY_QUOTAS = PASS`
- `RATE_LIMITING = BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`

**Source-of-Truth Review**:
- Original source-of-truth documents (`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`, `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`, `IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`, `opes eng.pdf`) are **SILENT** regarding edge rate-limiting and reverse proxy topology: `ORIGINAL_SOURCE_RATE_LIMIT_REQUIREMENT = SILENT`.
- Architectural adherence: Fully adheres to [ADR-007: Cache and Coordination — Database First](file:///F:/operations-engine/docs/adr/ADR-007-cache-and-coordination.md) and Phase 8A tenant isolation contracts.

---

## 1. Architectural Distinction: Edge Rate Limiting vs Application Resource Quotas

To prevent conflation between network-edge volumetric throttling and application-level domain capacity controls, the platform defines a clear boundary between two decoupled enforcement tiers:

```
[ Tier 1: Ingress / API Gateway (Edge Request Rate Limiting) ]
         │  • Enforces: Volumetric DoS / DDoS mitigation, per-IP throttling, brute-force protection
         │  • Status: BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION (External Infrastructure)
         │  • Response: HTTP 429 Too Many Requests (Retry-After)
         ▼
[ Tier 2: Application Replicas (FastAPI Core Runtime) ]
         │  • Enforces: Cryptographic Bearer / Service API Key verification
         │  • Establishes: Immutable AuthenticatedPrincipal.tenant_context
         ▼
[ Tier 2: TenantQuotaManager (Application Resource Quotas) ]
            • Enforces: State-aware multi-replica database-backed capacity ceilings
            • Status: PASS (Fully Implemented & Verified in app/core/capacity/quotas.py)
            • Protects: Solvers, Workflow instances, AI plans, Schedule repairs
            • Response: Canonical ApiError 429 ("capacity.quota_exceeded", Retry-After: 30)
```

---

## 2. Tier 1: Edge Request Rate Limiting Specification

Because the repository does **not** manage edge infrastructure (Nginx, Traefik, Envoy, or Kubernetes Ingress controllers are not present in this application codebase), edge rate limiting is formally classified as **`BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`**.

### Required Deployment Gateway Policy
When provisioned by the institutional hosting infrastructure / cloud platform (e.g., AWS ALB + WAF, Envoy Gateway, Kong, NGINX Ingress), the edge tier must enforce the following policy:

1. **Enforcement Location**: L7 Edge / Ingress Reverse Proxy.
2. **Trusted Key Basis**:
   - For unauthenticated endpoints: Client IP (`X-Forwarded-For` validated against trusted upstream proxy CIDRs).
   - For authenticated traffic: Extracted `Authorization` Bearer token / API key hash or mutual TLS client certificate.
3. **Per-IP Volumetric Protection**:
   - Token bucket / leaky bucket ceiling: Max 1,000 requests per minute per IP.
   - Burst allowance: Up to 50 concurrent in-flight HTTP requests per IP.
4. **Per-Principal / API-Key Policy**:
   - General CRUD endpoints: Max 300 requests per minute per authenticated principal.
   - Batch & sync endpoints: Max 60 requests per minute per authenticated principal.
5. **Standardized HTTP 429 Contract**:
   - Return status: `HTTP 429 Too Many Requests`.
   - Header: `Retry-After: <seconds>` (default 30).
   - Header: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
6. **Volumetric Protection Assumptions**:
   - Gateway absorbs layer-7 floods, TLS negotiation overhead, and malformed socket floods, preventing Python process socket saturation.

---

## 3. Tier 2: Application Resource Quotas (`APPLICATION_CAPACITY_QUOTAS = PASS`)

Implemented in [TenantQuotaManager](file:///F:/operations-engine/app/core/capacity/quotas.py#L48) and verified by 19 comprehensive unit tests in [tests/unit/capacity/test_capacity_controls.py](file:///F:/operations-engine/tests/unit/capacity/test_capacity_controls.py).

### Policies by Route & Resource Cost

| Resource Class | Endpoint / Operation | Abuse / Failure Mode | Enforcement Point | Quota Default | Error Contract |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Schedule Solvers** | `POST /api/v1/schedule-jobs` | CPU core exhaustion, worker starvation | `TenantQuotaManager` | **5 active/queued jobs per tenant** | HTTP 429, `Retry-After: 30` |
| **Schedule Repairs** | `POST /api/v1/schedules/{id}/repair` | Solver thread monopolization during disruption | `TenantQuotaManager` | **5 active repairs per tenant** | HTTP 429, `Retry-After: 30` |
| **Workflow Instances** | `POST /api/v1/workflows/instances` | Flowable connection pool & memory saturation | `TenantQuotaManager` | **20 running workflows per tenant** | HTTP 429, `Retry-After: 30` |
| **Plan Generation** | `POST /api/v1/goals/{id}/generate-plan` | Model provider API quota and token drain | `TenantQuotaManager` | **50 active plans per tenant** | HTTP 429, `Retry-After: 30` |
| **Operations Analytics** | `GET /api/v1/analytics/operations` | PostgreSQL CPU & buffer cache churn | Direct SQL Aggregate | Direct composite index scan | Sub-16ms latency |

---

## 4. Security & Anti-Bypass Invariants

Application capacity quotas cannot be bypassed by malicious or compromised clients:

1. **Untrusted Headers Ignored**: The system strictly ignores client-supplied headers such as `X-Tenant-ID`. Quota keys are strictly derived from the cryptographically verified `AuthenticatedPrincipal.tenant_context` established during request authentication. Verified in `test_quota_enforcement_immune_to_forged_x_tenant_id`.
2. **Multi-Credential Consistency**: A tenant creating multiple service API keys (e.g., `key-1`, `key-2`, `key-3`) cannot bypass capacity quotas. Because quota checks query database records filtered on `tenant_id`, all keys belonging to that tenant share the exact same quota pool. Verified in `test_quota_shared_across_distinct_api_credentials_for_same_tenant`.
3. **Payload Mismatch Immunity**: Quota evaluation ignores mismatched payload `tenant_id` fields, binding strictly to the authenticated context. Verified in `test_quota_enforcement_immune_to_payload_tenant_mismatch`.
4. **Concurrent Submissions Fail Closed**: Rapid concurrent bursts exceeding the quota ceiling all fail closed with HTTP 429. Verified in `test_concurrent_quota_submissions_fail_closed`.
5. **Idempotency Reconciliation**: Idempotent replays (submitting the exact same request with the same `Idempotency-Key`) return the cached result and do **not** decrement or consume tenant quota slots.

---

## 5. HTTP 429 Error Representation

When a quota is exceeded, the API returns the canonical Phase 1 error envelope:

```json
{
  "error": {
    "code": "capacity.quota_exceeded",
    "message": "Tenant 'tenant-alpha' has exceeded maximum active solver jobs quota (5). Please wait for existing jobs to complete.",
    "correlation_id": "01925b42-d6c1-7efb-9cf2-111111111111"
  }
}
```

**Headers**:
```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 30
X-Correlation-ID: 01925b42-d6c1-7efb-9cf2-111111111111
```

---

## 6. Observability & Privacy Guarantees

Quota rejections and queue depth metrics are exported via OpenTelemetry:
- Counter: `e7.capacity.quota.rejections`
  - Attributes: `{"resource_class": "..."}` in `{'solver_jobs', 'repair_jobs', 'workflow_burst', 'plan_generation'}`.
- UpDownCounter: `e7.capacity.queue.depth`
  - Attributes: `{"queue_name": "..."}`.
- Histogram: `e7.capacity.queue.oldest_item_age_seconds`
  - Attributes: `{"queue_name": "..."}`.
- **Strict Privacy Invariant**: Metric labels **NEVER** include `tenant_id`, user IDs, or UUIDs, preventing Prometheus metric explosion or multi-tenant information leakage.
