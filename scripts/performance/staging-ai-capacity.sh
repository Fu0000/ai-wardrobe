#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)/common.sh"

readonly k6_image="grafana/k6:2.1.0@sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669"
readonly capacity_confirmation="I_ACCEPT_STAGING_AI_COST_AND_ONCALL_WINDOW"
readonly k6_cost_confirmation="I_ACCEPT_REAL_AI_COST_AND_AUTHORIZED_DATA"
readonly capacity_namespace="ai-wardrobe"
AIW_CAPACITY_API_REPLICAS=""
AIW_CAPACITY_WORKER_REPLICAS=""

capacity_kubectl() {
  kubectl --context "${STAGING_KUBE_CONTEXT}" -n "${capacity_namespace}" "$@"
}

queue_audit_tool() {
  (
    cd "${AIW_REPO_ROOT}/backend"
    uv run python scripts/performance/queue_recovery_audit.py "$@"
  )
}

performance_tools() {
  (
    cd "${AIW_REPO_ROOT}/backend"
    uv run python scripts/performance_tools.py "$@"
  )
}

validate_staging_capacity_target() {
  local current_context
  current_context="$(kubectl config current-context)"
  [[ "${current_context}" == "${STAGING_KUBE_CONTEXT}" ]] ||
    aiw_fail "当前 Kubernetes Context 与 STAGING_KUBE_CONTEXT 不一致。"

  local runtime_environment
  runtime_environment="$(
    capacity_kubectl get configmap ai-wardrobe-config \
      -o jsonpath='{.data.AIW_ENVIRONMENT}'
  )"
  [[ "${runtime_environment}" == "staging" ]] ||
    aiw_fail "目标集群未声明 AIW_ENVIRONMENT=staging，拒绝发起 AI 压测。"

  local ready
  ready="$(
    curl \
      --fail \
      --silent \
      --show-error \
      --max-time 10 \
      "${STAGING_API_BASE_URL%/}/health/ready"
  )" || aiw_fail "Staging API 未就绪。"
  jq -e '.status == "ready" and .environment == "staging"' \
    <<<"${ready}" >/dev/null ||
    aiw_fail "API 必须明确返回 ready 且 environment=staging。"

  local deployment container expected_replicas available image
  for deployment in ai-wardrobe-api ai-wardrobe-worker-fast; do
    if [[ "${deployment}" == "ai-wardrobe-api" ]]; then
      container="api"
    else
      container="worker"
    fi
    image="$(
      capacity_kubectl get "deployment/${deployment}" \
        -o "jsonpath={.spec.template.spec.containers[?(@.name==\"${container}\")].image}"
    )"
    [[ "${image}" == *":${STAGING_EXPECTED_SHA}" ]] ||
      aiw_fail "${deployment} 镜像与 STAGING_EXPECTED_SHA 不一致。"
    expected_replicas="$(
      capacity_kubectl get "deployment/${deployment}" \
        -o jsonpath='{.spec.replicas}'
    )"
    available="$(
      capacity_kubectl get "deployment/${deployment}" \
        -o jsonpath='{.status.availableReplicas}'
    )"
    [[ "${expected_replicas}" =~ ^[1-9][0-9]*$ ]] ||
      aiw_fail "${deployment} 期望副本数无效。"
    [[ "${available:-0}" == "${expected_replicas}" ]] ||
      aiw_fail "${deployment} 未达到全部可用状态。"
    if [[ "${deployment}" == "ai-wardrobe-api" ]]; then
      AIW_CAPACITY_API_REPLICAS="${expected_replicas}"
    else
      AIW_CAPACITY_WORKER_REPLICAS="${expected_replicas}"
    fi
  done
}

