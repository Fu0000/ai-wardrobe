from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.events.server import (
    ServerEventContext,
    ServerEventRecorder,
)
from app.modules.growth.models import UserEvent
from app.modules.growth.repository import GrowthRepository
from app.modules.identity.models import User
from tests.integration.markers import requires_services

pytestmark = [pytest.mark.integration, requires_services]


async def test_server_event_is_transactional_idempotent_and_context_complete(
    session: AsyncSession,
    settings: Settings,
) -> None:
    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    job_id = uuid4()
    recorder = ServerEventRecorder(
        session,
        settings,
        ServerEventContext(
            request_id="server-event-integration",
            trace_id="d" * 32,
        ),
    )

    first = await recorder.record(
        subject_user_id=user.id,
        event_name="diagnosis.job.created",
        entity_type="GenerationJob",
        entity_id=job_id,
        dedupe_key=str(job_id),
        properties={"job_id": str(job_id), "occasion": "DAILY"},
    )
    duplicate = await recorder.record(
        subject_user_id=user.id,
        event_name="diagnosis.job.created",
        entity_type="GenerationJob",
        entity_id=job_id,
        dedupe_key=str(job_id),
        properties={"job_id": str(job_id), "occasion": "DAILY"},
    )

    assert first is True
    assert duplicate is False
    result = await session.execute(
        select(UserEvent).where(
            UserEvent.event_name == "diagnosis.job.created",
            UserEvent.dedupe_key == str(job_id),
        )
    )
    stored = result.scalar_one()
    assert stored.user_id == user.id
    assert stored.user_id_hash is not None
    assert stored.environment == settings.environment
    assert stored.request_id == "server-event-integration"
    assert stored.trace_id == "d" * 32
    assert stored.platform == "server"
    assert stored.app_channel == "server"


async def test_growth_event_adapter_uses_shared_server_context(
    session: AsyncSession,
    settings: Settings,
) -> None:
    user = User(id=uuid4())
    session.add(user)
    await session.flush()
    share_id = uuid4()
    repository = GrowthRepository(
        session,
        settings=settings,
        event_context=ServerEventContext(
            request_id="growth-event-integration",
            trace_id="e" * 32,
            app_channel="api",
        ),
    )

    first = await repository.record_event(
        event_id=uuid4(),
        user_id=user.id,
        event_name="share.wechat.invoked",
        entity_type="ShareRecord",
        entity_id=share_id,
        dedupe_key=f"{share_id}:WECHAT_FRIEND",
        properties={
            "share_id": str(share_id),
            "attribution_source": "WECHAT_FRIEND",
        },
    )
    duplicate = await repository.record_event(
        event_id=uuid4(),
        user_id=user.id,
        event_name="share.wechat.invoked",
        entity_type="ShareRecord",
        entity_id=share_id,
        dedupe_key=f"{share_id}:WECHAT_FRIEND",
        properties={
            "share_id": str(share_id),
            "attribution_source": "WECHAT_FRIEND",
        },
    )

    assert first is True
    assert duplicate is False
    result = await session.execute(
        select(UserEvent).where(
            UserEvent.event_name == "share.wechat.invoked",
            UserEvent.entity_id == share_id,
        )
    )
    stored = result.scalar_one()
    assert stored.user_id_hash is not None
    assert stored.environment == settings.environment
    assert stored.request_id == "growth-event-integration"
    assert stored.trace_id == "e" * 32
    assert stored.app_channel == "api"
    assert stored.properties["share_id"] == str(share_id)
