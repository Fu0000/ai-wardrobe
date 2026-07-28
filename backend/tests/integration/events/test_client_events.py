from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.main import create_app
from app.modules.assets.models import AssetKind, UserAsset
from app.modules.events.schemas import ClientEventBatch
from app.modules.events.service import (
    ClientEventError,
    ClientEventService,
    EventIngestContext,
)
from app.modules.growth.models import UserEvent
from app.modules.identity.models import User
from app.modules.identity.security import AccessTokenService
from tests.integration.markers import requires_services

pytestmark = [pytest.mark.integration, requires_services]


async def test_client_events_are_idempotent_with_complete_server_context(
    session: AsyncSession,
    settings: Settings,
) -> None:
    user = User(id=uuid4())
    session.add(user)
    await session.flush()

    event_id = uuid4()
    asset = UserAsset(
        id=uuid4(),
        user_id=user.id,
        kind=AssetKind.USER_UPLOAD,
        bucket="integration-test",
        object_key=f"integration/client-events/{uuid4().hex}.jpg",
    )
    session.add(asset)
    await session.flush()
    batch = ClientEventBatch.model_validate(
        {
            "events": [
                {
                    "event_id": str(event_id),
                    "event_name": "asset.upload.started",
                    "event_version": 1,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "session_id": uuid4().hex,
                    "client_version": "1.0.0",
                    "platform": "mp-weixin",
                    "app_channel": "wechat",
                    "properties": {
                        "asset_id": str(asset.id),
                        "size_bucket": "1_to_5mb",
                    },
                }
            ]
        }
    )
    context = EventIngestContext(
        user_id=user.id,
        request_id="integration-request",
        trace_id="b" * 32,
    )
    service = ClientEventService(session, settings)

    first = await service.ingest(batch=batch, context=context)
    duplicate = await service.ingest(batch=batch, context=context)

    assert (first.accepted_count, first.duplicate_count) == (1, 0)
    assert (duplicate.accepted_count, duplicate.duplicate_count) == (0, 1)

    result = await session.execute(select(UserEvent).where(UserEvent.id == event_id))
    stored = result.scalar_one()
    assert stored.user_id == user.id
    assert stored.event_version == 1
    assert stored.environment == settings.environment
    assert stored.trace_id == "b" * 32
    assert stored.request_id == "integration-request"
    assert stored.user_id_hash is not None
    assert stored.session_id == batch.events[0].session_id
    assert stored.platform == "mp-weixin"
    assert stored.app_channel == "wechat"
    assert stored.entity_type == "UserAsset"
    assert stored.entity_id == asset.id
    assert stored.properties == {
        "asset_id": str(asset.id),
        "size_bucket": "1_to_5mb",
    }


async def test_client_event_cannot_reference_another_users_entity(
    session: AsyncSession,
    settings: Settings,
) -> None:
    owner = User(id=uuid4())
    attacker = User(id=uuid4())
    foreign_asset = UserAsset(
        id=uuid4(),
        user_id=owner.id,
        kind=AssetKind.USER_UPLOAD,
        bucket="integration-test",
        object_key=f"integration/client-events/{uuid4().hex}.jpg",
    )
    session.add_all([owner, attacker])
    await session.flush()
    session.add(foreign_asset)
    await session.flush()

    batch = ClientEventBatch.model_validate(
        {
            "events": [
                {
                    "event_id": str(uuid4()),
                    "event_name": "asset.upload.started",
                    "event_version": 1,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "session_id": uuid4().hex,
                    "client_version": "1.0.0",
                    "platform": "mp-weixin",
                    "app_channel": "wechat",
                    "properties": {
                        "asset_id": str(foreign_asset.id),
                        "size_bucket": "1_to_5mb",
                    },
                }
            ]
        }
    )

    with pytest.raises(ClientEventError, match="CLIENT_EVENT_ENTITY_INVALID"):
        await ClientEventService(session, settings).ingest(
            batch=batch,
            context=EventIngestContext(
                user_id=attacker.id,
                request_id="integration-request",
                trace_id="c" * 32,
            ),
        )


async def test_client_event_endpoint_persists_authenticated_batch(
    session: AsyncSession,
    settings: Settings,
) -> None:
    user = User(id=uuid4())
    asset = UserAsset(
        id=uuid4(),
        user_id=user.id,
        kind=AssetKind.USER_UPLOAD,
        bucket="integration-test",
        object_key=f"integration/client-events/{uuid4().hex}.jpg",
    )
    session.add(user)
    await session.flush()
    session.add(asset)
    await session.flush()

    async def scoped_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = create_app(settings)
    app.dependency_overrides[get_session] = scoped_session
    event_id = uuid4()
    access_token = AccessTokenService(settings).issue(user.id).value
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/client-events",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Request-ID": "client-event-integration",
            },
            json={
                "events": [
                    {
                        "event_id": str(event_id),
                        "event_name": "asset.upload.started",
                        "event_version": 1,
                        "occurred_at": datetime.now(UTC).isoformat(),
                        "session_id": uuid4().hex,
                        "client_version": "1.0.0",
                        "platform": "mp-weixin",
                        "app_channel": "wechat",
                        "properties": {
                            "asset_id": str(asset.id),
                            "size_bucket": "1_to_5mb",
                        },
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted_count": 1, "duplicate_count": 0}
    result = await session.execute(select(UserEvent).where(UserEvent.id == event_id))
    stored = result.scalar_one()
    assert stored.user_id == user.id
    assert stored.request_id == "client-event-integration"
