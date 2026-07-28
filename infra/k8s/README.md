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

`base/` intentionally exposes only a `ClusterIP`. `staging/` adds the Tencent
TKE `qcloud` Ingress and must be rendered by the fail-closed deployment tool;
applying the placeholder file directly is invalid. The Ingress reuses the fixed
Terraform-managed public CLB, binds one existing Tencent SSL certificate, listens
only on 80/443, redirects HTTP with method-preserving 307, and enables CLB
deletion protection. The TKE Ingress Controller must be v2.11.0 or newer.

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
- variable `STAGING_API_HOST`, the exact lowercase DNS name without scheme/path
- variable `STAGING_TLS_CERT_ID`, the existing Tencent SSL server certificate ID
- variable `STAGING_EDGE_CLB_ID`, exactly matching Terraform output `edge_clb_id`
- variable `STAGING_TRUSTED_PROXY_CIDRS_JSON`, a non-empty JSON list containing only
  the exact source CIDRs used by the cluster ingress/load-balancer when connecting
  to the API (for example `["10.42.7.0/24"]`)

Protect the environment with required reviewers. The cluster identity should be scoped to the `ai-wardrobe` namespace.

Before the first application deployment, point the `STAGING_API_HOST` DNS A
record at every address in Terraform output `edge_clb_vips`, wait for public
resolution, and confirm the certificate is issued, unexpired, covers the exact
host, and belongs to the same Tencent account. Do not upload private-key material
to Kubernetes: TKE references only the certificate ID.

The edge proxy must discard any client-supplied forwarding headers and append its
observed source address to `X-Forwarded-For`. The API ignores forwarding headers
from peers outside the configured CIDRs, rejects whole-address-space trust, and
uses the rightmost untrusted hop for rate limiting and audit logs.

## Release order

`deploy-staging.yml` builds the image with provenance and an SBOM, validates and
applies the trusted-proxy contract, creates the registry pull Secret, runs
migrations, and renders the Staging overlay only after checking the image SHA,
HTTPS origin, host, certificate, fixed CLB and Ingress Controller version. It
performs a server-side dry run before applying, waits for every rollout, proves
the Ingress uses the expected CLB and DNS, verifies certificate/hostname trust,
HSTS and the 307 HTTP redirect, then calls `/health/ready`.

If migration or readiness fails, the workflow stops. Do not bypass the migration gate or replace an immutable SHA tag.

The CLB and Ingress both have deletion protection. Planned edge removal requires
a separate approved change that first drains traffic, archives evidence, removes
the Ingress protection annotation, and only then disables Terraform protection;
ordinary application rollback must never delete the edge.

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
