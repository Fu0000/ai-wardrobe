#!/usr/bin/env bash

set -Eeuo pipefail

readonly AIW_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly AIW_REPO_ROOT="$(cd -- "${AIW_SCRIPT_DIR}/.." && pwd)"
readonly AIW_LOG_DIR="${AIW_LOG_DIR:-${AIW_REPO_ROOT}/logs}"

aiw_fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

aiw_require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    aiw_fail "缺少命令 '$1'，请先按 README.md 安装开发依赖。"
}

aiw_docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return
  fi

  local desktop_bin="/Applications/Docker.app/Contents/Resources/bin/docker"
  if [[ -x "${desktop_bin}" ]]; then
    printf '%s\n' "${desktop_bin}"
    return
  fi

  aiw_fail "找不到 Docker CLI；请启动 Docker Desktop 或把 docker 加入 PATH。"
}

aiw_require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || aiw_fail "环境变量 ${name} 不能为空。"
}

aiw_run_logged() {
  local label="$1"
  shift

  mkdir -p "${AIW_LOG_DIR}"
  local timestamp
  timestamp="$(date '+%Y%m%dT%H%M%S')"
  local log_file="${AIW_LOG_DIR}/${label}-${timestamp}-$$.log"

  printf '==> %s\n' "${label}"
  printf '==> 日志: %s\n' "${log_file}"

  set +e
  (
    # aiw_run_logged 的调用方必须能依赖 errexit。若直接把 shell 函数放进
    # pipeline，外层的 set +e 会让函数内前置失败被后续成功命令掩盖。
    set -Eeuo pipefail
    "$@"
  ) 2>&1 | tee -a "${log_file}"
  local command_status="${PIPESTATUS[0]}"
  set -e

  if [[ "${command_status}" -ne 0 ]]; then
    printf '==> 失败（exit=%s）: %s\n' "${command_status}" "${label}" >&2
  else
    printf '==> 完成: %s\n' "${label}"
  fi
  return "${command_status}"
}
