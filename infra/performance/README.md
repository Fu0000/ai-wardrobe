# MVP performance and capacity tests

Use k6 `v2.1.0`. Run only against Staging during an approved test window, with Dashboard and On-call active. Never run load against production or use unconsented user photos.

## API read capacity

This suite ramps authenticated read traffic from 5 to 20 requests/second by default and enforces error rate <1%, P95 <1 second and P99 <2 seconds.

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
