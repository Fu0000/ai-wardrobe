#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" && pwd)/common.sh"

readonly queue_confirmation="I_ACCEPT_STAGING_WORKER_PAUSE_AND_REAL_AI_COST"
readonly queue_namespace="ai-wardrobe"
readonly queue_deployment="ai-wardrobe-worker-fast"
AIW_QUEUE_TEMP_DIR=""
AIW_QUEUE_STATE_FILE=""
AIW_QUEUE_ORIGINAL_REPLICAS=""
AIW_QUEUE_WORKER_SCALED="false"

queue_audit_tool() {
  (
    cd "${AIW_REPO_ROOT}/backend"
    uv run python scripts/performance/queue_recovery_audit.py "$@"
  )
}

queue_kubectl() {
  kubectl --context "${STAGING_KUBE_CONTEXT}" -n "${queue_namespace}" "$@"
}

wait_for_staging_api() {
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
}

restore_fast_worker() {
  [[ "${AIW_QUEUE_WORKER_SCALED}" == "true" ]] || return 0
  export AIW_QUEUE_RESTORE_STARTED_AT
  AIW_QUEUE_RESTORE_STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  queue_kubectl scale \
    "deployment/${queue_deployment}" \
    --replicas="${AIW_QUEUE_ORIGINAL_REPLICAS}" || return 1
  queue_kubectl rollout status \
    "deployment/${queue_deployment}" \
    --timeout=5m || return 1
  local available
  available="$(
    queue_kubectl get "deployment/${queue_deployment}" \
      -o jsonpath='{.status.availableReplicas}'
  )" || return 1
  if [[ "${available:-0}" != "${AIW_QUEUE_ORIGINAL_REPLICAS}" ]]; then
    printf 'ERROR: ai_fast Worker 未恢复到原副本数。\n' >&2
    return 1
  fi
  AIW_QUEUE_WORKER_SCALED="false"
}

cleanup_queue_recovery() {
  local command_status="$?"
  local restore_status=0
  trap - EXIT INT TERM
  if [[ "${AIW_QUEUE_WORKER_SCALED}" == "true" ]]; then
    restore_fast_worker || restore_status="$?"
  fi
  if [[ "${AIW_QUEUE_TEMP_DIR}" == "${TMPDIR:-/tmp}"/aiw-queue-recovery.* ]]; then
    rm -rf -- "${AIW_QUEUE_TEMP_DIR}"
  fi
  if [[ "${restore_status}" -ne 0 ]]; then
    printf 'ERROR: ai_fast Worker 自动恢复失败，必须立即人工介入。\n' >&2
    command_status="${restore_status}"
  fi
  exit "${command_status}"
}

