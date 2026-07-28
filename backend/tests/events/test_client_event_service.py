from datetime import UTC, datetime
from uuid import uuid4

from pydantic import SecretStr

from app.core.config import Settings
from app.modules.events.schemas import ClientEventBatch
from app.modules.events.service import (
    EventIngestContext,
    build_event_rows,
    privacy_safe_user_hash,
)


def test_event_rows_use_server_context_and_privacy_safe_identity() -> None:
    settings = Settings.model_construct(
        environment="staging",
        identity_hmac_key=SecretStr("event-test-hmac-key-with-enough-entropy"),
    )
    user_id = uuid4()
    event_id = uuid4()
    diagnosis_id = uuid4()
    batch = ClientEventBatch.model_validate(
        {
            "events": [
                {
                    "event_id": str(event_id),
                    "event_name": "diagnosis.result.viewed",
                    "event_version": 1,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "session_id": uuid4().hex,
                    "client_version": "1.0.0",
                    "platform": "mp-weixin",
                    "app_channel": "wechat",
                    "properties": {
                        "diagnosis_id": str(diagnosis_id),
                        "score_bucket": "80_to_100",
                    },
                }
            ]
        }
    )

    rows = build_event_rows(
        settings=settings,
        batch=batch,
        context=EventIngestContext(
            user_id=user_id,
            request_id="request-12345678",
            trace_id="a" * 32,
        ),
    )

    assert rows == [
        {
            "id": event_id,
            "user_id": user_id,
            "event_name": "diagnosis.result.viewed",
            "event_version": 1,
            "occurred_at": batch.events[0].occurred_at,
            "environment": "staging",
            "trace_id": "a" * 32,
            "request_id": "request-12345678",
            "user_id_hash": privacy_safe_user_hash(settings, user_id),
            "session_id": batch.events[0].session_id,
            "client_version": "1.0.0",
            "platform": "mp-weixin",
            "app_channel": "wechat",
            "entity_type": "StyleDiagnosis",
            "entity_id": diagnosis_id,
            "dedupe_key": f"client:{event_id}",
            "properties": {
                "diagnosis_id": str(diagnosis_id),
                "score_bucket": "80_to_100",
            },
        }
    ]
    assert rows[0]["user_id_hash"] != str(user_id)
    assert len(str(rows[0]["user_id_hash"])) == 64


def test_user_hash_is_domain_separated_and_stable() -> None:
    settings = Settings(identity_hmac_key="event-test-hmac-key-with-enough-entropy")
    user_id = uuid4()

    first = privacy_safe_user_hash(settings, user_id)
    second = privacy_safe_user_hash(settings, user_id)

    assert first == second
    assert first != privacy_safe_user_hash(settings, uuid4())
