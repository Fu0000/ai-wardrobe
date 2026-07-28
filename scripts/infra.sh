#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

compose() {
  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  [[ -f "${AIW_REPO_ROOT}/.env" ]] ||
    aiw_fail "缺少 .env；请先执行 cp .env.example .env。"
  "${docker_bin}" compose \
    --env-file "${AIW_REPO_ROOT}/.env" \
    -f "${AIW_REPO_ROOT}/infra/compose.yaml" \
    "$@"
}

infra_up() {
  compose up -d
}

observability_up() {
  compose --profile observability up -d
}

infra_down() {
  compose down
}

run_migrations() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv run alembic upgrade head
}

case "${1:-}" in
  up)
    aiw_run_logged "infra-up" infra_up
    ;;
  observability-up)
    aiw_run_logged "infra-observability-up" observability_up
    ;;
  down)
    aiw_run_logged "infra-down" infra_down
    ;;
  migrate)
    aiw_run_logged "infra-migrate" run_migrations
    ;;
  *)
    aiw_fail "用法: scripts/infra.sh {up|observability-up|down|migrate}"
    ;;
esac
