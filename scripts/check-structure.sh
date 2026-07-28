#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

readonly max_files="${AIW_MAX_FILES_PER_DIRECTORY:-8}"
[[ "${max_files}" =~ ^[1-9][0-9]*$ ]] ||
  aiw_fail "AIW_MAX_FILES_PER_DIRECTORY 必须是正整数。"

scope_roots=(
  "${AIW_REPO_ROOT}/backend/app"
  "${AIW_REPO_ROOT}/backend/tests"
  "${AIW_REPO_ROOT}/backend/scripts"
  "${AIW_REPO_ROOT}/miniapp/src"
  "${AIW_REPO_ROOT}/scripts"
  "${AIW_REPO_ROOT}/infra"
  "${AIW_REPO_ROOT}/evals"
)

checked_count=0
violation_count=0
while IFS= read -r -d "" directory; do
  file_count="$(
    find "${directory}" \
      -maxdepth 1 \
      -type f \
      ! -name ".DS_Store" \
      ! -name "*.pyc" \
      -print0 |
      awk 'BEGIN { RS = "\0" } { count += 1 } END { print count + 0 }'
  )"
  checked_count=$((checked_count + 1))
  if ((file_count > max_files)); then
    relative_path="${directory#"${AIW_REPO_ROOT}/"}"
    printf '目录文件超限: %s（%s > %s）\n' \
      "${relative_path}" \
      "${file_count}" \
      "${max_files}" >&2
    violation_count=$((violation_count + 1))
  fi
done < <(
  find "${scope_roots[@]}" \
    -type d \
    \( \
      -name __pycache__ \
      -o -name node_modules \
      -o -name dist \
      -o -name results \
      -o -name .pytest_cache \
      -o -name .mypy_cache \
      -o -name .ruff_cache \
    \) \
    -prune \
    -o -type d -print0
)

if ((violation_count > 0)); then
  aiw_fail "发现 ${violation_count} 个目录超过 ${max_files} 个直接文件。"
fi

printf '结构检查通过：%s 个工程目录均不超过 %s 个直接文件。\n' \
  "${checked_count}" \
  "${max_files}"
