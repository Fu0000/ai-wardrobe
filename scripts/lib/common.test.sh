#!/usr/bin/env bash

set -Eeuo pipefail

readonly AIW_COMMON_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiw-common-test.XXXXXX")"
trap 'rm -rf -- "${AIW_COMMON_TEST_DIR}"' EXIT
export AIW_LOG_DIR="${AIW_COMMON_TEST_DIR}/logs"

readonly AIW_COMMON_PATH="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  pwd
)/common.sh"

set +e
bash -c '
  set -Eeuo pipefail
  source "$1"
  fail_then_continue() {
    false
    printf "MASKED_FAILURE\n"
  }
  aiw_run_logged "errexit-self-test" fail_then_continue
' _ "${AIW_COMMON_PATH}" >"${AIW_COMMON_TEST_DIR}/output.log" 2>&1
readonly AIW_COMMON_TEST_STATUS="$?"
set -e

if [[ "${AIW_COMMON_TEST_STATUS}" -ne 1 ]]; then
  printf 'ERROR: aiw_run_logged 未传播失败状态（actual=%s）\n' \
    "${AIW_COMMON_TEST_STATUS}" >&2
  exit 1
fi

if [[ "$(<"${AIW_COMMON_TEST_DIR}/output.log")" == *"MASKED_FAILURE"* ]]; then
  printf 'ERROR: aiw_run_logged 在失败后继续执行了函数。\n' >&2
  exit 1
fi

printf 'Shell 日志包装器故障注入通过：前置失败未被掩盖。\n'
