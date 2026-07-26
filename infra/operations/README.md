# MVP operations runbook

## Database backup and restore drill

P0 objectives are RPO ≤24 hours and RTO ≤4 hours. Tencent Cloud managed PostgreSQL automatic backup/PITR remains the primary production mechanism. The logical backup below is a portable verification artifact; it does not replace managed snapshots or PITR.

Create libpq service entries outside the repository. Credentials must use a protected service/pass file and must never appear in command arguments, shell history, reports, or CI logs.

Backup from the source database into an encrypted, access-controlled directory:

```bash
export AIW_BACKUP_PGSERVICE='aiw-staging'
cd backend
uv run python scripts/backup_database.py \
  --output-dir='/encrypted/ai-wardrobe-backups'
unset AIW_BACKUP_PGSERVICE
```

The script creates a mode `0600` custom-format dump and manifest containing SHA-256, migration version and aggregate row counts. The directory must already be protected by disk/KMS encryption and mode `0700`; upload retention and deletion are owned by the platform backup policy.

Restore only to a newly created, empty, network-isolated database. Both the libpq service name and actual database name must contain `restore`, `drill`, or `sandbox`; the explicit confirmation prevents an accidental production restore:

```bash
export AIW_RESTORE_PGSERVICE='aiw-restore-drill'
export AIW_RESTORE_CONFIRM='RESTORE_TO_ISOLATED_DATABASE'
cd backend
uv run python scripts/restore_database_drill.py \
  --backup='/encrypted/ai-wardrobe-backups/<backup>.dump' \
  --manifest='/encrypted/ai-wardrobe-backups/<backup>.manifest.json' \
  --report='/secure/reports/restore-drill-YYYYMMDD.json'
unset AIW_RESTORE_PGSERVICE AIW_RESTORE_CONFIRM
```

The drill verifies checksum, empty target, single-transaction restore, migration version, core row counts and referential invariants. A report passes only when backup age ≤24 hours and restore duration ≤4 hours. Separately verify managed backup retention, PITR window, access audit and COS object consistency.

Do not delete the isolated database until QA and the technical owner have reviewed the report. Delete it through the cloud console/change process, not from these scripts.

## AI model Canary and rollback

The Staging workflow `AI Canary Staging` uses a deterministic hash of internal User ID to select a sticky 0/10/50/100% cohort. Candidate identifiers are non-secret ConfigMap values. Every newly created AI Job persists its resolved routes, timeout, cost ceiling, quality threshold, release track and optimization attempt limit.

Worker execution reads the persisted snapshot, not the current environment. Therefore:

- changing the Canary percentage affects only new Jobs;
- in-flight Jobs finish with their creation-time model policy;
- setting the percentage to `0` is the model rollback for new Jobs;
- rolling back application code must remain compatible with existing policy snapshots.

Promotion sequence:

1. Run offline Eval and authorized shadow comparison.
2. Apply 10% in Staging and execute the full smoke/Eval set.
3. Compare stable vs candidate by model label for quality, error rate, P95 and unit cost.
4. Promote to 50%, observe at least one agreed traffic window, then promote to 100%.
5. Record workflow run, model identifiers, dashboard window, decision and reviewer.

Pause immediately and set percentage to `0` if quality falls >10%, error rate rises >5 percentage points, P95 rises >50%, unit cost rises >30%, a privacy/safety issue appears, or any Blocker/Critical is opened. After rollback, verify Readiness, run a new stable Job, and confirm that pre-rollback in-flight Jobs still report their persisted candidate model.

Production uses the same mechanism only after Staging evidence and environment approval. Never edit model ConfigMap values directly without an auditable workflow run.
