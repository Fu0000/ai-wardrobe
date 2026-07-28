import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.modules.growth.models import UserEvent

CORE_EVENTS = (
    "auth.wechat.succeeded",
    "consent.ai.accepted",
    "consent.ai.revoked",
    "asset.upload.started",
    "asset.upload.interrupted",
    "asset.upload.completed",
    "diagnosis.job.created",
    "diagnosis.text.completed",
    "diagnosis.result.viewed",
    "diagnosis.optimization.clicked",
    "optimization.job.created",
    "optimization.result.completed",
    "optimization.before_after.viewed",
    "share.asset.created",
    "share.wechat.invoked",
    "share.scene.opened",
    "vote.choice.submitted",
    "growth.continue.clicked",
    "privacy.deletion.requested",
    "privacy.deletion.completed",
)

FUNNELS = {
    "first_value": (
        "auth.wechat.succeeded",
        "asset.upload.completed",
        "diagnosis.job.created",
        "diagnosis.text.completed",
        "diagnosis.result.viewed",
    ),
    "optimization": (
        "diagnosis.result.viewed",
        "diagnosis.optimization.clicked",
        "optimization.job.created",
        "optimization.result.completed",
        "optimization.before_after.viewed",
    ),
    "growth": (
        "optimization.before_after.viewed",
        "share.wechat.invoked",
        "share.scene.opened",
        "vote.choice.submitted",
        "growth.continue.clicked",
    ),
}

FORBIDDEN_PROPERTY_KEYS = {
    "openid",
    "open_id",
    "unionid",
    "union_id",
    "prompt",
    "prompt_text",
    "recognized_text",
    "image_text",
    "free_text",
    "photo_url",
    "image_url",
    "object_key",
}
FORBIDDEN_VALUE_MARKERS = (
    "cos-private://",
    "data:image/",
    "/private/",
    "private/",
)
UNCORRELATED_VALUES = {"", "unknown", "unavailable"}


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    event_name: str
    event_version: int
    occurred_at: datetime
    environment: str
    trace_id: str
    request_id: str
    user_id_hash: str | None
    session_id: str | None
    client_version: str | None
    platform: str
    app_channel: str
    properties: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AuditOptions:
    environment: str
    window_hours: int
    require_complete: bool
    require_correlated_context: bool


