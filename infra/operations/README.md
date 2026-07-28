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

For a local implementation check with Docker already running, use:

```bash
make local-db-drill
```

This command reads the Compose PostgreSQL source without changing it and restores into a separate
`--rm` container backed by tmpfs. It exercises the same Python backup, checksum and restore
verification code, writes its terminal output to `logs/`, and removes only the disposable container
and temporary backup artifacts. Local evidence does not replace managed backup/PITR or Staging
restore approval.

## Alert coverage and delivery drill

Start the local observability profile and execute the non-destructive alert drill:

```bash
make infra-observability-up
make local-alert-drill
```

The observability profile runs dedicated PostgreSQL and Redis exporters. Prometheus rules cover
database and Redis availability, database and Redis connection usage, and Redis memory usage in
addition to API, Worker, Provider, Outbox and deletion failures. The local Redis limit defaults to
256 MiB so its memory alert has a meaningful capacity denominator.

The drill verifies Prometheus and Alertmanager readiness, the five dependency rules, `pg_up=1`,
`redis_up=1`, and Alertmanager alert creation and resolution. Its synthetic alert is removed before
the command succeeds. Output is stored under `logs/`.

This local drill deliberately does not claim notification delivery. Before Staging approval:

1. Configure a real receiver through the platform secret manager; do not commit webhook URLs,
   tokens, phone numbers or email credentials.
2. Attach environment, cluster and service labels, and include a dashboard/runbook link in each
   notification.
3. Trigger one critical and one warning alert, confirm delivery to the named On-call person inside
   the agreed window, acknowledge them, resolve them and retain the notification/response record.
4. Confirm routing inhibition and repeat intervals prevent duplicate notification storms.

`REL-005` and `OBS-02` remain incomplete until that Staging delivery evidence exists.

## Staging security and signed URL audit

Prepare two dedicated Staging users. The Owner must own one READY source Asset and its related
Job, Diagnosis and Optimization; the Attacker must not own any of them. Do not use production users
or place tokens and signed URLs in files, arguments, screenshots or committed reports.

Inject both tokens and the resource IDs through environment variables. The explicit expiry
confirmation is required because the command waits until the real COS URL expires:

```bash
export STAGING_API_BASE_URL='https://staging.example.com'
export SECURITY_EXPECTED_ASSET_HOST='private-bucket.cos.ap-shanghai.myqcloud.com'
export SECURITY_OWNER_ASSET_ID='<owner asset UUID>'
export SECURITY_OWNER_JOB_ID='<owner job UUID>'
export SECURITY_OWNER_DIAGNOSIS_ID='<owner diagnosis UUID>'
export SECURITY_OWNER_OPTIMIZATION_ID='<owner optimization UUID>'
export AIW_SECURITY_OWNER_ACCESS_TOKEN='<owner token>'
export AIW_SECURITY_ATTACKER_ACCESS_TOKEN='<attacker token>'
export AIW_SECURITY_WAIT_FOR_EXPIRY='I_ACCEPT_WAIT_FOR_SIGNED_URL_EXPIRY'
make staging-security-audit
unset STAGING_API_BASE_URL SECURITY_EXPECTED_ASSET_HOST
unset SECURITY_OWNER_ASSET_ID SECURITY_OWNER_JOB_ID
unset SECURITY_OWNER_DIAGNOSIS_ID SECURITY_OWNER_OPTIMIZATION_ID
unset AIW_SECURITY_OWNER_ACCESS_TOKEN AIW_SECURITY_ATTACKER_ACCESS_TOKEN
unset AIW_SECURITY_WAIT_FOR_EXPIRY
```

The audit fails closed unless:

- Owner reads for Asset, Job, Diagnosis and Optimization all return 200;
- Attacker reads of Owner resources are indistinguishable from random absent resources and return
  the resource-specific 404 code;
- every API response includes Request ID and Trace ID;
- the returned Signed URL uses HTTPS and exactly the approved COS hostname;
- the URL is readable before expiry and returns 401/403/404 after its server-declared expiry.

Terminal output records only check names, status codes, durations, TTL and boolean correlation
signals. It never records tokens, resource IDs or the Signed URL. `AST-003` and the
Security/Privacy Release Gate still require actual execution evidence and COS object-deletion
verification; the script alone is not a pass.

## AI model Canary and rollback

The Staging workflow `AI Canary Staging` uses a deterministic hash of internal User ID to select a sticky 0/10/50/100% cohort. Candidate identifiers are non-secret ConfigMap values. Every newly created AI Job persists its resolved routes, timeout, cost ceiling, quality threshold, release track and optimization attempt limit.

Any non-zero rollout first runs the relevant Diagnosis and/or Optimization Eval and cannot patch the
ConfigMap unless the fixed-dataset comparison and current Release Gates pass. A `0%` emergency
rollback deliberately bypasses Eval so rollback is never blocked by Provider or data availability.

Configure these protected `staging` environment secrets before a non-zero run:

- `STAGING_AI_EVAL_BUNDLE_URL`: short-lived HTTPS read URL for the immutable Eval ZIP;
- `STAGING_AI_EVAL_OPENAI_API_KEY` and optional `STAGING_AI_EVAL_OPENAI_BASE_URL`;
- `STAGING_AI_EVAL_COS_BUCKET`, read-only `STAGING_AI_EVAL_COS_SECRET_ID` /
  `STAGING_AI_EVAL_COS_SECRET_KEY`, and optional `STAGING_AI_EVAL_COS_REGION`.

The COS identity must only read the authorized Eval object prefixes and must not have write/delete
permission. The ZIP is limited to 10 MiB and must contain exactly:

```text
diagnosis/baseline.json
diagnosis/manifest.jsonl
optimization/baseline.json
optimization/manifest.jsonl
```

Generate the archive outside the repository in an access-controlled directory. Record its lowercase
SHA-256 as the workflow `eval_bundle_sha256` input. The workflow verifies that digest, rejects ZIP
links, traversal, extra/missing files and oversized content, and never uploads the private input
bundle. Only redacted candidate reports are retained as workflow artifacts for 90 days.

For an image-model Canary, each Optimization Manifest record must contain a
`production_image_model` exactly matching the workflow candidate. For diagnosis and Critic
candidates, every Provider result must use the requested model; fallback usage fails the gate.

Worker execution reads the persisted snapshot, not the current environment. Therefore:

- changing the Canary percentage affects only new Jobs;
- in-flight Jobs finish with their creation-time model policy;
- setting the percentage to `0` is the model rollback for new Jobs;
- rolling back application code must remain compatible with existing policy snapshots.

Promotion sequence:

1. Prepare the immutable authorized Eval bundle and start the workflow; the offline regression gate
   runs before any non-zero policy change.
2. Apply 10% in Staging and execute the full smoke/Eval set.
3. Compare stable vs candidate by model label for quality, error rate, P95 and unit cost.
4. Promote to 50%, observe at least one agreed traffic window, then promote to 100%.
5. Record workflow run, model identifiers, dashboard window, decision and reviewer.

Pause immediately and set percentage to `0` if quality falls >10%, error rate rises >5 percentage points, P95 rises >50%, unit cost rises >30%, a privacy/safety issue appears, or any Blocker/Critical is opened. After rollback, verify Readiness, run a new stable Job, and confirm that pre-rollback in-flight Jobs still report their persisted candidate model.

Production uses the same mechanism only after Staging evidence and environment approval. Never edit model ConfigMap values directly without an auditable workflow run.
