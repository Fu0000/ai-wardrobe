#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)/common.sh"

readonly k6_image="grafana/k6:2.1.0@sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669"
readonly local_base_url="http://127.0.0.1:8000"
readonly container_base_url="http://host.docker.internal:8000"
readonly start_rate="${K6_START_RATE:-5}"
readonly target_rate="${K6_TARGET_RATE:-20}"
readonly ramp_duration="${K6_RAMP_DURATION:-1m}"
readonly steady_duration="${K6_STEADY_DURATION:-3m}"
readonly ramp_down_duration="${K6_RAMP_DOWN_DURATION:-30s}"
AIW_PERFORMANCE_TEMP_DIR=""
AIW_PERFORMANCE_FIXTURE=""

performance_tools() {
  (
    cd "${AIW_REPO_ROOT}/backend"
    uv run python scripts/performance_tools.py "$@"
  )
}

wait_for_local_api() {
  local ready
  ready="$(curl --fail --silent --show-error --max-time 5 "${local_base_url}/health/ready")" ||
    aiw_fail "本地 API 未就绪；请先执行 make infra-up、make migrate 和 make dev-api。"
  [[ "${ready}" == *'"status":"ready"'* && "${ready}" == *'"environment":"local"'* ]] ||
    aiw_fail "只允许对 environment=local 且 ready 的 API 执行本地基线。"
}

cleanup_performance_fixture() {
  if [[ -f "${AIW_PERFORMANCE_FIXTURE}" ]]; then
    if ! performance_tools delete --fixture "${AIW_PERFORMANCE_FIXTURE}" >/dev/null; then
      printf '临时性能用户清理失败，恢复清单位于: %s\n' \
        "${AIW_PERFORMANCE_FIXTURE}" >&2
      return 1
    fi
  fi
  if [[ "${AIW_PERFORMANCE_TEMP_DIR}" == "${TMPDIR:-/tmp}"/aiw-performance.* ]]; then
    rm -rf -- "${AIW_PERFORMANCE_TEMP_DIR}"
  fi
}

run_local_api_baseline() {
  aiw_require_command curl
  aiw_require_command git
  aiw_require_command uv
  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  wait_for_local_api

  AIW_PERFORMANCE_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiw-performance.XXXXXX")"
  AIW_PERFORMANCE_FIXTURE="${AIW_PERFORMANCE_TEMP_DIR}/fixture.json"
  trap 'cleanup_performance_fixture || exit 1' EXIT

  performance_tools create --output "${AIW_PERFORMANCE_FIXTURE}" >/dev/null
  local access_token
  access_token="$(
    cd "${AIW_REPO_ROOT}/backend"
    uv run python -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["access_token"])' \
      "${AIW_PERFORMANCE_FIXTURE}"
  )"

  local results_dir="${AIW_REPO_ROOT}/infra/performance/results"
  mkdir -p "${results_dir}"
  local timestamp
  timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  local summary_path="${results_dir}/local-api-${timestamp}.json"
  local report_path="${results_dir}/local-api-${timestamp}.md"
  local token_env="${AIW_PERFORMANCE_TEMP_DIR}/k6.env"
  umask 077
  printf 'K6_ACCESS_TOKEN=%s\n' "${access_token}" >"${token_env}"

  set +e
  PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" run --rm \
    --env-file "${token_env}" \
    -e "K6_BASE_URL=${container_base_url}" \
    -e "K6_START_RATE=${start_rate}" \
    -e "K6_TARGET_RATE=${target_rate}" \
    -e "K6_RAMP_DURATION=${ramp_duration}" \
    -e "K6_STEADY_DURATION=${steady_duration}" \
    -e "K6_RAMP_DOWN_DURATION=${ramp_down_duration}" \
    --mount "type=bind,source=${AIW_REPO_ROOT}/infra/performance,target=/work,readonly" \
    --mount "type=bind,source=${results_dir},target=/results" \
    "${k6_image}" run \
    --summary-trend-stats="avg,min,med,max,p(90),p(95),p(99)" \
    --summary-export="/results/$(basename -- "${summary_path}")" \
    /work/api-read.js
  local k6_status="$?"
  set -e

  if [[ -f "${summary_path}" ]]; then
    performance_tools report \
      --summary "${summary_path}" \
      --output "${report_path}" \
      --git-sha "$(git -C "${AIW_REPO_ROOT}" rev-parse HEAD)" \
      --k6-image "${k6_image}" \
      --k6-exit-code "${k6_status}" \
      --start-rate "${start_rate}" \
      --target-rate "${target_rate}" \
      --duration "${ramp_duration}+${steady_duration}+${ramp_down_duration}" >/dev/null
    printf '本地性能报告: %s\n' "${report_path}"
  else
    printf 'k6 未生成 summary，无法生成标准报告。\n' >&2
    k6_status=1
  fi
  return "${k6_status}"
}

aiw_run_logged "local-api-performance" run_local_api_baseline
