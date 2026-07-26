from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.modules.identity.models import (
    IdentityProvider,
    User,
    UserIdentity,
    UserProfile,
)


@dataclass(frozen=True, slots=True)
class IdentityUser:
    user_id: UUID
    display_name: str | None
    is_new: bool


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_wechat_user(
        self,
        *,
        subject_hash: str,
        subject_encrypted: str,
        union_hash: str | None,
        union_encrypted: str | None,
    ) -> IdentityUser:
        existing = await self._find_wechat_identity(subject_hash)
        if existing is not None:
            return self._as_identity_user(existing, is_new=False)

        user = User()
        profile = UserProfile(user=user)
        identity = UserIdentity(
            user=user,
            provider=IdentityProvider.WECHAT,
            provider_subject_hash=subject_hash,
            provider_subject_encrypted=subject_encrypted,
            union_subject_hash=union_hash,
            union_subject_encrypted=union_encrypted,
        )

        try:
            async with self._session.begin_nested():
                self._session.add_all([user, profile, identity])
                await self._session.flush()
            return IdentityUser(
                user_id=user.id,
                display_name=profile.display_name,
                is_new=True,
            )
        except IntegrityError:
            concurrent = await self._find_wechat_identity(subject_hash)
            if concurrent is None:
                raise
            return self._as_identity_user(concurrent, is_new=False)

    async def _find_wechat_identity(
        self,
        subject_hash: str,
    ) -> UserIdentity | None:
        statement = (
            select(UserIdentity)
            .where(
                UserIdentity.provider == IdentityProvider.WECHAT,
                UserIdentity.provider_subject_hash == subject_hash,
            )
            .options(
                joinedload(UserIdentity.user).joinedload(User.profile),
            )
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    @staticmethod
    def _as_identity_user(identity: UserIdentity, *, is_new: bool) -> IdentityUser:
        profile = identity.user.profile
        return IdentityUser(
            user_id=identity.user_id,
            display_name=profile.display_name if profile else None,
            is_new=is_new,
        )
