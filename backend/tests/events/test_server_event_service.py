from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.modules.events.server import (
    ServerEventContext,
    build_server_event_values,
    elapsed_milliseconds,
)
from app.modules.events.service import privacy_safe_user_hash


def test_server_event_values_include_complete_privacy_safe_context() -> None:
    settings = Settings(environment="staging")
    user_id = uuid4()
    entity_id = uuid4()
    occurred_at = datetime.now(UTC)
    values = build_server_event_values(
        settings=settings,
        context=ServerEventContext(
            request_id="request-123",
            trace_id="a" * 32,
            session_id="session_12345678",
            client_version="1.2.3",
            platform="mp-weixin",
            app_channel="wechat",
        ),
        subject_user_id=user_id,
        event_name="diagnosis.job.created",
        entity_type="GenerationJob",
        entity_id=entity_id,
        dedupe_key=str(entity_id),
        properties={"job_id": str(entity_id), "occasion": "DAILY"},
        occurred_at=occurred_at,
    )

    assert values["user_id"] == user_id
    assert values["event_version"] == 1
    assert values["occurred_at"] == occurred_at
    assert values["environment"] == "staging"
    assert values["trace_id"] == "a" * 32
    assert values["request_id"] == "request-123"
    assert values["user_id_hash"] == privacy_safe_user_hash(settings, user_id)
    assert str(user_id) not in str(values["user_id_hash"])
    assert values["session_id"] == "session_12345678"
    assert values["client_version"] == "1.2.3"
    assert values["platform"] == "mp-weixin"
    assert values["app_channel"] == "wechat"


def test_server_event_can_retain_hash_without_user_foreign_key() -> None:
    settings = Settings()
    user_id = uuid4()
    values = build_server_event_values(
        settings=settings,
        context=ServerEventContext(),
        subject_user_id=user_id,
        event_name="privacy.deletion.completed",
        entity_type="DeletionJob",
        entity_id=uuid4(),
        dedupe_key="deletion-completed",
        properties={"deletion_type": "ACCOUNT", "latency_ms": 100},
        persist_user_reference=False,
    )

    assert values["user_id"] is None
    assert values["user_id_hash"] == privacy_safe_user_hash(settings, user_id)
    assert values["platform"] == "server"
    assert values["app_channel"] == "server"


def test_elapsed_milliseconds_is_utc_safe_and_never_negative() -> None:
    ended_at = datetime(2026, 7, 28, 8, 0, 1, tzinfo=UTC)

    assert (
        elapsed_milliseconds(
            datetime(2026, 7, 28, 8, 0, 0),
            ended_at,
        )
        == 1_000
    )
    assert elapsed_milliseconds(ended_at, ended_at) == 0
    assert elapsed_milliseconds(ended_at, datetime(2026, 7, 28, 8, 0, 0)) == 0
    assert elapsed_milliseconds(None, ended_at) == 0
