#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

failures=0

report_failure() {
  printf '文档一致性失败: %s\n' "$*" >&2
  failures=$((failures + 1))
}

while IFS= read -r -d "" document; do
  filename="$(basename "${document}")"
  filename_version="$(printf '%s\n' "${filename}" | sed -E 's/.*_V([0-9]+\.[0-9]+)\.md/\1/')"
  if [[ "${filename_version}" == "${filename}" ]]; then
    report_failure "${filename} 缺少 _Vx.y 版本后缀"
    continue
  fi
  title="$(sed -n '1p' "${document}")"
  if [[ "${title}" != *"V${filename_version}"* ]]; then
    report_failure "${filename} 与正文标题版本不一致"
  fi
done < <(
  find "${AIW_REPO_ROOT}/docs" \
    -maxdepth 1 \
    -type f \
    -name "[0-9][0-9]_*.md" \
    -print0
)

wbs_file="${AIW_REPO_ROOT}/docs/13_MVP实现方案与任务进度明细_V1.0.md"
task_ids="$(
  awk -F "|" '
    /^\| [A-Z]+-[0-9]+/ {
      value = $2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      print value
    }
  ' "${wbs_file}"
)"
task_count="$(printf '%s\n' "${task_ids}" | awk 'NF { count += 1 } END { print count + 0 }')"
duplicate_ids="$(printf '%s\n' "${task_ids}" | sort | uniq -d)"

[[ -z "${duplicate_ids}" ]] ||
  report_failure "WBS 存在重复任务 ID: ${duplicate_ids//$'\n'/, }"

declared_total=0
status_total=0
for task_status in DONE IN_REVIEW IN_PROGRESS BLOCKED NOT_STARTED; do
  declared_count="$(
    awk -v target="${task_status}" '
      index($0, "- `" target "`") == 1 {
        if (match($0, /[0-9]+/)) {
          print substr($0, RSTART, RLENGTH)
          exit
        }
      }
    ' "${wbs_file}"
  )"
  table_count="$(
    awk -F "|" -v target="${task_status}" '
      /^\| [A-Z]+-[0-9]+/ {
        value = $(NF - 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (value == target) {
          count += 1
        }
      }
      END { print count + 0 }
    ' "${wbs_file}"
  )"
  [[ -n "${declared_count}" ]] ||
    report_failure "WBS 快照缺少 ${task_status} 声明"
  if [[ "${declared_count:-0}" -ne "${table_count}" ]]; then
    report_failure \
      "WBS ${task_status} 快照为 ${declared_count:-0}，任务表为 ${table_count}"
  fi
  declared_total=$((declared_total + ${declared_count:-0}))
  status_total=$((status_total + table_count))
done

if [[ "${task_count}" -ne "${status_total}" || "${declared_total}" -ne "${status_total}" ]]; then
  report_failure \
    "WBS 总数不一致：任务行 ${task_count}，快照 ${declared_total}，状态 ${status_total}"
fi

architecture_docs=(
  "${AIW_REPO_ROOT}/AGENTS.md"
  "${AIW_REPO_ROOT}/docs/02_技术方案设计_TDD_V1.1.md"
  "${AIW_REPO_ROOT}/docs/04_API与外部集成方案_V1.1.md"
  "${AIW_REPO_ROOT}/docs/11_非功能性需求NFR与SLO_V1.0.md"
  "${wbs_file}"
)

if grep -En 'OwnershipGuard|ScopedRepository|Scoped Repository' "${architecture_docs[@]}"; then
  report_failure "架构文档仍引用不存在的资源归属类名"
fi

if grep -En '`(background|governance)`' \
  "${AIW_REPO_ROOT}/docs/02_技术方案设计_TDD_V1.1.md" \
  "${wbs_file}"; then
  report_failure "P0 文档仍引用过期 Celery 队列"
fi

for queue_name in ai_fast image_generation media_generation maintenance; do
  grep -qF "${queue_name}" "${wbs_file}" ||
    report_failure "WBS 缺少 P0 队列 ${queue_name}"
done

if awk -F "|" '/^\| INF-04 / && /CLS/' "${wbs_file}" | grep -q .; then
  report_failure "INF-04 名称或完成条件仍把未接入的 CLS 计为代码能力"
fi

reference_files=(
  "${AIW_REPO_ROOT}/AGENTS.md"
  "${AIW_REPO_ROOT}/CLAUDE.md"
  "${AIW_REPO_ROOT}/README.md"
)
while IFS= read -r -d "" reference_file; do
  reference_files+=("${reference_file}")
done < <(find "${AIW_REPO_ROOT}/docs" -type f -name "*.md" -print0)

if grep -En '0[0-8]_[^` )]+_V1\.0\.md' "${reference_files[@]}"; then
  report_failure "仍有核心 V1.1 文档使用旧 V1.0 文件名"
fi

if ((failures > 0)); then
  aiw_fail "发现 ${failures} 类文档一致性问题。"
fi

printf '文档检查通过：编号版本一致，WBS %s 项且状态汇总匹配，架构术语已对齐。\n' \
  "${task_count}"
