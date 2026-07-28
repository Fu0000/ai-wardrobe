#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

run_tests() {
  aiw_require_command uv
  aiw_require_command pnpm
  cd "${AIW_REPO_ROOT}/backend"
  uv run pytest
  cd "${AIW_REPO_ROOT}"
  pnpm test
}

run_lint() {
  aiw_require_command uv
  aiw_require_command pnpm
  "${AIW_SCRIPT_DIR}/check-structure.sh"
  "${AIW_SCRIPT_DIR}/check-docs.sh"
  cd "${AIW_REPO_ROOT}/backend"
  uv run ruff check .
  uv run ruff format --check .
  cd "${AIW_REPO_ROOT}"
  pnpm lint
}

run_typecheck() {
  aiw_require_command uv
  aiw_require_command pnpm
  cd "${AIW_REPO_ROOT}/backend"
  uv run mypy app tests scripts
  cd "${AIW_REPO_ROOT}"
  pnpm typecheck
}

run_security_audit() {
  aiw_require_command uv
  aiw_require_command pnpm
  local requirements_file
  requirements_file="$(mktemp "${TMPDIR:-/tmp}/aiw-requirements.XXXXXX")"
  trap 'rm -f "${requirements_file}"' RETURN

  cd "${AIW_REPO_ROOT}/backend"
  uv export \
    --frozen \
    --no-dev \
    --no-emit-project \
    --format requirements-txt \
    --quiet >"${requirements_file}"
  uv run pip-audit --requirement "${requirements_file}" --disable-pip
  cd "${AIW_REPO_ROOT}"
  pnpm security:audit
}

run_build() {
  aiw_require_command pnpm
  cd "${AIW_REPO_ROOT}"
  pnpm build
}

case "${1:-}" in
  test)
    aiw_run_logged "quality-test" run_tests
    ;;
  lint)
    aiw_run_logged "quality-lint" run_lint
    ;;
  typecheck)
    aiw_run_logged "quality-typecheck" run_typecheck
    ;;
  structure)
    aiw_run_logged "quality-structure" "${AIW_SCRIPT_DIR}/check-structure.sh"
    ;;
  docs)
    aiw_run_logged "quality-docs" "${AIW_SCRIPT_DIR}/check-docs.sh"
    ;;
  security-audit)
    aiw_run_logged "quality-security-audit" run_security_audit
    ;;
  build)
    aiw_run_logged "quality-build" run_build
    ;;
  *)
    aiw_fail "用法: scripts/quality.sh {test|lint|typecheck|structure|docs|security-audit|build}"
    ;;
esac
