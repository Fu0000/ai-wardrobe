from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from scripts.event_funnel_audit import (
    CORE_EVENTS,
    AuditEvent,
    AuditOptions,
    _database_url,
    build_audit_report,
)


def event(
    name: str,
    *,
    user_hash: str = "a" * 64,
    properties: dict[str, object] | None = None,
    trace_id: str = "b" * 32,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        event_name=name,
        event_version=1,
        occurred_at=datetime(2026, 7, 28, 8, tzinfo=UTC),
        environment="staging",
        trace_id=trace_id,
        request_id=uuid4().hex,
        user_id_hash=user_hash,
        session_id=None,
        client_version=None,
        platform="server",
        app_channel="api",
        properties=properties or {},
    )


def options(
    *,
    require_complete: bool = True,
    require_correlated_context: bool = True,
) -> AuditOptions:
    return AuditOptions(
        environment="staging",
        window_hours=24,
        require_complete=require_complete,
        require_correlated_context=require_correlated_context,
    )


def test_audit_contract_covers_all_twenty_documented_events() -> None:
    assert len(CORE_EVENTS) == 20
    assert len(set(CORE_EVENTS)) == len(CORE_EVENTS)

    report = build_audit_report(
        [event(name) for name in CORE_EVENTS],
        options=options(),
        generated_at=datetime(2026, 7, 28, 9, tzinfo=UTC),
    )

    assert report["status"] == "PASSED"
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["covered_core_event_count"] == 20
    assert summary["missing_core_events"] == []
    assert report["context_violations"] == []
    assert report["privacy_violations"] == []
    funnels = report["funnels"]
    assert isinstance(funnels, dict)
    assert all(len(steps) == 5 for steps in funnels.values())


def test_audit_reports_missing_events_without_failing_local_baseline() -> None:
    report = build_audit_report(
        [],
        options=options(
            require_complete=False,
            require_correlated_context=False,
        ),
    )

    assert report["status"] == "BASELINE_NO_DATA"
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["missing_core_events"] == list(CORE_EVENTS)
    assert summary["failures"] == []


def test_audit_fails_closed_on_context_and_prohibited_data() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    unsafe = event(
        "auth.wechat.succeeded",
        trace_id="unavailable",
        properties={
            "open_id": str(user_id),
            "nested": {"photo": "private/user/source.jpg"},
        },
    )

    report = build_audit_report(
        [unsafe],
        options=options(),
    )

    assert report["status"] == "FAILED"
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert set(summary["failures"]) == {
        "COMMON_CONTEXT_INVALID",
        "PROHIBITED_DATA_DETECTED",
        "CORE_EVENT_COVERAGE_INCOMPLETE",
    }
    assert report["context_violations"] == [
        {
            "event_id": str(unsafe.event_id),
            "event_name": unsafe.event_name,
            "failed_fields": ["trace_id_uncorrelated"],
        }
    ]
    assert report["privacy_violations"] == [
        {
            "event_id": str(unsafe.event_id),
            "event_name": unsafe.event_name,
            "property_paths": [
                "properties.nested.photo",
                "properties.open_id",
            ],
        }
    ]


def test_database_url_keeps_password_when_switching_sync_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIW_EVENT_AUDIT_DATABASE_URL", raising=False)
    url = _database_url(
        Settings(
            database_url=("postgresql+asyncpg://audit-user:audit-password@localhost:5432/audit")
        )
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.password == "audit-password"
