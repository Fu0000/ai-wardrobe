#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)/common.sh"

run_staging_alert_delivery_drill() {
  aiw_require_command uv
  [[ "$#" -ge 1 ]] || aiw_fail "用法: make staging-alert-drill ALERT_DRILL_ARGS='<start|ack|finish|abort> ...'"

  cd "${AIW_REPO_ROOT}/backend"
  uv run python -m scripts.operations.alert_delivery_drill "$@"
}

aiw_run_logged "staging-alert-delivery-drill" run_staging_alert_delivery_drill "$@"
