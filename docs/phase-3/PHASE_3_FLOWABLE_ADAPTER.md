# Phase 3 Flowable REST Adapter Architecture

## 1. Provider-Neutral Protocol (`WorkflowAdapterProtocol`)

The core workflow engine interacts with Flowable strictly through an abstract protocol:

```python
class WorkflowAdapterProtocol(Protocol):
    async def deploy_definition(self, name: str, bpmn_xml: str, tenant_id: str) -> EngineDeploymentDTO: ...
    async def start_instance(self, process_definition_key: str, business_key: str, variables: dict[str, Any], tenant_id: str) -> EngineInstanceDTO: ...
    async def get_instance(self, engine_instance_id: str, tenant_id: str) -> EngineInstanceDTO | None: ...
    async def list_user_tasks(self, engine_instance_id: str | None = None, tenant_id: str | None = None) -> list[EngineTaskDTO]: ...
    async def complete_user_task(self, engine_task_id: str, action: str, variables: dict[str, Any], tenant_id: str) -> None: ...
    async def get_history(self, engine_instance_id: str, tenant_id: str) -> list[EngineHistoryEntryDTO]: ...
```

## 2. Flowable REST Mappings

The `FlowableAdapter` maps protocol methods to Flowable REST v1 endpoints:

| Protocol Method | Flowable REST Endpoint | HTTP Method | Payload / Parameters |
|---|---|---|---|
| `deploy_definition` | `/repository/deployments` | POST (multipart) | `file` (`.bpmn20.xml`), `tenantId` |
| `start_instance` | `/runtime/process-instances` | POST (JSON) | `processDefinitionKey`, `businessKey`, `tenantId`, `variables` |
| `get_instance` | `/runtime/process-instances/{id}` | GET | Falls back to `/history/historic-process-instances/{id}` when completed |
| `list_user_tasks` | `/runtime/tasks` | GET | `processInstanceId`, `tenantId` |
| `complete_user_task` | `/runtime/tasks/{id}` | POST (JSON) | `{"action": "complete", "variables": [...]}` |
| `get_history` | `/history/historic-activity-instances` | GET | `processInstanceId`, `tenantId` |

## 3. Resilience, Timeout & Retry Policy

- **HTTP Client**: Built on `httpx.AsyncClient` with bounded execution timeouts (`timeout_seconds=10.0`).
- **Retry Policy**:
  - GET queries automatically retry with exponential backoff on transient errors (`httpx.RequestError` and HTTP 503/504) up to `max_retries=3`.
  - Mutating operations (POST/PUT/DELETE) are NOT retried blindly to prevent unintended external side effects.
- **Resource Management**: Implements `async def close()` and async context manager semantics (`__aenter__` / `__aexit__`) to ensure socket cleanup.

## 4. Authentication Modes & Configuration Validation

The adapter supports two distinct, provider-neutral authentication modes via `FlowableSettings`:

- **Mode A: HTTP Basic Authentication**: Requires both `username` (string) and `password` (`SecretStr`).
- **Mode B: HTTP Bearer Authentication**: Requires `token` (`SecretStr`).

### Validation Invariants:
1. `base_url`, `timeout_seconds`, and `max_retries` are strictly required (zero silent production defaults).
2. Exactly one authentication mode must be configured. Specifying neither raises `ValueError` ("Flowable authentication is required"). Specifying both raises `ValueError` ("Ambiguous Flowable authentication configuration").
3. In Basic auth, specifying `username` without `password` or `password` without `username` raises `ValueError`.
4. Production credentials are never hardcoded in Python defaults; they must be provided via environment variables or explicit deployment secret configuration.
5. All secrets (`password`, `token`) are masked as Pydantic `SecretStr` to prevent leakage in logs, `str`, or `repr`.

## 5. Error Translation Matrix

| HTTP Status | Flowable Engine Error | E-7 Adapter Exception |
|---|---|---|
| 404 | Process instance / Task not found | `EngineEntityNotFoundError` |
| 409 | Task state conflict or already completed | `EngineConflictError` |
| 400 | Invalid payload or syntax | `EngineValidationError` |
| 500 | Engine execution exception | `EngineExecutionError` |
| Timeout / Conn Refused | Flowable unreachable | `EngineCommunicationError` |

## 5. Security & Variable Sanitization

1. **Artifact Validation**: BPMN 2.0 XML is strictly validated with `defusedxml`:
   - XXE / DTD expansion is prohibited.
   - Executable scripts (`<scriptTask>`) are rejected.
   - Dynamic expressions (`${...}`, `#{...}`) are rejected unless explicitly allowed.
   - Start and end event presence is enforced.
2. **Variable Sanitization**: Only explicitly allowlisted variables pass to Flowable (`filter_workflow_variables`). Internal metadata (`_key_digest`, `_action_by`) is stripped of credentials, and strings exceeding maximum byte limits are truncated or rejected.
