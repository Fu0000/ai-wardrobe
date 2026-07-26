import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog
from celery import Celery

from app.core.config import Settings
from app.core.telemetry import record_outbox_backlog, record_outbox_publish
from app.database.session import Database
from app.modules.events.models import OutboxEvent, OutboxStatus
from app.modules.events.repository import OutboxRepository

logger = structlog.get_logger(__name__)


class EventPublishError(Exception):
    pass


class EventPublisher(Protocol):
    async def publish(self, event: OutboxEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class EventRoute:
    task_name: str
    queue: str


ROUTES_BY_JOB_TYPE: dict[str, EventRoute] = {
    "STYLE_DIAGNOSIS": EventRoute(
        task_name="ai_wardrobe.run_style_diagnosis",
        queue="ai_fast",
    ),
    "STYLE_OPTIMIZATION": EventRoute(
        task_name="ai_wardrobe.run_style_optimization",
        queue="image_generation",
    ),
    "SHARE_ASSET": EventRoute(
        task_name="ai_wardrobe.run_share_asset",
        queue="media_generation",
    ),
    "DELETION": EventRoute(
        task_name="ai_wardrobe.run_deletion",
        queue="maintenance",
    ),
}


class CeleryEventPublisher:
    def __init__(self, celery_app: Celery) -> None:
        self._celery_app = celery_app

    async def publish(self, event: OutboxEvent) -> None:
        if event.event_type != "GenerationJobCreated":
            raise EventPublishError("UNSUPPORTED_EVENT_TYPE")
        task_type = event.payload.get("task_type")
        if not isinstance(task_type, str) or task_type not in ROUTES_BY_JOB_TYPE:
            raise EventPublishError("UNSUPPORTED_JOB_TYPE")
        job_id = event.payload.get("job_id")
        if not isinstance(job_id, str):
            raise EventPublishError("INVALID_JOB_EVENT")

        route = ROUTES_BY_JOB_TYPE[task_type]
        raw_trace_context = event.payload.get("trace_context")
        trace_headers = (
            {
                str(key): str(value)
                for key, value in raw_trace_context.items()
                if isinstance(key, str)
                and key.lower() in {"traceparent", "tracestate"}
                and isinstance(value, str)
            }
            if isinstance(raw_trace_context, dict)
            else {}
        )
        send_options: dict[str, object] = {
            "kwargs": {"job_id": job_id},
            "queue": route.queue,
        }
        if trace_headers:
            send_options["headers"] = trace_headers
        await asyncio.to_thread(
            self._celery_app.send_task,
            route.task_name,
            **send_options,
        )


class OutboxDispatcher:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        publisher: EventPublisher,
    ) -> None:
        self._settings = settings
        self._database = database
        self._publisher = publisher

    async def dispatch_once(self) -> int:
        async with self._database.session_factory() as session:
            repository = OutboxRepository(session)
            events = await repository.claim_batch(
                worker_id=self._settings.outbox_worker_id,
                batch_size=self._settings.outbox_batch_size,
                lock_timeout_seconds=self._settings.outbox_lock_timeout_seconds,
            )
            await session.commit()

        for event in events:
            await self._publish_one(event.id, event)
        async with self._database.session_factory() as session:
            backlog = await OutboxRepository(session).metrics()
        record_outbox_backlog(
            pending_count=backlog.pending_count,
            failed_count=backlog.failed_count,
            dead_letter_count=backlog.dead_letter_count,
            oldest_pending_age_seconds=backlog.oldest_pending_age_seconds,
        )
        return len(events)

    async def _publish_one(self, event_id: UUID, event: OutboxEvent) -> None:
        try:
            await self._publisher.publish(event)
        except Exception as error:
            record_outbox_publish(
                event_type=event.event_type,
                outcome="failed",
            )
            async with self._database.session_factory() as session:
                status = await OutboxRepository(session).mark_failed(
                    event_id,
                    safe_error=type(error).__name__,
                    max_attempts=self._settings.outbox_max_attempts,
                )
                await session.commit()
            if status is OutboxStatus.DEAD_LETTER:
                # 死信不会自愈，必须显式告警而不是淹没在重试日志里。
                await logger.aerror(
                    "outbox_event_dead_lettered",
                    event_id=str(event_id),
                    event_type=event.event_type,
                    error_type=type(error).__name__,
                    attempt_count=event.attempt_count + 1,
                )
                return
            await logger.aerror(
                "outbox_publish_failed",
                event_id=str(event_id),
                event_type=event.event_type,
                error_type=type(error).__name__,
            )
            return

        record_outbox_publish(
            event_type=event.event_type,
            outcome="published",
        )
        async with self._database.session_factory() as session:
            await OutboxRepository(session).mark_published(event_id)
            await session.commit()
