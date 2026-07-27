from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.growth.models import UserEvent
from app.modules.identity.api import UpdateProfileRequest, update_profile
from app.modules.identity.models import User, UserProfile


def test_profile_update_requires_explicit_versioned_consent() -> None:
    with pytest.raises(ValidationError):
        UpdateProfileRequest(
            has_ai_processing_consent=True,
        )
    with pytest.raises(ValidationError):
        UpdateProfileRequest(
            consent_version="privacy-v1",
        )
    with pytest.raises(ValidationError):
        UpdateProfileRequest()

    payload = UpdateProfileRequest(
        has_ai_processing_consent=True,
        consent_version="privacy-v1",
    )
    assert payload.has_ai_processing_consent is True


def test_profile_update_normalizes_display_name() -> None:
    payload = UpdateProfileRequest(display_name="  林   真  ")
    assert payload.display_name == "林 真"


@pytest.mark.asyncio
async def test_profile_update_persists_name_and_consent() -> None:
    user_id = uuid4()
    user = User(id=user_id)
    user.profile = UserProfile(
        user_id=user_id,
        display_name=None,
        has_ai_processing_consent=False,
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()

    response = await update_profile(
        payload=UpdateProfileRequest(
            display_name="  林 真  ",
            has_ai_processing_consent=True,
            consent_version="privacy-v1",
        ),
        user=user,
        session=session,
    )

    assert response.display_name == "林 真"
    assert response.has_ai_processing_consent is True
    assert response.consent_version == "privacy-v1"
    event = next(
        call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], UserEvent)
    )
    assert event.event_name == "consent.ai.accepted"
    assert event.user_id == user_id
    assert event.properties == {"consent_version": "privacy-v1"}
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_update_can_revoke_ai_consent() -> None:
    user_id = uuid4()
    user = User(id=user_id)
    user.profile = UserProfile(
        user_id=user_id,
        consent_version="privacy-v1",
        has_ai_processing_consent=True,
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()

    response = await update_profile(
        payload=UpdateProfileRequest(has_ai_processing_consent=False),
        user=user,
        session=session,
    )

    assert response.has_ai_processing_consent is False
    assert response.consent_version is None
    event = next(
        call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], UserEvent)
    )
    assert event.event_name == "consent.ai.revoked"
    assert event.properties == {"consent_version": "privacy-v1"}


@pytest.mark.asyncio
async def test_profile_update_does_not_audit_name_or_unchanged_consent() -> None:
    user_id = uuid4()
    user = User(id=user_id)
    user.profile = UserProfile(
        user_id=user_id,
        display_name="原名字",
        consent_version="privacy-v1",
        has_ai_processing_consent=True,
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()

    await update_profile(
        payload=UpdateProfileRequest(display_name="新名字"),
        user=user,
        session=session,
    )
    await update_profile(
        payload=UpdateProfileRequest(
            has_ai_processing_consent=True,
            consent_version="privacy-v1",
        ),
        user=user,
        session=session,
    )

    assert not any(isinstance(call.args[0], UserEvent) for call in session.add.call_args_list)