def _privacy_paths(
    value: object,
    *,
    path: str = "properties",
) -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_PROPERTY_KEYS:
                violations.append(child_path)
            violations.extend(_privacy_paths(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(_privacy_paths(child, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        normalized = value.lower()
        if any(marker in normalized for marker in FORBIDDEN_VALUE_MARKERS):
            violations.append(path)
    return violations


def _context_failures(
    event: AuditEvent,
    *,
    require_correlated_context: bool,
) -> list[str]:
    failures: list[str] = []
    if event.event_version < 1:
        failures.append("event_version")
    if not event.environment:
        failures.append("environment")
    if not event.trace_id:
        failures.append("trace_id")
    if not event.request_id:
        failures.append("request_id")
    if event.user_id_hash is None or len(event.user_id_hash) != 64:
        failures.append("user_id_hash")
    if not event.platform:
        failures.append("platform")
    if not event.app_channel:
        failures.append("app_channel")
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
        failures.append("occurred_at_timezone")
    if require_correlated_context:
        if event.trace_id.lower() in UNCORRELATED_VALUES:
            failures.append("trace_id_uncorrelated")
        if event.request_id.lower() in UNCORRELATED_VALUES:
            failures.append("request_id_uncorrelated")
    return failures


def _step_report(
    events_by_name: Mapping[str, list[AuditEvent]],
    steps: tuple[str, ...],
) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    previous_subjects: int | None = None
    for event_name in steps:
        step_events = events_by_name.get(event_name, [])
        subjects = {event.user_id_hash for event in step_events if event.user_id_hash is not None}
        unique_subjects = len(subjects)
        conversion = (
            round(unique_subjects / previous_subjects * 100, 2) if previous_subjects else None
        )
        report.append(
            {
                "event_name": event_name,
                "event_count": len(step_events),
                "unique_subjects": unique_subjects,
                "subject_conversion_from_previous_percent": conversion,
            }
        )
        previous_subjects = unique_subjects
    return report


def build_audit_report(
    events: list[AuditEvent],
    *,
    options: AuditOptions,
    environment_counts: Mapping[str, int] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    events_by_name: dict[str, list[AuditEvent]] = defaultdict(list)
    for event in events:
        events_by_name[event.event_name].append(event)

    missing_events = [event_name for event_name in CORE_EVENTS if not events_by_name[event_name]]
    context_violations: list[dict[str, object]] = []
    privacy_violations: list[dict[str, object]] = []
    for event in events:
        failed_fields = _context_failures(
            event,
            require_correlated_context=options.require_correlated_context,
        )
        if event.environment != options.environment:
            failed_fields.append("environment_mismatch")
        if failed_fields:
            context_violations.append(
                {
                    "event_id": str(event.event_id),
                    "event_name": event.event_name,
                    "failed_fields": sorted(failed_fields),
                }
            )
        leaked_paths = sorted(set(_privacy_paths(event.properties)))
        if leaked_paths:
            privacy_violations.append(
                {
                    "event_id": str(event.event_id),
                    "event_name": event.event_name,
                    "property_paths": leaked_paths,
                }
            )

    failures: list[str] = []
    if context_violations:
        failures.append("COMMON_CONTEXT_INVALID")
    if privacy_violations:
        failures.append("PROHIBITED_DATA_DETECTED")
    if options.require_complete and missing_events:
        failures.append("CORE_EVENT_COVERAGE_INCOMPLETE")

    if failures:
        status = "FAILED"
    elif not events:
        status = "BASELINE_NO_DATA"
    elif missing_events:
        status = "PARTIAL"
    else:
        status = "PASSED"

    event_counts = Counter(event.event_name for event in events)
    now = generated_at or datetime.now(UTC)
    return {
        "schema_version": 1,
        "status": status,
        "generated_at": now.isoformat(),
        "window": {
            "hours": options.window_hours,
            "environment": options.environment,
        },
        "requirements": {
            "require_complete": options.require_complete,
            "require_correlated_context": options.require_correlated_context,
        },
        "summary": {
            "event_count": len(events),
            "covered_core_event_count": len(CORE_EVENTS) - len(missing_events),
            "required_core_event_count": len(CORE_EVENTS),
            "missing_core_events": missing_events,
            "failures": failures,
        },
        "environment_counts": dict(sorted((environment_counts or {}).items())),
        "event_counts": {event_name: event_counts.get(event_name, 0) for event_name in CORE_EVENTS},
        "funnels": {name: _step_report(events_by_name, steps) for name, steps in FUNNELS.items()},
        "context_violations": context_violations,
        "privacy_violations": privacy_violations,
        "notes": [
            "Growth 漏斗跨分享者与好友，subject 转化率是聚合观察值，不代表同一用户序列。",
            "报告仅输出违规字段路径，不回显疑似敏感值。",
        ],
    }


def _database_url(settings: Settings) -> URL:
    override = os.environ.get("AIW_EVENT_AUDIT_DATABASE_URL")
    raw_url = override or settings.database_url
    # URL.__str__ 会把密码替换为 "***"；必须把 URL 对象直接交给 SQLAlchemy，
    # 否则会真的拿三个星号连接数据库。
    return make_url(raw_url).set(drivername="postgresql+psycopg")


def load_events(
    settings: Settings,
    *,
    options: AuditOptions,
    now: datetime | None = None,
) -> tuple[list[AuditEvent], dict[str, int]]:
    ended_at = now or datetime.now(UTC)
    started_at = ended_at - timedelta(hours=options.window_hours)
    engine = create_engine(_database_url(settings), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    UserEvent.id,
                    UserEvent.event_name,
                    UserEvent.event_version,
                    UserEvent.occurred_at,
                    UserEvent.environment,
                    UserEvent.trace_id,
                    UserEvent.request_id,
                    UserEvent.user_id_hash,
                    UserEvent.session_id,
                    UserEvent.client_version,
                    UserEvent.platform,
                    UserEvent.app_channel,
                    UserEvent.properties,
                ).where(
                    UserEvent.occurred_at >= started_at,
                    UserEvent.occurred_at <= ended_at,
                    UserEvent.environment == options.environment,
                )
            ).mappings()
            events = [
                AuditEvent(
                    event_id=row["id"],
                    event_name=row["event_name"],
                    event_version=row["event_version"],
                    occurred_at=row["occurred_at"],
                    environment=row["environment"],
                    trace_id=row["trace_id"],
                    request_id=row["request_id"],
                    user_id_hash=row["user_id_hash"],
                    session_id=row["session_id"],
                    client_version=row["client_version"],
                    platform=row["platform"],
                    app_channel=row["app_channel"],
                    properties=row["properties"],
                )
                for row in rows
            ]
            environment_counts = {
                environment: int(count)
                for environment, count in connection.execute(
                    select(UserEvent.environment, func.count(UserEvent.id))
                    .where(
                        UserEvent.occurred_at >= started_at,
                        UserEvent.occurred_at <= ended_at,
                    )
                    .group_by(UserEvent.environment)
                )
            }
    finally:
        engine.dispose()
    return events, environment_counts


def _write_report(report: dict[str, object], path: Path | None) -> None:
    content = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if path is None:
        sys.stdout.write(content)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit MVP event coverage, privacy fields, and three product funnels.",
    )
    parser.add_argument("--environment")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--report")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-correlated-context", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.window_hours <= 0:
        raise SystemExit("--window-hours must be positive")
    settings = Settings()
    options = AuditOptions(
        environment=args.environment or settings.environment,
        window_hours=args.window_hours,
        require_complete=args.require_complete,
        require_correlated_context=args.require_correlated_context,
    )
    try:
        events, environment_counts = load_events(settings, options=options)
    except SQLAlchemyError:
        sys.stderr.write(
            json.dumps(
                {"status": "FAILED", "error": "EVENT_AUDIT_DATABASE_UNAVAILABLE"},
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2
    report = build_audit_report(
        events,
        options=options,
        environment_counts=environment_counts,
    )
    _write_report(
        report,
        Path(args.report).expanduser().resolve() if args.report else None,
    )
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
