import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.operations.alert_delivery_drill import (
    REQUIRED_CONFIG_MARKERS,
    AlertDrillError,
    AlertmanagerClient,
    abort_drill,
    acknowledge_drill,
    finish_drill,
    start_drill,
    validate_https_origin,
)


class AlertmanagerStub:
    def __init__(self, *, approved_config: bool = True) -> None:
        self.active: dict[tuple[str, str], dict[str, Any]] = {}
        self.posted: list[dict[str, Any]] = []
        self.approved_config = approved_config

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer alertmanager-secret-token"
        if request.url.path == "/-/ready":
            return httpx.Response(200, text="ready")
        if request.url.path == "/api/v2/status":
            config = "\n".join(REQUIRED_CONFIG_MARKERS) if self.approved_config else "unexpected"
            return httpx.Response(200, json={"config": {"original": config}})
        if request.method == "POST" and request.url.path == "/api/v2/alerts":
            payload = json.loads(request.content)
            assert isinstance(payload, list)
            for alert in payload:
                self.posted.append(alert)
                labels = alert["labels"]
                key = (labels["drill_id"], labels["severity"])
                if alert["endsAt"] == "2099-01-01T00:00:00Z":
                    self.active[key] = alert
                else:
                    self.active.pop(key, None)
            return httpx.Response(202)
        if request.method == "GET" and request.url.path == "/api/v2/alerts":
            return httpx.Response(200, json=list(self.active.values()))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def private_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return directory


def client(stub: AlertmanagerStub) -> AlertmanagerClient:
    return AlertmanagerClient(
        base_url="https://alerts.staging.example.com",
        bearer_token="alertmanager-secret-token",
        transport=httpx.MockTransport(stub.handle),
    )


def test_alert_delivery_drill_requires_safe_origins_and_private_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(AlertDrillError):
        validate_https_origin("http://alerts.staging.example.com")
    with pytest.raises(AlertDrillError):
        validate_https_origin("https://user:password@alerts.staging.example.com")

    unsafe_directory = tmp_path / "unsafe"
    unsafe_directory.mkdir(mode=0o755)
    alertmanager = client(AlertmanagerStub())
    try:
        with pytest.raises(AlertDrillError, match="group or others"):
            start_drill(
                client=alertmanager,
                state_path=unsafe_directory / "state.json",
                runbook_url="https://runbooks.example.com/alerts",
            )
    finally:
        alertmanager.close()


def test_alert_delivery_drill_requires_approved_receiver_contract() -> None:
    alertmanager = client(AlertmanagerStub(approved_config=False))
    try:
        with pytest.raises(AlertDrillError, match="approved receiver"):
            alertmanager.validate_contract()
    finally:
        alertmanager.close()


def test_alert_delivery_drill_records_human_ack_and_no_secrets(
    tmp_path: Path,
) -> None:
    directory = private_directory(tmp_path)
    state_path = directory / "state.json"
    report_path = directory / "report.json"
    stub = AlertmanagerStub()
    alertmanager = client(stub)
    started_at = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    try:
        drill_id = start_drill(
            client=alertmanager,
            state_path=state_path,
            runbook_url="https://runbooks.example.com/alerts",
            now=lambda: started_at,
        )
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
        tokens = {
            alert["labels"]["severity"]: alert["annotations"]["ack_token"]
            for alert in stub.posted
            if alert["endsAt"] == "2099-01-01T00:00:00Z"
        }
        acknowledge_drill(
            state_path=state_path,
            severity="critical",
            ack_token=tokens["critical"],
            responder_id="oncall_primary",
            now=lambda: started_at + timedelta(seconds=30),
        )
        acknowledge_drill(
            state_path=state_path,
            severity="warning",
            ack_token=tokens["warning"],
            responder_id="oncall_primary",
            now=lambda: started_at + timedelta(seconds=60),
        )

        report = finish_drill(
            client=alertmanager,
            state_path=state_path,
            report_path=report_path,
            critical_sla_seconds=300,
            warning_sla_seconds=900,
            now=lambda: started_at + timedelta(seconds=70),
            sleep=lambda _seconds: None,
        )
    finally:
        alertmanager.close()

    assert report["status"] == "PASSED"
    assert report["drill_id"] == drill_id
    assert report["acknowledgements"]["critical"]["acknowledgement_seconds"] == 30
    assert report["acknowledgements"]["warning"]["acknowledgement_seconds"] == 60
    assert not state_path.exists()
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    serialized = report_path.read_text(encoding="utf-8")
    assert "alertmanager-secret-token" not in serialized
    assert "alerts.staging.example.com" not in serialized
    assert all(token not in serialized for token in tokens.values())
    assert not stub.active


def test_alert_delivery_drill_rejects_forged_ack_and_can_abort(
    tmp_path: Path,
) -> None:
    directory = private_directory(tmp_path)
    state_path = directory / "state.json"
    stub = AlertmanagerStub()
    alertmanager = client(stub)
    try:
        start_drill(
            client=alertmanager,
            state_path=state_path,
            runbook_url="https://runbooks.example.com/alerts",
        )
        with pytest.raises(AlertDrillError, match="token is invalid"):
            acknowledge_drill(
                state_path=state_path,
                severity="critical",
                ack_token="forged-ack-token",
                responder_id="oncall_primary",
            )
        abort_drill(client=alertmanager, state_path=state_path)
    finally:
        alertmanager.close()

    assert not state_path.exists()
    assert not stub.active
