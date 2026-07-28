from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.events.service import privacy_safe_user_hash
from app.modules.growth.models import UserEvent


@dataclass(frozen=True, slots=True)
class ServerEventContext:
    request_id: str = "unavailable"
    trace_id: str = "unavailable"
    session_id: str | None = None
    client_version: str | None = None
    platform: str = "server"
    app_channel: str = "server"


def build_server_event_values(
    *,
    settings: Settings,
    context: ServerEventContext,
    subject_user_id: UUID,
    event_name: str,
    entity_type: str,
    entity_id: UUID,
    dedupe_key: str,
    properties: dict[str, object],
    event_id: UUID | None = None,
    occurred_at: datetime | None = None,
    persist_user_reference: bool = True,
) -> dict[str, object]:
    return {
        "id": event_id or uuid4(),
        "user_id": subject_user_id if persist_user_reference else None,
        "event_name": event_name,
        "event_version": 1,
        "occurred_at": occurred_at or datetime.now(UTC),
        "environment": settings.environment,
        "trace_id": context.trace_id,
        "request_id": context.request_id,
        "user_id_hash": privacy_safe_user_hash(settings, subject_user_id),
        "session_id": context.session_id,
        "client_version": context.client_version,
        "platform": context.platform,
        "app_channel": context.app_channel,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "dedupe_key": dedupe_key,
        "properties": properties,
    }


class ServerEventRecorder:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        context: ServerEventContext | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._context = context or ServerEventContext()

    async def record(
        self,
        *,
        subject_user_id: UUID,
        event_name: str,
        entity_type: str,
        entity_id: UUID,
        dedupe_key: str,
        properties: dict[str, object],
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
        persist_user_reference: bool = True,
    ) -> bool:
        values = build_server_event_values(
            settings=self._settings,
            context=self._context,
            subject_user_id=subject_user_id,
            event_name=event_name,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=dedupe_key,
            properties=properties,
            event_id=event_id,
            occurred_at=occurred_at,
            persist_user_reference=persist_user_reference,
        )
        statement = (
            insert(UserEvent)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_user_events_name_dedupe")
            .returning(UserEvent.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None
