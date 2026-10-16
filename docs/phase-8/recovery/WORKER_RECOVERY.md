# Operational Runbook: Background Worker & Scheduler Interruption Recovery

## 1. Symptom
- Background worker process crashed, terminated by OOM killer, or host rebooted mid-execution.
- Schedule jobs remain in `SOLVING` state with stale claim timestamp (`claimed_at`).
- Outbox events remain claimed with expired `claim_token`.

## 2. Impact
- **Severity**: Medium to High.
- Schedule solving jobs claimed by the crashed worker are temporarily locked until lease expiration.
- Outbox events claimed by the worker are delayed until their dispatch lease expires.

## 3. Preconditions
- PostgreSQL database is operational and reachable.
- Worker process manager (Docker, systemd, or supervisor) capable of restarting worker processes.

## 4. Safe Recovery Steps
1. **Restart Worker Process**:
   ```bash
   python -m app.worker
   ```
   *(Or restart worker container: `docker restart e7-worker`).*

2. **Reclaim Stale Scheduler Leases**:
   The SchedulingService exposes `reclaim_stale_jobs(session, lease_timeout_seconds=300.0)`.
   Run the automated reclamation routine or execute via scheduled recovery job:
   ```sql
   UPDATE schedule_jobs
   SET status = 'REQUESTED', claimed_at = NULL, claimed_by = NULL
   WHERE status = 'SOLVING' AND claimed_at <= (NOW() - INTERVAL '300 seconds');
   ```

3. **Reclaim Expired Outbox Event Leases**:
   The outbox dispatcher automatically includes expired leases in its polling query:
   ```sql
   SELECT event_id FROM outbox_events
   WHERE published_at IS NULL AND (claimed_at IS NULL OR claimed_at <= NOW() - INTERVAL '30 seconds');
   ```
   No manual intervention is required for outbox events; the restarted dispatcher worker reclaims and re-publishes them automatically.

4. **Reconcile Durable Repair Queue**:
   Pending repair jobs in `schedule_jobs` table are picked up and executed by the restarted repair worker. Duplicate trigger events are suppressed by checking `trigger_event_id` in `input_snapshot`.

## 5. Verification Steps
1. **Verify No Permanently Stuck Jobs**:
   ```sql
   SELECT count(*) FROM schedule_jobs WHERE status = 'SOLVING' AND claimed_at <= (NOW() - INTERVAL '300 seconds');
   ```
   *Expected*: `0`.

2. **Verify Schedule Job Settlement**:
   Confirm previously stuck jobs progress to `FEASIBLE` or `INFEASIBLE` with valid solver metadata.

3. **Verify Stability Invariant**:
   For repaired schedules, verify that no locked assignments were modified:
   ```sql
   SELECT solver_metadata->'stability_kpis'->>'locked_moved' FROM schedule_jobs WHERE schedule_key LIKE 'repair-%';
   ```
   *Expected*: `0`.

## 6. Rollback / Escalation
- If a specific schedule job repeatedly crashes worker processes due to solver memory exhaustion:
  * Manually transition job to `FAILED` with diagnostics:
    ```sql
    UPDATE schedule_jobs SET status = 'FAILED', failure_message = 'Quarantined: solver OOM' WHERE id = '<job_id>';
    ```
  * Notify scheduling engineering team.

## 7. Data-Loss Considerations
- **RPO Capability**: Zero data loss. Unfinished computations are re-solved from the immutable input snapshot stored in PostgreSQL.
- **RTO Capability**: Measured local recovery duration for lease reclamation is **0.06 - 0.15 seconds**.

## 8. Observability Checks
- Active worker count and task processing rates in Prometheus / OpenTelemetry:
  * `e7.scheduling.jobs.active`
  * `e7.outbox.dispatch.claimed`
- Log entries: `reclaimed_stale_jobs_count`, `outbox_claim_acquired`, `repair_job_started`.

## 9. Secret-Handling Warning
> [!WARNING]
> Worker execution environments inherit database and broker credentials via environment configuration.
> Ensure worker process logs do not dump full process environment dictionaries or connection URLs.
