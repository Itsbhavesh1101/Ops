# Operational Runbook: Flowable Engine Outage & State Reconciliation

## 1. Symptom
- Flowable REST API container (`e7-flowable`) unreachable or unresponsive on port 8080.
- Workflow service logs `WorkflowEngineConnectionError` with status code 503 or connect timeout.

## 2. Impact
- **Severity**: High for human workflow transitions (approvals, manual user tasks, timer-driven state machines).
- Flowable is an external workflow orchestration engine. Canonical E-7 application state remains in PostgreSQL (`workflow_instances`, `workflow_tasks`).
- Tasks cannot be transitioned or started until Flowable returns to healthy operation.

## 3. Preconditions
- Flowable backing database (`flowable_db` in PostgreSQL container) is online and consistent.
- Docker environment with `e7-flowable` service definition.

## 4. Safe Recovery Steps
1. **Restart Flowable Container**:
   ```bash
   docker restart e7-flowable
   ```
2. **Wait for Flowable Engine Initialization**:
   Flowable Spring Boot startup performs schema checks and process definition deployment:
   ```bash
   docker logs -f e7-flowable
   ```
   Wait for log entry: `Started FlowableRestApplication in ... seconds`.

3. **Verify REST API Readiness Probe**:
   ```bash
   curl -s -u rest-admin:test http://localhost:8080/flowable-rest/service/management/engine
   ```
   *Expected response*: HTTP 200 with engine name `default` and version `7.0.0` or higher.

4. **Reconcile Active Tasks**:
   The E-7 WorkflowService queries Flowable for active tasks using `list_user_tasks` and synchronizes task state against `workflow_tasks`.

## 5. Verification Steps
1. **Query Active Process Instances**:
   ```bash
   curl -s -u rest-admin:test http://localhost:8080/flowable-rest/service/runtime/process-instances
   ```
2. **Verify User Task Completion**:
   Execute task completion drill or verify pending human approval tasks resume normally.

3. **Verify Timer Jobs**:
   Inspect Flowable timer jobs table:
   ```sql
   SELECT count(*) FROM act_ru_timer_job;
   ```
   *Expected*: Existing timers registered prior to restart survive and resume execution.

## 6. Rollback / Escalation
- If Flowable schema is corrupted but E-7 `workflow_instances` table is intact:
  * Deploy BPMN definitions fresh via `FlowableAdapter.deploy_definition`.
  * E-7 workflow reconciliation maps existing business keys to new engine instances.

## 7. Data-Loss Considerations
- **RPO Capability**: Zero loss for completed workflow tasks (persisted in PostgreSQL). In-flight user tasks retain their state in `act_ru_task` and `act_ru_variable`.
- **RTO Capability**: Measured local Flowable container restart duration is **12 - 25 seconds** (JVM startup).

## 8. Observability Checks
- Flowable connection metrics:
  Check OpenTelemetry meter `flowable.request.duration` and error count.
- Flowable job executor status:
  Check for dead-lettered jobs in `act_ru_deadletter_job`.

## 9. Secret-Handling Warning
> [!WARNING]
> Flowable credentials (`FLOWABLE_USER`, `FLOWABLE_PASSWORD`) must be managed strictly via environment variables.
> Never print passwords in logs or terminal outputs. Pydantic `FlowableSettings` enforces `SecretStr` masking.
