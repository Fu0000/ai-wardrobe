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
  *)
    aiw_fail \
      "用法: scripts/infra.sh {up|observability-up|down|migrate|terraform-staging-validate}"
    ;;
esac
