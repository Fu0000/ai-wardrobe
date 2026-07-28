#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

run_api() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
}

run_miniapp() {
  aiw_require_command pnpm
  cd "${AIW_REPO_ROOT}"
  pnpm dev:miniapp
}

run_worker() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv run celery -A app.worker.celery_app:celery_app worker \
    -Q ai_fast,image_generation,media_generation,maintenance \
    --loglevel=INFO
}

run_beat() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv run celery -A app.worker.celery_app:celery_app beat --loglevel=INFO
}

case "${1:-}" in
  api)
    aiw_run_logged "dev-api" run_api
    ;;
  miniapp)
    aiw_run_logged "dev-miniapp" run_miniapp
    ;;
  worker)
    aiw_run_logged "dev-worker" run_worker
    ;;
  beat)
    aiw_run_logged "dev-beat" run_beat
    ;;
  *)
    aiw_fail "用法: scripts/dev.sh {api|miniapp|worker|beat}"
    ;;
esac
