#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

run_staging_smoke() {
  aiw_require_command uv
  aiw_require_env STAGING_API_BASE_URL
  aiw_require_env SMOKE_IMAGE
  aiw_require_env AIW_SMOKE_ACCESS_TOKEN
  [[ -f "${SMOKE_IMAGE}" ]] ||
    aiw_fail "授权照片不存在: ${SMOKE_IMAGE}"

  cd "${AIW_REPO_ROOT}/backend"
  uv run python scripts/smoke_mvp.py \
    --base-url "${STAGING_API_BASE_URL}" \
    --image "${SMOKE_IMAGE}"
}

aiw_run_logged "staging-smoke" run_staging_smoke
