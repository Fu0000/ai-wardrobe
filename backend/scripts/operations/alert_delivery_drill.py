from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

EXPECTED_RECEIVERS = (
    "staging-oncall-critical",
    "staging-oncall-warning",
)
REQUIRED_CONFIG_MARKERS = (
    *EXPECTED_RECEIVERS,
    'environment="staging"',
    "inhibit_rules:",
    "repeat_interval: 1h",
    "repeat_interval: 4h",
    "send_resolved: true",
    "url_file: /etc/alertmanager/secrets/oncall-webhook-url",
)
SEVERITIES = ("critical", "warning")
RESPONDER_PATTERN = re.compile(r"^oncall_[a-z0-9][a-z0-9_-]{5,63}$")


class AlertDrillError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AlertDrillError("drill state contains an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AlertDrillError("drill state contains an invalid timestamp") from error
    if parsed.tzinfo is None:
        raise AlertDrillError("drill state timestamp must be timezone-aware")
    return parsed


def validate_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise AlertDrillError("Alertmanager URL must be a plain HTTPS origin")
    return value.rstrip("/")


def validate_runbook_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise AlertDrillError("runbook URL must be HTTPS without credentials or fragment")
    return value


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _require_private_parent(path: Path) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise AlertDrillError("state/report parent must be an existing directory")
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise AlertDrillError("state/report parent must not be accessible by group or others")


def _read_state(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AlertDrillError("drill state must be an existing regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AlertDrillError("drill state permissions must be 0600")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlertDrillError("drill state is unreadable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AlertDrillError("drill state schema is invalid")
    return payload


def _write_private_json(path: Path, payload: dict[str, Any], *, replace: bool) -> None:
    _require_private_parent(path)
    if not replace and (path.exists() or path.is_symlink()):
        raise AlertDrillError("output path must not already exist")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        if replace:
            temporary.replace(path)
        else:
            os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


class AlertmanagerClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if len(bearer_token) < 16:
            raise AlertDrillError("Alertmanager bearer token is missing or too short")
        self.base_url = validate_https_origin(base_url)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "User-Agent": "ai-wardrobe-alert-delivery-drill/1.0",
            },
            timeout=httpx.Timeout(15),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def validate_contract(self) -> None:
        readiness = self._client.get("/-/ready")
        if readiness.status_code != 200:
            raise AlertDrillError("Alertmanager readiness control failed")
        status = self._client.get("/api/v2/status")
        if status.status_code != 200:
            raise AlertDrillError("Alertmanager status control failed")
        try:
            payload = status.json()
        except json.JSONDecodeError as error:
            raise AlertDrillError("Alertmanager status response is not JSON") from error
        config = payload.get("config") if isinstance(payload, dict) else None
        original = config.get("original") if isinstance(config, dict) else None
        if not isinstance(original, str) or any(
            marker not in original for marker in REQUIRED_CONFIG_MARKERS
        ):
            raise AlertDrillError("Alertmanager does not expose the approved receiver contract")

    def post_alerts(self, alerts: list[dict[str, object]]) -> None:
        response = self._client.post("/api/v2/alerts", json=alerts)
        if response.status_code not in {200, 202}:
            raise AlertDrillError("Alertmanager rejected the synthetic alerts")

    def active_drill_severities(self, drill_id: str) -> set[str]:
        response = self._client.get(
            "/api/v2/alerts",
            params={
                "active": "true",
                "silenced": "false",
                "inhibited": "false",
                "unprocessed": "true",
            },
        )
        if response.status_code != 200:
            raise AlertDrillError("Alertmanager active-alert control failed")
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise AlertDrillError("Alertmanager active-alert response is not JSON") from error
        if not isinstance(payload, list):
            raise AlertDrillError("Alertmanager active-alert response is invalid")
        severities: set[str] = set()
        for alert in payload:
            labels = alert.get("labels") if isinstance(alert, dict) else None
            if not isinstance(labels, dict) or labels.get("drill_id") != drill_id:
                continue
            severity = labels.get("severity")
            if isinstance(severity, str):
                severities.add(severity)
        return severities


def _alert_labels(drill_id: str, severity: str) -> dict[str, str]:
    return {
        "alertname": f"AIWardrobeStaging{severity.title()}DeliveryDrill",
        "cluster": "ai-wardrobe-staging",
        "drill_id": drill_id,
        "environment": "staging",
        "service": "ai-wardrobe",
        "severity": severity,
    }


def _active_alert(
    *,
    drill_id: str,
    severity: str,
    started_at: str,
    runbook_url: str,
    ack_token: str,
) -> dict[str, object]:
    return {
        "labels": _alert_labels(drill_id, severity),
        "annotations": {
            "summary": f"AI Wardrobe Staging {severity} delivery drill",
            "runbook": runbook_url,
            "ack_token": ack_token,
        },
        "startsAt": started_at,
        "endsAt": "2099-01-01T00:00:00Z",
    }


def _resolved_alert(
    *,
    drill_id: str,
    severity: str,
    started_at: str,
    ended_at: str,
) -> dict[str, object]:
    return {
        "labels": _alert_labels(drill_id, severity),
        "annotations": {"summary": "AI Wardrobe Staging delivery drill resolved"},
        "startsAt": started_at,
        "endsAt": ended_at,
    }


def start_drill(
    *,
    client: AlertmanagerClient,
    state_path: Path,
    runbook_url: str,
    now: Callable[[], datetime] = _utc_now,
) -> str:
    _require_private_parent(state_path)
    if state_path.exists() or state_path.is_symlink():
        raise AlertDrillError("drill state path must not already exist")
    client.validate_contract()
    started_at = _timestamp(now())
    drill_id = f"staging-{uuid4().hex}"
    ack_tokens = {severity: secrets.token_urlsafe(24) for severity in SEVERITIES}
    alerts = [
        _active_alert(
            drill_id=drill_id,
            severity=severity,
            started_at=started_at,
            runbook_url=validate_runbook_url(runbook_url),
            ack_token=ack_tokens[severity],
        )
        for severity in SEVERITIES
    ]
    client.post_alerts(alerts)
    try:
        if client.active_drill_severities(drill_id) != set(SEVERITIES):
            raise AlertDrillError("synthetic alerts are not active and routable")
        state: dict[str, Any] = {
            "schema_version": 1,
            "drill_id": drill_id,
            "environment": "staging",
            "alertmanager_origin_sha256": _fingerprint(client.base_url),
            "started_at": started_at,
            "receivers": list(EXPECTED_RECEIVERS),
            "alerts": {
                severity: {
                    "ack_token_sha256": _fingerprint(ack_tokens[severity]),
                    "acknowledged_at": None,
                    "responder_id": None,
                }
                for severity in SEVERITIES
            },
        }
        _write_private_json(state_path, state, replace=False)
    except Exception:
        ended_at = _timestamp(now())
        client.post_alerts(
            [
                _resolved_alert(
                    drill_id=drill_id,
                    severity=severity,
                    started_at=started_at,
                    ended_at=ended_at,
                )
                for severity in SEVERITIES
            ]
        )
        raise
    return drill_id


def acknowledge_drill(
    *,
    state_path: Path,
    severity: str,
    ack_token: str,
    responder_id: str,
    now: Callable[[], datetime] = _utc_now,
) -> None:
    if severity not in SEVERITIES:
        raise AlertDrillError("severity must be critical or warning")
    if not RESPONDER_PATTERN.fullmatch(responder_id):
        raise AlertDrillError("responder ID must be a pseudonymous oncall_* identifier")
    state = _read_state(state_path)
    alerts = state.get("alerts")
    alert = alerts.get(severity) if isinstance(alerts, dict) else None
    if not isinstance(alert, dict):
        raise AlertDrillError("drill state is missing the requested severity")
    expected_digest = alert.get("ack_token_sha256")
    if not isinstance(expected_digest, str) or not hmac.compare_digest(
        expected_digest,
        _fingerprint(ack_token),
    ):
        raise AlertDrillError("acknowledgement token is invalid")
    existing_responder = alert.get("responder_id")
    if existing_responder not in {None, responder_id}:
        raise AlertDrillError("severity was already acknowledged by another responder")
    if alert.get("acknowledged_at") is None:
        alert["acknowledged_at"] = _timestamp(now())
        alert["responder_id"] = responder_id
        _write_private_json(state_path, state, replace=True)


def finish_drill(
    *,
    client: AlertmanagerClient,
    state_path: Path,
    report_path: Path,
    critical_sla_seconds: int,
    warning_sla_seconds: int,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not 30 <= critical_sla_seconds <= 900:
        raise AlertDrillError("critical acknowledgement SLA must be 30-900 seconds")
    if not critical_sla_seconds <= warning_sla_seconds <= 3_600:
        raise AlertDrillError("warning acknowledgement SLA must cover critical and be <=3600s")
    state = _read_state(state_path)
    if state.get("alertmanager_origin_sha256") != _fingerprint(client.base_url):
        raise AlertDrillError("finish must use the same Alertmanager origin")
    client.validate_contract()
    drill_id = state.get("drill_id")
    started_at_raw = state.get("started_at")
    if not isinstance(drill_id, str) or not isinstance(started_at_raw, str):
        raise AlertDrillError("drill state identifiers are invalid")
    started_at = _parse_timestamp(started_at_raw)
    alerts = state.get("alerts")
    if not isinstance(alerts, dict):
        raise AlertDrillError("drill state alerts are invalid")

    acknowledgements: dict[str, dict[str, object]] = {}
    for severity, sla_seconds in (
        ("critical", critical_sla_seconds),
        ("warning", warning_sla_seconds),
    ):
        alert = alerts.get(severity)
        if not isinstance(alert, dict):
            raise AlertDrillError("drill state is missing an alert")
        acknowledged_at = _parse_timestamp(alert.get("acknowledged_at"))
        responder_id = alert.get("responder_id")
        if not isinstance(responder_id, str):
            raise AlertDrillError("both alerts require human acknowledgement")
        duration_seconds = round((acknowledged_at - started_at).total_seconds(), 3)
        if duration_seconds < 0 or duration_seconds > sla_seconds:
            raise AlertDrillError(f"{severity} acknowledgement exceeded its SLA")
        acknowledgements[severity] = {
            "acknowledgement_seconds": duration_seconds,
            "responder_id": responder_id,
            "sla_seconds": sla_seconds,
        }

    if client.active_drill_severities(drill_id) != set(SEVERITIES):
        raise AlertDrillError("both synthetic alerts must remain active before resolution")
    ended_at = _timestamp(now())
    client.post_alerts(
        [
            _resolved_alert(
                drill_id=drill_id,
                severity=severity,
                started_at=started_at_raw,
                ended_at=ended_at,
            )
            for severity in SEVERITIES
        ]
    )
    for _attempt in range(10):
        if not client.active_drill_severities(drill_id):
            break
        sleep(1)
    else:
        raise AlertDrillError("synthetic alerts did not resolve within 10 seconds")

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "PASSED",
        "environment": "staging",
        "drill_id": drill_id,
        "started_at": started_at_raw,
        "resolved_at": ended_at,
        "receivers": list(EXPECTED_RECEIVERS),
        "acknowledgements": acknowledgements,
        "checks": {
            "alertmanager_ready": True,
            "approved_receivers_loaded": True,
            "critical_and_warning_routable": True,
            "human_acknowledgements_within_sla": True,
            "resolved_notifications_enabled": True,
        },
    }
    _write_private_json(report_path, report, replace=False)
    state_path.unlink()
    return report


def abort_drill(
    *,
    client: AlertmanagerClient,
    state_path: Path,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    state = _read_state(state_path)
    if state.get("alertmanager_origin_sha256") != _fingerprint(client.base_url):
        raise AlertDrillError("abort must use the same Alertmanager origin")
    client.validate_contract()
    drill_id = state.get("drill_id")
    started_at = state.get("started_at")
    if not isinstance(drill_id, str) or not isinstance(started_at, str):
        raise AlertDrillError("drill state identifiers are invalid")
    client.post_alerts(
        [
            _resolved_alert(
                drill_id=drill_id,
                severity=severity,
                started_at=started_at,
                ended_at=_timestamp(now()),
            )
            for severity in SEVERITIES
        ]
    )
    for _attempt in range(10):
        if not client.active_drill_severities(drill_id):
            break
        sleep(1)
    else:
        raise AlertDrillError("synthetic alerts did not resolve within 10 seconds")
    state_path.unlink()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AlertDrillError(f"set {name}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Staging On-call delivery drill.")
    parser.add_argument("command", choices=["start", "ack", "finish", "abort"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--severity", choices=list(SEVERITIES))
    parser.add_argument("--critical-sla-seconds", type=int, default=300)
    parser.add_argument("--warning-sla-seconds", type=int, default=900)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "ack":
            if args.severity is None:
                raise AlertDrillError("--severity is required for ack")
            acknowledge_drill(
                state_path=args.state,
                severity=args.severity,
                ack_token=_required_env("AIW_ALERT_DRILL_ACK_TOKEN"),
                responder_id=_required_env("AIW_ALERT_DRILL_RESPONDER_ID"),
            )
            print(json.dumps({"status": "ACKNOWLEDGED", "severity": args.severity}))
            return 0

        client = AlertmanagerClient(
            base_url=_required_env("STAGING_ALERTMANAGER_URL"),
            bearer_token=_required_env("AIW_STAGING_ALERTMANAGER_TOKEN"),
        )
        try:
            if args.command == "start":
                drill_id = start_drill(
                    client=client,
                    state_path=args.state,
                    runbook_url=_required_env("STAGING_ALERT_RUNBOOK_URL"),
                )
                print(json.dumps({"status": "STARTED", "drill_id": drill_id}))
                return 0
            if args.command == "abort":
                abort_drill(client=client, state_path=args.state)
                print(json.dumps({"status": "ABORTED"}))
                return 0
            if args.report is None:
                raise AlertDrillError("--report is required for finish")
            report = finish_drill(
                client=client,
                state_path=args.state,
                report_path=args.report,
                critical_sla_seconds=args.critical_sla_seconds,
                warning_sla_seconds=args.warning_sla_seconds,
            )
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "report": str(args.report),
                    }
                )
            )
            return 0
        finally:
            client.close()
    except (AlertDrillError, httpx.HTTPError, OSError) as error:
        raise SystemExit(f"staging alert delivery drill failed: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
