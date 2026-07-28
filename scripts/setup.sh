#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

install_backend() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv sync --all-groups
}

install_frontend() {
  aiw_require_command pnpm
  cd "${AIW_REPO_ROOT}"
  pnpm install --frozen-lockfile
}

install_all() {
  install_backend
  install_frontend
}

case "${1:-all}" in
  all)
    aiw_run_logged "setup-all" install_all
    ;;
  backend)
    aiw_run_logged "setup-backend" install_backend
    ;;
  frontend)
    aiw_run_logged "setup-frontend" install_frontend
    ;;
  *)
    aiw_fail "用法: scripts/setup.sh [all|backend|frontend]"
    ;;
esac
