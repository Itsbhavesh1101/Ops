# Operational Runbook: PostgreSQL Logical Backup and Restore

## 1. Symptom
- Corrupted PostgreSQL database cluster, catastrophic filesystem damage, accidental drop of application tables, or unrecoverable schema state.
- Database service reports healthy connectivity but application entities fail integrity invariants.

## 2. Impact
- **Severity**: Critical.
- Complete outage for all stateful E-7 domain operations (workflows, scheduling, task intelligence, audit, outbox).
- API requests fail with 500 / DatabaseUnavailableError. Workers fail health checks.

## 3. Preconditions
- Access to valid `.dump` logical backup file created with `pg_dump -Fc` (custom compressed format).
- Docker or host environment with PostgreSQL 16 client utilities (`pg_restore`, `psql`).
- Target recovery database (must be isolated disposable database `e7_recovery_drill_disposable` or clean staging database).
- Primary development database (`operations_engine`) must NEVER be overwritten without explicit override and verified offline status.

## 4. Safe Recovery Steps

### 4.1 Automated Scripted Recovery (Recommended)
Execute the non-destructive automated recovery script:
```bash
python scripts/recovery/restore_logical.py path/to/backup.dump --target-db e7_recovery_drill_disposable
```

### 4.2 Manual Step-by-Step Recovery
1. **Verify Backup Checksum**:
   ```bash
   certutil -hashfile backup.dump SHA256
   ```
   Compare against backup manifest SHA-256 hash.

2. **Provision Target Database**:
   ```bash
   docker exec e7-postgres psql -U postgres -c "CREATE DATABASE e7_recovery_drill_disposable;"
   ```

3. **Stage Backup into Container**:
   ```bash
   docker cp backup.dump e7-postgres:/tmp/restore.dump
   ```

4. **Execute Restoration via pg_restore**:
   ```bash
   docker exec e7-postgres pg_restore -U postgres -d e7_recovery_drill_disposable --no-owner --no-privileges --clean --if-exists /tmp/restore.dump
   ```

5. **Clean Temporary Container File**:
   ```bash
   docker exec e7-postgres rm -f /tmp/restore.dump
   ```

## 5. Verification Steps
1. **Schema Revision Verification**:
   ```sql
   SELECT version_num FROM alembic_version;
   ```
   *Expected*: `0013`.

2. **Table Count Verification**:
   ```sql
   SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name != 'alembic_version';
   ```
   *Expected*: Exactly `25`.

3. **Tenant Invariant Verification**:
   ```sql
   SELECT count(DISTINCT tenant_id) FROM goals;
   ```
   *Expected*: Greater than 0; consistent with pre-incident tenant ledger.

## 6. Rollback / Escalation
- If `pg_restore` exits with fatal errors (code 1), drop target recovery database:
  ```bash
  docker exec e7-postgres psql -U postgres -c "DROP DATABASE IF EXISTS e7_recovery_drill_disposable;"
  ```
- Inspect backup integrity manifest. Escalate to Physical Point-In-Time Recovery (PITR) if logical dump is truncated or corrupted.

## 7. Data-Loss Considerations
- **RPO Capability**: Equals the frequency of logical backups (e.g., daily or hourly dump interval). Any data written between the last backup and the incident window is not captured in logical dumps.
- **RTO Capability**: Measured local restore duration is **2.4 - 4.5 seconds** for standard development dataset.

## 8. Observability Checks
- Check PostgreSQL container logs:
  ```bash
  docker logs --tail 100 e7-postgres
  ```
- Verify connection pool health:
  ```sql
  SELECT count(*) FROM pg_stat_activity WHERE datname = 'e7_recovery_drill_disposable';
  ```

## 9. Secret-Handling Warning
> [!WARNING]
> NEVER hardcode PostgreSQL passwords or credentials in recovery runbooks or command lines.
> Use environment variables (`POSTGRES_PASSWORD`, `PGPASSWORD`) or container-local peer authentication.
> Never print connection URLs containing embedded secrets.
