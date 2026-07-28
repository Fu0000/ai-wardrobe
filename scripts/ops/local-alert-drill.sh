#!/usr/bin/env bash

set -Eeuo pipefail

readonly OPS_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${OPS_SCRIPT_DIR}/../lib/common.sh"

readonly PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
readonly ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://127.0.0.1:9093}"

wait_until_ready() {
  local name="$1"
  local url="$2"
  local attempt
  for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 3 "${url}" >/dev/null 2>&1; then
      printf 'PASS %-28s %s\n' "${name}" "${url}"
      return
    fi
    sleep 2
  done
  aiw_fail "${name} 在 60 秒内未就绪：${url}"
}

assert_contains() {
  local description="$1"
  local content="$2"
  local expected="$3"
  if [[ "${content}" != *"${expected}"* ]]; then
    aiw_fail "${description} 缺少 ${expected}"
  fi
  printf 'PASS %-28s %s\n' "${description}" "${expected}"
}

query_is_one() {
  local metric="$1"
  local payload
  payload="$(
    curl --fail --silent --show-error --max-time 5 \
      --get \
      --data-urlencode "query=${metric}" \
      "${PROMETHEUS_URL}/api/v1/query"
  )"
  assert_contains "${metric} exporter query" "${payload}" '"status":"success"'
  assert_contains "${metric} exporter value" "${payload}" ',"1"]'
}

drill() {
  aiw_require_command curl
  aiw_require_command date
  aiw_require_command seq

  wait_until_ready "Prometheus readiness" "${PROMETHEUS_URL}/-/ready"
  wait_until_ready "Alertmanager readiness" "${ALERTMANAGER_URL}/-/ready"

  local rules
  rules="$(curl --fail --silent --show-error --max-time 5 "${PROMETHEUS_URL}/api/v1/rules")"
  local alert_name
  for alert_name in \
    AIWardrobePostgreSQLUnavailable \
    AIWardrobePostgreSQLConnectionsHigh \
    AIWardrobeRedisUnavailable \
    AIWardrobeRedisConnectionsHigh \
    AIWardrobeRedisMemoryHigh; do
    assert_contains "Prometheus rule loaded" "${rules}" "\"name\":\"${alert_name}\""
  done

  query_is_one "pg_up"
  query_is_one "redis_up"

  local drill_id="local-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
  local now
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  local active_payload
  active_payload="$(
    printf '[{"labels":{"alertname":"AIWardrobeLocalDeliveryDrill","severity":"warning","drill_id":"%s"},"annotations":{"summary":"Local Alertmanager API delivery drill; no external notification is claimed."},"startsAt":"%s","endsAt":"2099-01-01T00:00:00Z"}]' \
      "${drill_id}" \
      "${now}"
  )"
  curl --fail --silent --show-error --max-time 5 \
    -H 'Content-Type: application/json' \
    --data "${active_payload}" \
    "${ALERTMANAGER_URL}/api/v2/alerts" >/dev/null

  local active_alerts
  active_alerts="$(
    curl --fail --silent --show-error --max-time 5 \
      "${ALERTMANAGER_URL}/api/v2/alerts?active=true&silenced=false&inhibited=false&unprocessed=true"
  )"
  assert_contains "Alertmanager delivery" "${active_alerts}" "\"drill_id\":\"${drill_id}\""

  local resolved_payload
  resolved_payload="$(
    printf '[{"labels":{"alertname":"AIWardrobeLocalDeliveryDrill","severity":"warning","drill_id":"%s"},"annotations":{"summary":"Local Alertmanager API delivery drill; no external notification is claimed."},"startsAt":"%s","endsAt":"%s"}]' \
      "${drill_id}" \
      "${now}" \
      "${now}"
  )"
  curl --fail --silent --show-error --max-time 5 \
    -H 'Content-Type: application/json' \
    --data "${resolved_payload}" \
    "${ALERTMANAGER_URL}/api/v2/alerts" >/dev/null

  local attempt
  local unresolved_alerts
  for attempt in $(seq 1 10); do
    unresolved_alerts="$(
      curl --fail --silent --show-error --max-time 5 \
        "${ALERTMANAGER_URL}/api/v2/alerts?active=true&silenced=false&inhibited=false&unprocessed=true"
    )"
    if [[ "${unresolved_alerts}" != *"\"drill_id\":\"${drill_id}\""* ]]; then
      printf 'PASS %-28s %s\n' "Alertmanager resolution" "${drill_id}"
      printf 'RESULT local alert pipeline verified; external On-call delivery remains unverified.\n'
      return
    fi
    sleep 1
  done
  aiw_fail "演练告警未在 10 秒内解除：${drill_id}"
}

aiw_run_logged "local-alert-drill" drill
