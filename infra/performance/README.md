# MVP performance and capacity tests

Use k6 `v2.1.0`. Remote load is restricted to Staging during an approved test
window with Dashboard and On-call active; the automated baseline is restricted to
the local environment. Never run load against production or use unconsented photos.

## API read capacity

This suite ramps authenticated read traffic from 5 to 20 requests/second by default and enforces error rate <1%, P95 <500 ms and P99 <1 second.

Run the reproducible local baseline after local PostgreSQL/Redis, migrations and the API are ready:

```bash
make local-api-baseline
```

The runner uses the immutable `grafana/k6:2.1.0` image digest, creates an isolated
short-lived performance user, removes it on exit, and writes a sanitized JSON summary
and Markdown report under the gitignored `infra/performance/results/`. It refuses
non-local APIs and non-loopback databases. Override the traffic or duration only with
`K6_START_RATE`, `K6_TARGET_RATE`, `K6_RAMP_DURATION`, `K6_STEADY_DURATION`, and
`K6_RAMP_DOWN_DURATION`.

```bash
export K6_BASE_URL='https://staging.example.com'
export K6_ACCESS_TOKEN='<dedicated performance user token>'
k6 run \
  --summary-export=infra/performance/results/api-read-YYYYMMDD.json \
  infra/performance/api-read.js
unset K6_BASE_URL K6_ACCESS_TOKEN
```

Optional `K6_DIAGNOSIS_ID`, `K6_OPTIMIZATION_ID` and `K6_SCENE_CODE` add resource reads owned by the same performance user. `K6_START_RATE`, `K6_TARGET_RATE` and `K6_PREALLOCATED_VUS` may be raised only after the previous stage passes.

## AI queue and end-to-end capacity

This suite creates one real Diagnosis per dataset row and polls it to a terminal state. It incurs Provider cost and consumes quota. Use dedicated performance users, expendable ready Assets, explicit photo authorization and a unique dataset stored as `*.local.json`; these files and results are gitignored.

```bash
cp infra/performance/ai-job-dataset.example.json \
  infra/performance/ai-job-dataset.local.json

export K6_BASE_URL='https://staging.example.com'
export K6_DATA_FILE='/absolute/path/to/infra/performance/ai-job-dataset.local.json'
export K6_RUN_ID='staging-YYYYMMDD-HHMM'
export K6_ENABLE_COSTLY_AI_LOAD='I_ACCEPT_REAL_AI_COST_AND_AUTHORIZED_DATA'
k6 run \
  --summary-export=infra/performance/results/ai-jobs-YYYYMMDD.json \
  infra/performance/ai-job-capacity.js
unset K6_BASE_URL K6_DATA_FILE K6_RUN_ID K6_ENABLE_COSTLY_AI_LOAD
```

Start with 10 authorized records. Expand to 30～50 only after verifying quotas, budget and alert routing. The Gate requires Diagnosis success ≥95%, P90 <20 seconds, P95 <30 seconds, no lost/stuck Job, no double quota charge and queue recovery after load stops.

## Required report

Archive:

- immutable application SHA and model/prompt/schema versions;
- k6 command, sanitized summary and dataset size (never tokens or photo URLs);
- API/Worker replica counts and resource limits;
- Dashboard screenshots for API, PostgreSQL, Redis, Outbox, queues, Provider, cost and deletion;
- P50/P90/P95/P99, throughput, error categories and dropped iterations;
- queue drain time and database/COS consistency checks;
- conclusion, bottleneck, capacity recommendation and follow-up Owner.

Stop immediately on Blocker/Critical, 5xx >2%, uncontrolled queue growth, Provider cost ceiling breach, database saturation, cross-user access or privacy symptoms.

## Staging Worker pause and queue recovery

`make staging-queue-recovery` is the failure-closed `REL-003` entrypoint. It is
disruptive and incurs real Provider cost: run it only in an approved Staging
window with Dashboard and On-call active. It refuses to continue unless the
Kubernetes ConfigMap and API both identify themselves as `staging`, the current
Context exactly matches the operator-provided Context, and the deployed
API/`ai_fast` Worker images match the expected immutable Commit SHA.

Create `infra/performance/ai-job-dataset.local.json` with 1～50 records following
the example. Every record must use a distinct dedicated Staging user, its own
READY authorized Asset and a non-placeholder Token. The file must be private:

```bash
chmod 600 infra/performance/ai-job-dataset.local.json
export STAGING_API_BASE_URL='https://staging.example.com'
export STAGING_EXPECTED_SHA='<40-character deployed commit SHA>'
export STAGING_KUBE_CONTEXT='<exact current staging context>'
export AIW_QUEUE_DATA_FILE="$PWD/infra/performance/ai-job-dataset.local.json"
export AIW_QUEUE_RECOVERY_CONFIRMATION='I_ACCEPT_STAGING_WORKER_PAUSE_AND_REAL_AI_COST'
make staging-queue-recovery
unset STAGING_API_BASE_URL STAGING_EXPECTED_SHA STAGING_KUBE_CONTEXT
unset AIW_QUEUE_DATA_FILE AIW_QUEUE_RECOVERY_CONFIRMATION
```

The runner records the original `ai_fast` replica count, scales only that
Deployment to zero, creates each Diagnosis once plus an idempotent replay,
proves the Jobs remain pending while the Worker is absent, restores the exact
replica count through an EXIT/signal trap, and waits for every Job to complete.
The report is written with mode `0600` under the ignored
`infra/performance/results/` directory. It includes only aggregate counts,
recovery percentiles, Queue Drain Time and the application SHA; Tokens, user or
resource IDs, Asset references and URLs are never recorded.

This audit proves Worker-pause retention and recovery only. `REL-004` still
requires the approved 10→30→50 AI capacity stages, resource/Dashboard evidence,
database/COS/Quota reconciliation and low-end Android validation.
