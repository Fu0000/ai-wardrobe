from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from celery import Celery

from app.modules.events.dispatcher import (
    CeleryEventPublisher,
    EventPublishError,
)
from app.modules.events.models import OutboxEvent, OutboxStatus
from app.modules.events.repository import retry_delay


def _job_event(task_type: str = "STYLE_DIAGNOSIS") -> OutboxEvent:
    job_id = uuid4()
    return OutboxEvent(
        id=uuid4(),
        aggregate_type="GenerationJob",
        aggregate_id=job_id,
        event_type="GenerationJobCreated",
        payload={
            "job_id": str(job_id),
            "user_id": str(uuid4()),
            "task_type": task_type,
            "status": "PENDING",
        },
        status=OutboxStatus.PENDING,
        attempt_count=0,
    )


def test_outbox_retry_is_exponential_and_capped() -> None:
    assert retry_delay(1) == timedelta(seconds=5)
    assert retry_delay(2) == timedelta(seconds=10)
    assert retry_delay(20) == timedelta(seconds=900)


@pytest.mark.asyncio
async def test_job_event_routes_to_independent_celery_queue() -> None:
    celery = MagicMock(spec=Celery)
    publisher = CeleryEventPublisher(celery)
    event = _job_event("STYLE_OPTIMIZATION")

    await publisher.publish(event)

    celery.send_task.assert_called_once_with(
        "ai_wardrobe.run_style_optimization",
        kwargs={"job_id": str(event.aggregate_id)},
        queue="image_generation",
    )


@pytest.mark.asyncio
async def test_job_event_propagates_only_allowlisted_trace_headers() -> None:
    celery = MagicMock(spec=Celery)
    publisher = CeleryEventPublisher(celery)
    event = _job_event()
    event.payload["trace_context"] = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "vendor=value",
        "ignored": 42,
    }

    await publisher.publish(event)

    celery.send_task.assert_called_once_with(
        "ai_wardrobe.run_style_diagnosis",
        kwargs={"job_id": str(event.aggregate_id)},
        queue="ai_fast",
        headers={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "tracestate": "vendor=value",
        },
    )


@pytest.mark.asyncio
async def test_unknown_job_type_is_not_silently_published() -> None:
    celery = MagicMock(spec=Celery)
    publisher = CeleryEventPublisher(celery)

    with pytest.raises(EventPublishError, match="UNSUPPORTED_JOB_TYPE"):
        await publisher.publish(_job_event("UNKNOWN"))

    celery.send_task.assert_not_called()
