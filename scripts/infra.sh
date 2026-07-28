#!/usr/bin/env bash

set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

compose() {
  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  [[ -f "${AIW_REPO_ROOT}/.env" ]] ||
    aiw_fail "缺少 .env；请先执行 cp .env.example .env。"
  PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" compose \
    --env-file "${AIW_REPO_ROOT}/.env" \
    -f "${AIW_REPO_ROOT}/infra/compose.yaml" \
    "$@"
}

infra_up() {
  compose up -d
}

observability_up() {
  compose --profile observability up -d
}

infra_down() {
  compose down
}

run_migrations() {
  aiw_require_command uv
  cd "${AIW_REPO_ROOT}/backend"
  uv run alembic upgrade head
}

terraform_staging_validate() {
  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  local terraform_root="/workspace/infra/terraform/staging"
  local terraform_image="hashicorp/terraform@sha256:7ae513256f7ce67879e218ae8593d6fbe216ec9e123abe6c94e4e10704857963"
  local container_user
  container_user="$(id -u):$(id -g)"

  terraform_container() {
    PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" run --rm \
      --user "${container_user}" \
      --env HOME=/tmp \
      --volume "${AIW_REPO_ROOT}:/workspace" \
      --workdir "${terraform_root}" \
      "${terraform_image}" \
      "$@"
  }

  terraform_container fmt -check -recursive
  terraform_container init \
    -backend=false \
    -input=false \
    -lockfile=readonly \
    -no-color
  terraform_container validate -no-color
  terraform_container test -no-color
}

