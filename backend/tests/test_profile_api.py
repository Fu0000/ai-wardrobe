from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

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
