#!/usr/bin/env bash

set -Eeuo pipefail

entry_path="${BASH_SOURCE[0]}"
if [[ -L "${entry_path}" ]]; then
  entry_path="$(readlink "${entry_path}")"
fi
entry_dir="$(cd -- "$(dirname -- "${entry_path}")" && pwd)"
source "${entry_dir}/lib/common.sh"

resolve_client_target() {
  local service="$1"
  if [[ "${service}" == "${AIW_BACKUP_PGSERVICE:-}" ]]; then
    client_container="${AIW_LOCAL_SOURCE_CONTAINER}"
  elif [[ "${service}" == "${AIW_RESTORE_PGSERVICE:-}" ]]; then
    client_container="${AIW_LOCAL_RESTORE_CONTAINER}"
  else
    aiw_fail "未知本地 libpq service: ${service}"
  fi

  client_user="$("${AIW_LOCAL_DOCKER_BIN}" exec "${client_container}" printenv POSTGRES_USER)"
  client_database="$(
    "${AIW_LOCAL_DOCKER_BIN}" exec "${client_container}" printenv POSTGRES_DB
  )"
  [[ -n "${client_user}" && -n "${client_database}" ]] ||
    aiw_fail "无法读取临时 PostgreSQL 容器身份。"
}

run_container_client() {
  local client_name="$1"
  shift
  local service=""
  local output_file=""
  local input_file=""
  client_args=()

  while (($# > 0)); do
    case "$1" in
      --dbname)
        [[ $# -ge 2 ]] || aiw_fail "--dbname 缺少参数"
        service="${2#service=}"
        shift 2
        ;;
      --dbname=service=*)
        service="${1#--dbname=service=}"
        shift
        ;;
      --file)
        [[ $# -ge 2 ]] || aiw_fail "--file 缺少参数"
        output_file="$2"
        shift 2
        ;;
      *)
        if [[ "${client_name}" == "pg_restore" && "$1" != -* ]]; then
          input_file="$1"
          shift
          continue
        fi
        client_args+=("$1")
        shift
        ;;
    esac
  done

  [[ -n "${service}" ]] || aiw_fail "${client_name} 缺少 service 数据库参数。"
  resolve_client_target "${service}"

  case "${client_name}" in
    psql)
      exec "${AIW_LOCAL_DOCKER_BIN}" exec -i "${client_container}" \
        psql \
        --username "${client_user}" \
        --dbname "${client_database}" \
        "${client_args[@]}"
      ;;
    pg_dump)
      [[ -n "${output_file}" ]] || aiw_fail "pg_dump 缺少输出文件。"
      "${AIW_LOCAL_DOCKER_BIN}" exec "${client_container}" \
        pg_dump \
        --username "${client_user}" \
        --dbname "${client_database}" \
        "${client_args[@]}" >"${output_file}"
      ;;
    pg_restore)
      [[ -f "${input_file}" ]] ||
        aiw_fail "pg_restore 缺少本地备份文件。"
      "${AIW_LOCAL_DOCKER_BIN}" exec -i "${client_container}" \
        pg_restore \
        --username "${client_user}" \
        --dbname "${client_database}" \
        "${client_args[@]}" <"${input_file}"
      ;;
    *)
      aiw_fail "不支持的 PostgreSQL 客户端: ${client_name}"
      ;;
  esac
}

entry_name="$(basename -- "$0")"
case "${entry_name}" in
  psql | pg_dump | pg_restore)
    run_container_client "${entry_name}" "$@"
    exit
    ;;
esac

run_drill() {
  aiw_require_command uv
  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  [[ -f "${AIW_REPO_ROOT}/.env" ]] ||
    aiw_fail "缺少 .env；请先执行 cp .env.example .env。"

  local source_container
  source_container="$(
    "${docker_bin}" compose \
      --env-file "${AIW_REPO_ROOT}/.env" \
      -f "${AIW_REPO_ROOT}/infra/compose.yaml" \
      ps -q postgres
  )"
  [[ -n "${source_container}" ]] || aiw_fail "本地 PostgreSQL 服务未运行。"

  local source_image
  source_image="$("${docker_bin}" inspect --format "{{.Config.Image}}" "${source_container}")"
  [[ -n "${source_image}" ]] || aiw_fail "无法识别 PostgreSQL 镜像。"

  local restore_container="aiw-restore-drill-$(date '+%Y%m%d%H%M%S')-$$"
  local workspace
  workspace="$(mktemp -d "${TMPDIR:-/tmp}/aiw-restore-drill.XXXXXX")"
  chmod 700 "${workspace}"
  local wrappers="${workspace}/bin"
  local backups="${workspace}/backups"
  local report="${workspace}/restore-report.json"
  mkdir -m 700 "${wrappers}" "${backups}"

  cleanup_local_drill() {
    "${docker_bin}" stop "${restore_container}" >/dev/null 2>&1 || true
    if [[ "${workspace}" == *"/aiw-restore-drill."* && -d "${workspace}" ]]; then
      rm -R -- "${workspace}"
    fi
  }
  trap cleanup_local_drill RETURN

  "${docker_bin}" run \
    --detach \
    --rm \
    --name "${restore_container}" \
    --env POSTGRES_DB=ai_wardrobe_restore_drill \
    --env POSTGRES_USER=aiw_restore \
    --env POSTGRES_PASSWORD=local-drill-only \
    --tmpfs /var/lib/postgresql:rw,noexec,nosuid,size=512m \
    "${source_image}" >/dev/null

  ready=0
  for _ in {1..30}; do
    if "${docker_bin}" exec "${restore_container}" \
      pg_isready --username aiw_restore --dbname ai_wardrobe_restore_drill \
      >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  [[ "${ready}" -eq 1 ]] || aiw_fail "临时恢复数据库未在 30 秒内就绪。"

  ln -s "${AIW_SCRIPT_DIR}/local-db-drill.sh" "${wrappers}/psql"
  ln -s "${AIW_SCRIPT_DIR}/local-db-drill.sh" "${wrappers}/pg_dump"
  ln -s "${AIW_SCRIPT_DIR}/local-db-drill.sh" "${wrappers}/pg_restore"

  export AIW_LOCAL_DOCKER_BIN="${docker_bin}"
  export AIW_LOCAL_SOURCE_CONTAINER="${source_container}"
  export AIW_LOCAL_RESTORE_CONTAINER="${restore_container}"
  export AIW_BACKUP_PGSERVICE="aiw-local-source"
  export AIW_RESTORE_PGSERVICE="aiw-local-restore-drill"
  export AIW_RESTORE_CONFIRM="RESTORE_TO_ISOLATED_DATABASE"

  cd "${AIW_REPO_ROOT}/backend"
  PATH="${wrappers}:${PATH}" uv run python scripts/backup_database.py \
    --output-dir="${backups}"

  backup_files=("${backups}"/*.dump)
  manifest_files=("${backups}"/*.manifest.json)
  [[ "${#backup_files[@]}" -eq 1 && "${#manifest_files[@]}" -eq 1 ]] ||
    aiw_fail "本地备份产物数量异常。"

  PATH="${wrappers}:${PATH}" uv run python scripts/restore_database_drill.py \
    --backup="${backup_files[0]}" \
    --manifest="${manifest_files[0]}" \
    --report="${report}" \
    --rto-seconds=14400 \
    --rpo-seconds=86400
}

aiw_run_logged "local-db-restore-drill" run_drill