run_staging_queue_recovery() {
  aiw_require_command curl
  aiw_require_command jq
  aiw_require_command kubectl
  aiw_require_command uv
  aiw_require_env STAGING_API_BASE_URL
  aiw_require_env STAGING_EXPECTED_SHA
  aiw_require_env STAGING_KUBE_CONTEXT
  aiw_require_env AIW_QUEUE_DATA_FILE
  aiw_require_env AIW_QUEUE_RECOVERY_CONFIRMATION
  [[ "${AIW_QUEUE_RECOVERY_CONFIRMATION}" == "${queue_confirmation}" ]] ||
    aiw_fail "未确认 Staging Worker 暂停、真实 AI 成本和授权数据。"
  [[ "${STAGING_EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] ||
    aiw_fail "STAGING_EXPECTED_SHA 必须是 40 位小写 Commit SHA。"

  local current_context
  current_context="$(kubectl config current-context)"
  [[ "${current_context}" == "${STAGING_KUBE_CONTEXT}" ]] ||
    aiw_fail "当前 Kubernetes Context 与 STAGING_KUBE_CONTEXT 不一致。"
  [[ "$(queue_kubectl auth can-i update deployments.apps --subresource=scale)" == "yes" ]] ||
    aiw_fail "当前 Staging 身份无权缩放 Worker。"

  local runtime_environment
  runtime_environment="$(
    queue_kubectl get configmap ai-wardrobe-config \
      -o jsonpath='{.data.AIW_ENVIRONMENT}'
  )"
  [[ "${runtime_environment}" == "staging" ]] ||
    aiw_fail "目标集群未声明 AIW_ENVIRONMENT=staging，拒绝停 Worker。"
  wait_for_staging_api

  local worker_image
  worker_image="$(
    queue_kubectl get "deployment/${queue_deployment}" \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="worker")].image}'
  )"
  [[ "${worker_image}" == *":${STAGING_EXPECTED_SHA}" ]] ||
    aiw_fail "ai_fast Worker 镜像与 STAGING_EXPECTED_SHA 不一致。"
  local api_image
  api_image="$(
    queue_kubectl get deployment/ai-wardrobe-api \
      -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}'
  )"
  [[ "${api_image}" == *":${STAGING_EXPECTED_SHA}" ]] ||
    aiw_fail "API 镜像与 STAGING_EXPECTED_SHA 不一致。"
  queue_audit_tool validate

  AIW_QUEUE_ORIGINAL_REPLICAS="$(
    queue_kubectl get "deployment/${queue_deployment}" \
      -o jsonpath='{.spec.replicas}'
  )"
  [[ "${AIW_QUEUE_ORIGINAL_REPLICAS}" =~ ^[1-9][0-9]*$ ]] ||
    aiw_fail "ai_fast Worker 原副本数无效。"
  ((AIW_QUEUE_ORIGINAL_REPLICAS <= 10)) ||
    aiw_fail "ai_fast Worker 原副本数超过演练安全上限。"
  local available
  available="$(
    queue_kubectl get "deployment/${queue_deployment}" \
      -o jsonpath='{.status.availableReplicas}'
  )"
  [[ "${available:-0}" == "${AIW_QUEUE_ORIGINAL_REPLICAS}" ]] ||
    aiw_fail "演练前 ai_fast Worker 未全部可用。"
  export AIW_QUEUE_ORIGINAL_REPLICAS

  AIW_QUEUE_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiw-queue-recovery.XXXXXX")"
  AIW_QUEUE_STATE_FILE="${AIW_QUEUE_TEMP_DIR}/state.json"
  trap cleanup_queue_recovery EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  AIW_QUEUE_WORKER_SCALED="true"
  queue_kubectl scale "deployment/${queue_deployment}" --replicas=0
  queue_kubectl rollout status "deployment/${queue_deployment}" --timeout=5m
  available="$(
    queue_kubectl get "deployment/${queue_deployment}" \
      -o jsonpath='{.status.availableReplicas}'
  )"
  [[ "${available:-0}" == "0" ]] ||
    aiw_fail "ai_fast Worker 未完全停止。"
  local remaining_pods
  remaining_pods="$(
    queue_kubectl get pods \
      -l 'app.kubernetes.io/name=ai-wardrobe-worker-fast' \
      -o json |
      jq '.items | length'
  )"
  [[ "${remaining_pods}" == "0" ]] ||
    aiw_fail "仍有 ai_fast Worker Pod 存活，拒绝创建演练任务。"

  queue_audit_tool create --state "${AIW_QUEUE_STATE_FILE}"
  restore_fast_worker

  local results_dir="${AIW_REPO_ROOT}/infra/performance/results"
  local timestamp
  timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  local report_path="${AIW_QUEUE_REPORT_PATH:-${results_dir}/staging-queue-recovery-${timestamp}.json}"
  queue_audit_tool verify \
    --state "${AIW_QUEUE_STATE_FILE}" \
    --report "${report_path}"
  printf 'Staging 队列恢复报告: %s\n' "${report_path}"

  trap - EXIT INT TERM
  cleanup_queue_recovery
}

aiw_run_logged "staging-queue-recovery" run_staging_queue_recovery