run_staging_ai_capacity() {
  aiw_require_command curl
  aiw_require_command jq
  aiw_require_command kubectl
  aiw_require_command uv
  aiw_require_env STAGING_API_BASE_URL
  aiw_require_env STAGING_EXPECTED_SHA
  aiw_require_env STAGING_KUBE_CONTEXT
  aiw_require_env AIW_QUEUE_DATA_FILE
  aiw_require_env AIW_CAPACITY_STAGE
  aiw_require_env AIW_AI_CAPACITY_CONFIRMATION
  [[ "${AIW_AI_CAPACITY_CONFIRMATION}" == "${capacity_confirmation}" ]] ||
    aiw_fail "未确认 Staging AI 成本、授权数据、Dashboard 和 On-call 窗口。"
  [[ "${STAGING_EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] ||
    aiw_fail "STAGING_EXPECTED_SHA 必须是 40 位小写 Commit SHA。"
  case "${AIW_CAPACITY_STAGE}" in
    10 | 30 | 50) ;;
    *) aiw_fail "AIW_CAPACITY_STAGE 只允许 10、30 或 50。" ;;
  esac
  local configured_vus="${K6_VUS:-10}"
  local poll_timeout_seconds="${K6_POLL_TIMEOUT_SECONDS:-90}"
  [[ "${configured_vus}" =~ ^[1-9][0-9]*$ ]] &&
    ((configured_vus <= 50)) ||
    aiw_fail "K6_VUS 必须为 1～50。"
  [[ "${poll_timeout_seconds}" =~ ^[1-9][0-9]*$ ]] &&
    ((poll_timeout_seconds >= 10 && poll_timeout_seconds <= 600)) ||
    aiw_fail "K6_POLL_TIMEOUT_SECONDS 必须为 10～600。"

  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  validate_staging_capacity_target
  queue_audit_tool validate \
    --purpose ai_capacity \
    --expected-size "${AIW_CAPACITY_STAGE}"

  local dataset_dir dataset_path
  dataset_dir="$(cd -- "$(dirname -- "${AIW_QUEUE_DATA_FILE}")" && pwd -P)"
  dataset_path="${dataset_dir}/$(basename -- "${AIW_QUEUE_DATA_FILE}")"
  local results_dir="${AIW_REPO_ROOT}/infra/performance/results"
  mkdir -p "${results_dir}"
  local timestamp
  timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  local summary_path="${results_dir}/staging-ai-${AIW_CAPACITY_STAGE}-${timestamp}.json"
  local report_path="${results_dir}/staging-ai-${AIW_CAPACITY_STAGE}-${timestamp}.md"
  [[ ! -e "${summary_path}" && ! -e "${report_path}" ]] ||
    aiw_fail "本次 AI 容量报告路径已存在。"

  set +e
  PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" run --rm \
    -e "K6_BASE_URL=${STAGING_API_BASE_URL%/}" \
    -e "K6_DATA_FILE=/dataset/ai-jobs.local.json" \
    -e "K6_RUN_ID=stage-${AIW_CAPACITY_STAGE}-${timestamp}" \
    -e "K6_ENABLE_COSTLY_AI_LOAD=${k6_cost_confirmation}" \
    -e "K6_EXPECTED_ENVIRONMENT=staging" \
    -e "K6_VUS=${configured_vus}" \
    -e "K6_POLL_TIMEOUT_SECONDS=${poll_timeout_seconds}" \
    --mount "type=bind,source=${dataset_path},target=/dataset/ai-jobs.local.json,readonly" \
    --mount "type=bind,source=${AIW_REPO_ROOT}/infra/performance/ai-job-capacity.js,target=/work/ai-job-capacity.js,readonly" \
    --mount "type=bind,source=${results_dir},target=/results" \
    "${k6_image}" run \
    --summary-trend-stats="avg,min,med,max,p(90),p(95),p(99)" \
    --summary-export="/results/$(basename -- "${summary_path}")" \
    /work/ai-job-capacity.js
  local k6_status="$?"
  set -e

  if [[ -f "${summary_path}" ]]; then
    set +e
    performance_tools ai-report \
      --summary "${summary_path}" \
      --output "${report_path}" \
      --git-sha "${STAGING_EXPECTED_SHA}" \
      --k6-image "${k6_image}" \
      --k6-exit-code "${k6_status}" \
      --stage "${AIW_CAPACITY_STAGE}" \
      --vus "${configured_vus}" \
      --api-replicas "${AIW_CAPACITY_API_REPLICAS}" \
      --worker-replicas "${AIW_CAPACITY_WORKER_REPLICAS}" >/dev/null
    local report_status="$?"
    set -e
    if [[ -f "${report_path}" ]]; then
      printf 'Staging AI 容量报告: %s\n' "${report_path}"
    else
      printf '标准报告生成失败；原始脱敏 summary: %s\n' "${summary_path}" >&2
    fi
    if [[ "${report_status}" -ne 0 ]]; then
      return "${report_status}"
    fi
  else
    printf 'k6 未生成 summary，无法生成标准报告。\n' >&2
    k6_status=1
  fi
  return "${k6_status}"
}

aiw_run_logged "staging-ai-capacity" run_staging_ai_capacity
