from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.identity import api as identity_api
from app.modules.identity.api import UpdateProfileRequest, update_profile
from app.modules.identity.models import User, UserProfile


class EventRecorderSpy:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, **values: object) -> bool:
        self.events.append(values)
        return True


def _request() -> Request:
    request = MagicMock(spec=Request)
    request.app.state.settings = Settings()
    request.state.request_id = "profile-test"
    return request


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
async def test_profile_update_persists_name_and_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    user = User(id=user_id)
    user.profile = UserProfile(
        user_id=user_id,
        display_name=None,
        has_ai_processing_consent=False,
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()
    recorder = EventRecorderSpy()
    monkeypatch.setattr(
        identity_api,
        "ServerEventRecorder",
        lambda *_args, **_kwargs: recorder,
    )

    response = await update_profile(
        payload=UpdateProfileRequest(
            display_name="  林 真  ",
            has_ai_processing_consent=True,
            consent_version="privacy-v1",
        ),
        request=_request(),
        user=user,
        session=session,
    )

    assert response.display_name == "林 真"
    assert response.has_ai_processing_consent is True
    assert response.consent_version == "privacy-v1"
    assert len(recorder.events) == 1
    assert recorder.events[0]["subject_user_id"] == user_id
    assert recorder.events[0]["event_name"] == "consent.ai.accepted"
    assert recorder.events[0]["entity_type"] == "User"
    assert recorder.events[0]["entity_id"] == user_id
    assert str(recorder.events[0]["dedupe_key"]).startswith("consent:")
    assert recorder.events[0]["properties"] == {"consent_version": "privacy-v1"}
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_update_can_revoke_ai_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    user = User(id=user_id)
    user.profile = UserProfile(
        user_id=user_id,
        consent_version="privacy-v1",
        has_ai_processing_consent=True,
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()
    recorder = EventRecorderSpy()
    monkeypatch.setattr(
        identity_api,
        "ServerEventRecorder",
        lambda *_args, **_kwargs: recorder,
    )

    response = await update_profile(
        payload=UpdateProfileRequest(has_ai_processing_consent=False),
        request=_request(),
        user=user,
        session=session,
    )

    assert response.has_ai_processing_consent is False
    assert response.consent_version is None
    assert recorder.events[0]["event_name"] == "consent.ai.revoked"
    assert recorder.events[0]["properties"] == {"consent_version": "privacy-v1"}


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
        request=_request(),
        user=user,
        session=session,
    )
    await update_profile(
        payload=UpdateProfileRequest(
            has_ai_processing_consent=True,
            consent_version="privacy-v1",
        ),
        request=_request(),
        user=user,
        session=session,
    )

    session.execute.assert_not_called()
