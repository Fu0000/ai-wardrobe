# Staging deployment contract

The manifests run one immutable backend image as:

- two API replicas with liveness, dependency-aware readiness, HPA, and PDB;
- independent `ai_fast`, `image_generation`, `media_generation`, and `maintenance` workers;
- one Celery Beat replica for Outbox dispatch scheduling;
- a forward-only Alembic migration Job before workload rollout.

The base manifest is Staging-only and sets `AIW_ENVIRONMENT=staging`, so logs,
metrics, traces and product events cannot be mistaken for production traffic.
Runtime safety validation applies equally to Staging and Production: strong
non-local cryptographic keys, private COS, rate limiting, trusted proxies and
OpenTelemetry remain mandatory.

All containers run as UID/GID `10001`, drop Linux capabilities, disable privilege escalation, use a read-only root filesystem, and write temporary state only to bounded `/tmp` volumes.

## Required cluster state

Create both `ai-wardrobe-secrets` and `ai-wardrobe-data-ca` from
`secret.example.yaml` using the platform secret manager. Never commit the
rendered Secrets. The CA Secret is mounted read-only into every API, Worker,
Beat and migration Pod; database URLs must reference those exact paths and
require full certificate verification.

The GitHub `staging` environment must provide:

- `STAGING_KUBECONFIG_B64`
- `STAGING_API_BASE_URL`
- `GHCR_PULL_USERNAME`
- `GHCR_PULL_TOKEN`
- variable `STAGING_TRUSTED_PROXY_CIDRS_JSON`, a non-empty JSON list containing only
  the exact source CIDRs used by the cluster ingress/load-balancer when connecting
  to the API (for example `["10.42.7.0/24"]`)

Protect the environment with required reviewers. The cluster identity should be scoped to the `ai-wardrobe` namespace.

The edge proxy must discard any client-supplied forwarding headers and append its
observed source address to `X-Forwarded-For`. The API ignores forwarding headers
from peers outside the configured CIDRs, rejects whole-address-space trust, and
uses the rightmost untrusted hop for rate limiting and audit logs.

## Release order

`deploy-staging.yml` builds the image with provenance and an SBOM, validates and
applies the trusted-proxy contract, creates the registry pull Secret, runs
migrations, waits for migration success, applies all workloads with the immutable
commit SHA image, waits for every rollout, then calls `/health/ready`.

If migration or readiness fails, the workflow stops. Do not bypass the migration gate or replace an immutable SHA tag.

## Rollback

Application rollback:

```bash
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-api
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-worker-fast
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-worker-image
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-worker-media
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-worker-maintenance
kubectl -n ai-wardrobe rollout undo deployment/ai-wardrobe-beat
```

Database migrations are forward-only. A rollback must deploy application code that remains compatible with the migrated schema; destructive schema changes require an expand/migrate/contract sequence.
