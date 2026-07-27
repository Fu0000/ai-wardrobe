import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.modules.feedback.models import BetaFeedback, FeedbackCategory
from app.modules.feedback.repository import FeedbackRepository


class FeedbackServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FeedbackInput:
    category: FeedbackCategory
    rating: int | None
    message: str
    related_job_id: UUID | None
    page: str | None
    app_version: str | None
    platform: str | None
    system_version: str | None
    wechat_version: str | None
    network_type: str | None
    trace_id: str | None

    def canonical_payload(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "rating": self.rating,
            "message": self.message,
            "related_job_id": (
                str(self.related_job_id) if self.related_job_id is not None else None
            ),
            "page": self.page,
            "app_version": self.app_version,
            "platform": self.platform,
            "system_version": self.system_version,
            "wechat_version": self.wechat_version,
            "network_type": self.network_type,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True, slots=True)
class CreatedFeedback:
    feedback: BetaFeedback
    reused: bool


@dataclass(frozen=True, slots=True)
class FeedbackPage:
    items: list[BetaFeedback]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _FeedbackCursor:
    created_at: datetime
    feedback_id: UUID


def _encode_cursor(feedback: BetaFeedback) -> str:
    payload = json.dumps(
        {
            "created_at": feedback.created_at.isoformat(),
            "id": str(feedback.id),
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> _FeedbackCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(decoded)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError
        if set(payload) != {"created_at", "id", "version"}:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        feedback_id = UUID(payload["id"])
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FeedbackServiceError("INVALID_FEEDBACK_CURSOR") from error
    return _FeedbackCursor(created_at=created_at, feedback_id=feedback_id)


class FeedbackApplicationService:
    def __init__(self, repository: FeedbackRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
        feedback_input: FeedbackInput,
    ) -> CreatedFeedback:
        normalized_key = idempotency_key.strip()
        if not 8 <= len(normalized_key) <= 128:
            raise FeedbackServiceError("INVALID_IDEMPOTENCY_KEY")
        canonical = json.dumps(
            feedback_input.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        existing = await self._repository.find_idempotent(
            user_id=user_id,
            idempotency_key=normalized_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise FeedbackServiceError("IDEMPOTENCY_KEY_REUSED")
            return CreatedFeedback(existing, reused=True)

        if feedback_input.related_job_id is not None:
            job_owned = await self._repository.owned_job_exists(
                user_id=user_id,
                job_id=feedback_input.related_job_id,
            )
            if not job_owned:
                raise FeedbackServiceError("RELATED_JOB_NOT_FOUND")

        feedback = BetaFeedback(
            id=uuid4(),
            user_id=user_id,
            related_job_id=feedback_input.related_job_id,
            category=feedback_input.category,
            rating=feedback_input.rating,
            message=feedback_input.message,
            page=feedback_input.page,
            app_version=feedback_input.app_version,
            platform=feedback_input.platform,
            system_version=feedback_input.system_version,
            wechat_version=feedback_input.wechat_version,
            network_type=feedback_input.network_type,
            trace_id=feedback_input.trace_id,
            idempotency_key=normalized_key,
            request_hash=request_hash,
        )
        if await self._repository.create(feedback):
            return CreatedFeedback(feedback, reused=False)
        concurrent = await self._repository.find_idempotent(
            user_id=user_id,
            idempotency_key=normalized_key,
        )
        if concurrent is None:
            raise FeedbackServiceError("FEEDBACK_CREATE_CONFLICT")
        if concurrent.request_hash != request_hash:
            raise FeedbackServiceError("IDEMPOTENCY_KEY_REUSED")
        return CreatedFeedback(concurrent, reused=True)

    async def list_owned(
        self,
        *,
        user_id: UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> FeedbackPage:
        decoded = _decode_cursor(cursor) if cursor is not None else None
        feedback = await self._repository.list_owned(
            user_id=user_id,
            cursor_created_at=decoded.created_at if decoded else None,
            cursor_id=decoded.feedback_id if decoded else None,
            limit=limit + 1,
        )
        has_more = len(feedback) > limit
        items = feedback[:limit]
        return FeedbackPage(
            items=items,
            next_cursor=_encode_cursor(items[-1]) if has_more and items else None,
        )
