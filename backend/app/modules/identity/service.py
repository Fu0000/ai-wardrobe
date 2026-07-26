from dataclasses import dataclass
from uuid import UUID

from app.modules.identity.repository import IdentityRepository
from app.modules.identity.security import AccessTokenService, SubjectProtector
from app.modules.identity.wechat import WeChatLoginClient


@dataclass(frozen=True, slots=True)
class LoginResult:
    user_id: UUID
    display_name: str | None
    is_new_user: bool
    access_token: str
    expires_in_seconds: int


class IdentityApplicationService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        wechat_client: WeChatLoginClient,
        subject_protector: SubjectProtector,
        token_service: AccessTokenService,
    ) -> None:
        self._repository = repository
        self._wechat_client = wechat_client
        self._subject_protector = subject_protector
        self._token_service = token_service

    async def login_with_wechat_code(self, code: str) -> LoginResult:
        wechat_session = await self._wechat_client.exchange_code(code)

        subject_hash = self._subject_protector.digest(wechat_session.open_id)
        subject_encrypted = self._subject_protector.encrypt(wechat_session.open_id)
        union_hash = (
            self._subject_protector.digest(wechat_session.union_id)
            if wechat_session.union_id
            else None
        )
        union_encrypted = (
            self._subject_protector.encrypt(wechat_session.union_id)
            if wechat_session.union_id
            else None
        )
        user = await self._repository.get_or_create_wechat_user(
            subject_hash=subject_hash,
            subject_encrypted=subject_encrypted,
            union_hash=union_hash,
            union_encrypted=union_encrypted,
        )
        access_token = self._token_service.issue(user.user_id)

        return LoginResult(
            user_id=user.user_id,
            display_name=user.display_name,
            is_new_user=user.is_new,
            access_token=access_token.value,
            expires_in_seconds=access_token.expires_in_seconds,
        )
