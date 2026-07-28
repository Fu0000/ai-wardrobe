from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.events.schemas import ClientEventBatch


def _event_payload(
    *,
    event_name: str = "diagnosis.result.viewed",
    properties: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "event_name": event_name,
        "event_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "session_id": uuid4().hex,
        "client_version": "1.0.0",
        "platform": "mp-weixin",
        "app_channel": "wechat",
        "properties": properties
        or {
            "diagnosis_id": str(uuid4()),
            "score_bucket": "80_to_100",
        },
    }


def test_client_event_schema_accepts_only_versioned_allowlisted_events() -> None:
    payload = ClientEventBatch.model_validate({"events": [_event_payload()]})

    event = payload.events[0]
    assert event.event_name == "diagnosis.result.viewed"
    assert event.event_version == 1
    assert event.occurred_at.tzinfo is UTC

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        ClientEventBatch.model_validate(
            {"events": [_event_payload(event_name="user.supplied.event")]}
        )


def test_client_event_schema_rejects_sensitive_or_unbounded_properties() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ClientEventBatch.model_validate(
            {
                "events": [
                    _event_payload(
                        properties={
                            "diagnosis_id": str(uuid4()),
                            "score_bucket": "80_to_100",
                            "photo_url": "https://private.example/photo.jpg",
                        }
                    )
                ]
            }
        )

    with pytest.raises(ValidationError, match="literal_error"):
        ClientEventBatch.model_validate(
            {
                "events": [
                    _event_payload(
                        properties={
                            "diagnosis_id": str(uuid4()),
                            "score_bucket": "user-controlled",
                        }
                    )
                ]
            }
        )


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime.now().replace(microsecond=0).isoformat(),
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        (datetime.now(UTC) - timedelta(days=31)).isoformat(),
    ],
)
def test_client_event_schema_rejects_invalid_client_timestamps(occurred_at: str) -> None:
    event = _event_payload()
    event["occurred_at"] = occurred_at

    with pytest.raises(ValidationError, match="occurred_at"):
        ClientEventBatch.model_validate({"events": [event]})


def test_client_event_batch_is_bounded() -> None:
    with pytest.raises(ValidationError, match="too_long"):
        ClientEventBatch.model_validate({"events": [_event_payload() for _ in range(21)]})
