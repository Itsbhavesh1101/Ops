# Operational Runbook: PostgreSQL Point-In-Time Recovery (PITR)

## 1. Symptom
- Destructive transaction committed (accidental mass deletion, table drop, corrupted batch migration) at timestamp `T_incident`.
- Need to roll back state to exact point `T_target` (immediately prior to `T_incident`) without losing prior valid transactions.

## 2. Impact
- **Severity**: Critical.
- Risk of catastrophic data loss if recovery overshoots into the destructive transaction or undershoots and loses valid committed state.

## 3. Preconditions
- A valid physical base backup (`pg_basebackup`) taken at or before `T_target`.
- Unbroken continuous WAL archive segments covering the interval from base backup start LSN up to `T_target`.
- WAL archive directory or remote bucket containing all archived WAL files (`.backup` and `.wal`).
- Isolated target recovery container/host. Primary active cluster must NOT be used directly.

## 4. Safe Recovery Steps

### 4.1 Automated Isolated Drill Execution
Execute the automated PITR drill script:
```bash
python scripts/recovery/pitr_drill.py
```

### 4.2 Manual Step-by-Step Recovery
1. **Prepare Isolated Target Directory**:
   Extract physical base backup to a fresh data directory `/recovery/data`:
   ```bash
   tar -xzf /archive/basebackup/base.tar.gz -C /recovery/data
   rm -rf /recovery/data/pg_wal/*
   ```

2. **Configure Recovery Target**:
   Create `recovery.signal` marker in the data directory:
   ```bash
   touch /recovery/data/recovery.signal
   ```
   Append recovery configuration to `postgresql.auto.conf`:
   ```ini
   restore_command = 'cp /archive/wal/%f %p'
   recovery_target_time = '2026-10-10 02:40:00 UTC'
   recovery_target_action = 'promote'
   recovery_target_inclusive = true
   ```
   *(Alternatively use `recovery_target_name = 'restore_point_name'` if a named restore point was created).*

3. **Set File Ownership**:
   Ensure data directory permissions are restricted to user `postgres`:
   ```bash
   chown -R postgres:postgres /recovery/data
   chmod 700 /recovery/data
   ```

4. **Start Recovery Instance**:
   Start PostgreSQL pointing to `/recovery/data`:
   ```bash
   postgres -D /recovery/data
   ```

5. **Monitor WAL Replay & Promotion**:
   PostgreSQL will execute `restore_command` sequentially, replaying WAL files until the target timestamp/point is reached. Upon completion, it automatically promotes to read-write and renames `recovery.signal` to `recovery.signal.done`.

## 5. Verification Steps
1. **Promotion Verification**:
   ```sql
   SELECT pg_is_in_recovery();
   ```
   *Expected*: `f` (false - promoted and accepting read/write transactions).

2. **Synthetic Marker Verification**:
   Query transactions committed before target time vs after target time:
   ```sql
   SELECT * FROM pitr_synthetic_ledger ORDER BY id DESC;
   ```
   *Expected*: Transactions committed before `T_target` are PRESENT; destructive transactions committed after `T_target` are ABSENT.

## 6. Rollback / Escalation
- If WAL replay fails with missing segment gap:
  * Check WAL archive for missing files.
  * Inspect `pg_stat_archiver` on source instance.
  * If WAL continuity is broken, fallback to closest logical backup (`pg_dump`).

## 7. Data-Loss Considerations
- **RPO Capability**: True sub-second RPO capability (up to the last archived WAL boundary or synchronous replica).
- **RTO Capability**: Measured local replay duration is **2.8 - 3.5 seconds** for test scenarios.

## 8. Observability Checks
- Source instance archiver health:
  ```sql
  SELECT archived_count, last_archived_wal, failed_count, last_failed_wal FROM pg_stat_archiver;
  ```
- Target recovery logs:
  Look for log lines: `restored log file ...`, `recovery stopping at restore point ...`, `consistent recovery state reached`, `database system is ready to accept connections`.

## 9. Secret-Handling Warning
> [!WARNING]
> Recovery configurations must never contain database passwords or cloud access tokens in plain text.
> Use IAM instance profiles, environment variables, or secure credential helper scripts for remote bucket access.