terraform_staging_audit_state() {
  local confirmation="${AIW_TERRAFORM_AUDIT_CONFIRMATION:-}"
  [[ "${confirmation}" == "I_ACKNOWLEDGE_STAGING_AUDIT_READ_ONLY" ]] ||
    aiw_fail "必须显式设置 AIW_TERRAFORM_AUDIT_CONFIRMATION=I_ACKNOWLEDGE_STAGING_AUDIT_READ_ONLY。"

  local required_env
  for required_env in \
    AIW_TERRAFORM_BACKEND_CONFIG \
    AIW_TERRAFORM_AUDIT_OUTPUT_DIR \
    AIW_TERRAFORM_STATE_ACCESS_KEY_ID \
    AIW_TERRAFORM_STATE_SECRET_ACCESS_KEY; do
    aiw_require_env "${required_env}"
  done

  local backend_config="${AIW_TERRAFORM_BACKEND_CONFIG}"
  local output_dir="${AIW_TERRAFORM_AUDIT_OUTPUT_DIR}"
  [[ -f "${backend_config}" && ! -L "${backend_config}" ]] ||
    aiw_fail "Terraform backend 配置必须是普通文件且不能是符号链接。"
  [[ -d "${output_dir}" && ! -L "${output_dir}" ]] ||
    aiw_fail "Terraform 审计输出目录必须已存在且不能是符号链接。"

  file_mode() {
    local path="$1"
    if stat -f "%Lp" "${path}" >/dev/null 2>&1; then
      stat -f "%Lp" "${path}"
    else
      stat -c "%a" "${path}"
    fi
  }

  local backend_mode
  backend_mode="$(file_mode "${backend_config}")"
  (( (8#${backend_mode} & 8#077) == 0 )) ||
    aiw_fail "敏感 Terraform backend 配置必须禁止 group/other 读取。"
  backend_config="$(
    cd -- "$(dirname -- "${backend_config}")" &&
      printf '%s/%s\n' "$(pwd -P)" "$(basename -- "${backend_config}")"
  )"

  local output_mode
  output_mode="$(file_mode "${output_dir}")"
  (( (8#${output_mode} & 8#077) == 0 )) ||
    aiw_fail "Terraform 审计输出目录必须禁止 group/other 访问。"
  local canonical_output_dir
  canonical_output_dir="$(cd -- "${output_dir}" && pwd -P)"
  case "${canonical_output_dir}/" in
    "${AIW_REPO_ROOT}/"*)
      aiw_fail "Terraform 审计临时证据必须输出到仓库之外。"
      ;;
  esac

  local output_name
  for output_name in \
    compute-contract.json \
    data-contract.json \
    terraform-edge-clb-id.txt \
    terraform-edge-vips.json; do
    [[ ! -e "${canonical_output_dir}/${output_name}" ]] ||
      aiw_fail "Terraform 审计输出已存在，拒绝覆盖：${output_name}"
  done

  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  local terraform_root="/workspace/infra/terraform/staging"
  local terraform_image="hashicorp/terraform@sha256:7ae513256f7ce67879e218ae8593d6fbe216ec9e123abe6c94e4e10704857963"
  local container_user
  container_user="$(id -u):$(id -g)"
  local docker_env=(
    --env "HOME=/tmp"
    --env "TF_DATA_DIR=/tmp/terraform-data"
    --env "AWS_ACCESS_KEY_ID"
    --env "AWS_SECRET_ACCESS_KEY"
  )
  export AWS_ACCESS_KEY_ID="${AIW_TERRAFORM_STATE_ACCESS_KEY_ID}"
  export AWS_SECRET_ACCESS_KEY="${AIW_TERRAFORM_STATE_SECRET_ACCESS_KEY}"
  if [[ -n "${AIW_TERRAFORM_STATE_SESSION_TOKEN:-}" ]]; then
    export AWS_SESSION_TOKEN="${AIW_TERRAFORM_STATE_SESSION_TOKEN}"
    docker_env+=(--env "AWS_SESSION_TOKEN")
  fi

  PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" run --rm \
    --user "${container_user}" \
    "${docker_env[@]}" \
    --volume "${AIW_REPO_ROOT}:/workspace:ro" \
    --volume "${backend_config}:/terraform-input/backend.hcl:ro" \
    --volume "${canonical_output_dir}:/terraform-output" \
    --workdir "${terraform_root}" \
    --entrypoint /bin/sh \
    "${terraform_image}" \
    -ec '
      terraform init \
        -reconfigure \
        -backend-config=/terraform-input/backend.hcl \
        -input=false \
        -lockfile=readonly \
        -no-color
      terraform output -json compute_contract \
        > /terraform-output/compute-contract.json
      terraform output -json data_service_contract \
        > /terraform-output/data-contract.json
      terraform output -raw edge_clb_id \
        > /terraform-output/terraform-edge-clb-id.txt
      terraform output -json edge_clb_vips \
        > /terraform-output/terraform-edge-vips.json
      chmod 600 /terraform-output/*
    '
  chmod 600 \
    "${canonical_output_dir}/compute-contract.json" \
    "${canonical_output_dir}/data-contract.json" \
    "${canonical_output_dir}/terraform-edge-clb-id.txt" \
    "${canonical_output_dir}/terraform-edge-vips.json"
  for output_name in \
    compute-contract.json \
    data-contract.json \
    terraform-edge-clb-id.txt \
    terraform-edge-vips.json; do
    [[ -s "${canonical_output_dir}/${output_name}" ]] ||
      aiw_fail "Terraform State 缺少必需的非敏感审计输出：${output_name}"
  done

  printf 'Terraform Staging State 审计证据已按 0600 写入临时目录；未读取敏感输出，未刷新或修改云资源。\n'
}

terraform_staging_plan() {
  local confirmation="${AIW_TERRAFORM_PLAN_CONFIRMATION:-}"
  [[ "${confirmation}" == "I_ACKNOWLEDGE_STAGING_PLAN_READ_ONLY" ]] ||
    aiw_fail "必须显式设置 AIW_TERRAFORM_PLAN_CONFIRMATION=I_ACKNOWLEDGE_STAGING_PLAN_READ_ONLY。"

  local required_env
  for required_env in \
    AIW_TERRAFORM_BACKEND_CONFIG \
    AIW_TERRAFORM_VAR_FILE \
    AIW_TERRAFORM_PLAN_OUTPUT_DIR \
    TENCENTCLOUD_SECRET_ID \
    TENCENTCLOUD_SECRET_KEY \
    AIW_TERRAFORM_STATE_ACCESS_KEY_ID \
    AIW_TERRAFORM_STATE_SECRET_ACCESS_KEY \
    TF_VAR_postgresql_root_password \
    TF_VAR_redis_password; do
    aiw_require_env "${required_env}"
  done
  [[ "${AIW_TERRAFORM_STATE_ACCESS_KEY_ID}" != "${TENCENTCLOUD_SECRET_ID}" ]] ||
    aiw_fail "远程 State 与资源 Provider 必须使用不同的最小权限身份。"

  local backend_config="${AIW_TERRAFORM_BACKEND_CONFIG}"
  local var_file="${AIW_TERRAFORM_VAR_FILE}"
  local plan_output_dir="${AIW_TERRAFORM_PLAN_OUTPUT_DIR}"
  [[ -f "${backend_config}" && ! -L "${backend_config}" ]] ||
    aiw_fail "Terraform backend 配置必须是普通文件且不能是符号链接。"
  [[ -f "${var_file}" && ! -L "${var_file}" ]] ||
    aiw_fail "Terraform tfvars 必须是普通文件且不能是符号链接。"
  [[ -d "${plan_output_dir}" && ! -L "${plan_output_dir}" ]] ||
    aiw_fail "Terraform Plan 输出目录必须已存在且不能是符号链接。"

  file_mode() {
    local path="$1"
    if stat -f "%Lp" "${path}" >/dev/null 2>&1; then
      stat -f "%Lp" "${path}"
    else
      stat -c "%a" "${path}"
    fi
  }

  local path
  for path in "${backend_config}" "${var_file}"; do
    local mode
    mode="$(file_mode "${path}")"
    (( (8#${mode} & 8#077) == 0 )) ||
      aiw_fail "敏感 Terraform 输入必须禁止 group/other 读取：${path}"
  done
  backend_config="$(
    cd -- "$(dirname -- "${backend_config}")" &&
      printf '%s/%s\n' "$(pwd -P)" "$(basename -- "${backend_config}")"
  )"
  var_file="$(
    cd -- "$(dirname -- "${var_file}")" &&
      printf '%s/%s\n' "$(pwd -P)" "$(basename -- "${var_file}")"
  )"
  local output_mode
  output_mode="$(file_mode "${plan_output_dir}")"
  (( (8#${output_mode} & 8#077) == 0 )) ||
    aiw_fail "Terraform Plan 输出目录必须禁止 group/other 访问。"

  local canonical_output_dir
  canonical_output_dir="$(cd -- "${plan_output_dir}" && pwd -P)"
  case "${canonical_output_dir}/" in
    "${AIW_REPO_ROOT}/"*)
      aiw_fail "Terraform Plan 含敏感值，输出目录必须位于仓库之外。"
      ;;
  esac

  local plan_path="${canonical_output_dir}/staging.tfplan"
  [[ ! -e "${plan_path}" ]] ||
    aiw_fail "Plan 输出已存在；请先人工确认并清理旧文件：${plan_path}"

  local docker_bin
  docker_bin="$(aiw_docker_bin)"
  local docker_bin_dir
  docker_bin_dir="$(dirname -- "${docker_bin}")"
  local terraform_root="/workspace/infra/terraform/staging"
  local terraform_image="hashicorp/terraform@sha256:7ae513256f7ce67879e218ae8593d6fbe216ec9e123abe6c94e4e10704857963"
  local container_user
  container_user="$(id -u):$(id -g)"

  local docker_env=(
    --env "HOME=/tmp"
    --env "TENCENTCLOUD_SECRET_ID"
    --env "TENCENTCLOUD_SECRET_KEY"
    --env "TF_VAR_postgresql_root_password"
    --env "TF_VAR_redis_password"
    --env "AWS_ACCESS_KEY_ID"
    --env "AWS_SECRET_ACCESS_KEY"
  )
  export AWS_ACCESS_KEY_ID="${AIW_TERRAFORM_STATE_ACCESS_KEY_ID}"
  export AWS_SECRET_ACCESS_KEY="${AIW_TERRAFORM_STATE_SECRET_ACCESS_KEY}"
  if [[ -n "${TENCENTCLOUD_SECURITY_TOKEN:-}" ]]; then
    docker_env+=(--env "TENCENTCLOUD_SECURITY_TOKEN")
  fi
  if [[ -n "${AIW_TERRAFORM_STATE_SESSION_TOKEN:-}" ]]; then
    export AWS_SESSION_TOKEN="${AIW_TERRAFORM_STATE_SESSION_TOKEN}"
    docker_env+=(--env "AWS_SESSION_TOKEN")
  fi

  terraform_plan_container() {
    PATH="${docker_bin_dir}:${PATH}" "${docker_bin}" run --rm \
      --user "${container_user}" \
      "${docker_env[@]}" \
      --volume "${AIW_REPO_ROOT}:/workspace" \
      --volume "${backend_config}:/terraform-input/backend.hcl:ro" \
      --volume "${var_file}:/terraform-input/staging.tfvars:ro" \
      --volume "${canonical_output_dir}:/terraform-output" \
      --workdir "${terraform_root}" \
      "${terraform_image}" \
      "$@"
  }

  terraform_plan_container init \
    -reconfigure \
    -backend-config=/terraform-input/backend.hcl \
    -input=false \
    -lockfile=readonly \
    -no-color

  set +e
  terraform_plan_container plan \
    -detailed-exitcode \
    -input=false \
    -lock-timeout=5m \
    -no-color \
    -out=/terraform-output/staging.tfplan \
    -var-file=/terraform-input/staging.tfvars
  local plan_status="$?"
  set -e

  case "${plan_status}" in
    0)
      printf 'Terraform Staging Plan 通过：当前远程状态无变更。\n'
      ;;
    2)
      printf 'Terraform Staging Plan 通过：检测到待审批变更，未执行 Apply。\n'
      ;;
    *)
      aiw_fail "Terraform Staging Plan 失败（exit=${plan_status}）。"
      ;;
  esac

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${plan_path}"
  else
    shasum -a 256 "${plan_path}"
  fi
  printf 'Plan 文件包含敏感值：%s；禁止上传为公开 Artifact。\n' "${plan_path}"
}

case "${1:-}" in
  up)
    aiw_run_logged "infra-up" infra_up
    ;;
  observability-up)
    aiw_run_logged "infra-observability-up" observability_up
    ;;
  down)
    aiw_run_logged "infra-down" infra_down
    ;;
  migrate)
    aiw_run_logged "infra-migrate" run_migrations
    ;;
  terraform-staging-validate)
    aiw_run_logged "terraform-staging-validate" terraform_staging_validate
    ;;
  terraform-staging-audit-state)
    aiw_run_logged "terraform-staging-audit-state" terraform_staging_audit_state
    ;;
  terraform-staging-plan)
    aiw_run_logged "terraform-staging-plan" terraform_staging_plan
    ;;
  *)
    aiw_fail \
      "用法: scripts/infra.sh {up|observability-up|down|migrate|terraform-staging-validate|terraform-staging-audit-state|terraform-staging-plan}"
    ;;
esac
