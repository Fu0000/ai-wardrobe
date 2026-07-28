#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)/common.sh"

run_staging_security_audit() {
  aiw_require_command uv
  aiw_require_env STAGING_API_BASE_URL
  aiw_require_env SECURITY_EXPECTED_ASSET_HOST
  aiw_require_env SECURITY_OWNER_ASSET_ID
  aiw_require_env SECURITY_OWNER_JOB_ID
  aiw_require_env SECURITY_OWNER_DIAGNOSIS_ID
  aiw_require_env SECURITY_OWNER_OPTIMIZATION_ID
  aiw_require_env AIW_SECURITY_OWNER_ACCESS_TOKEN
  aiw_require_env AIW_SECURITY_ATTACKER_ACCESS_TOKEN
  aiw_require_env AIW_SECURITY_WAIT_FOR_EXPIRY
  [[ "${AIW_SECURITY_WAIT_FOR_EXPIRY}" == "I_ACCEPT_WAIT_FOR_SIGNED_URL_EXPIRY" ]] ||
    aiw_fail "AIW_SECURITY_WAIT_FOR_EXPIRY 未确认等待 Signed URL 真实过期。"

  cd "${AIW_REPO_ROOT}/backend"
  uv run python scripts/security_privacy_audit.py
}

aiw_run_logged "staging-security-audit" run_staging_security_audit
