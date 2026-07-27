from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.modules.feedback.api import CreateFeedbackRequest
from app.modules.feedback.models import BetaFeedback, FeedbackCategory
from app.modules.feedback.service import (
    FeedbackApplicationService,
    FeedbackInput,
    FeedbackServiceError,
)


class FakeFeedbackRepository:
    def __init__(self) -> None:
        self.feedback: list[BetaFeedback] = []
        self.owned_jobs: set[UUID] = set()

    async def find_idempotent(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> BetaFeedback | None:
        return next(
            (
                item
                for item in self.feedback
                if item.user_id == user_id and item.idempotency_key == idempotency_key
            ),
            None,
        )

    async def owned_job_exists(self, *, user_id: UUID, job_id: UUID) -> bool:
        del user_id
        return job_id in self.owned_jobs

    async def create(self, feedback: BetaFeedback) -> bool:
        self.feedback.append(feedback)
        return True

    async def list_owned(
        self,
        *,
        user_id: UUID,
        cursor_created_at: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> list[BetaFeedback]:
        items = sorted(
            (item for item in self.feedback if item.user_id == user_id),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        if cursor_created_at is not None and cursor_id is not None:
            items = [
                item
                for item in items
                if (item.created_at, item.id) < (cursor_created_at, cursor_id)
            ]
        return items[:limit]


def feedback_input(
    *,
    message: str = "优化后的图片和我的原始衣服不够一致。",
    related_job_id: UUID | None = None,
) -> FeedbackInput:
    return FeedbackInput(
        category=FeedbackCategory.AI_QUALITY,
        rating=3,
        message=message,
        related_job_id=related_job_id,
        page="pages/optimization/result",
        app_version="0.1.0",
        platform="ios",
        system_version="iOS 19",
        wechat_version="9.0",
        network_type="wifi",
        trace_id="a" * 32,
    )


def stored_feedback(
    *,
    user_id: UUID,
    created_at: datetime,
) -> BetaFeedback:
    return BetaFeedback(
        id=uuid4(),
        user_id=user_id,
        category=FeedbackCategory.EXPERIENCE,
        message="用于验证反馈列表游标分页的测试内容。",
        idempotency_key=f"feedback-{uuid4().hex}",
        request_hash="a" * 64,
        created_at=created_at,
        updated_at=created_at,
    )


async def test_feedback_is_idempotent_for_the_same_payload() -> None:
    repository = FakeFeedbackRepository()
    service = FeedbackApplicationService(repository)  # type: ignore[arg-type]
    user_id = uuid4()

    first = await service.create(
        user_id=user_id,
        idempotency_key="feedback-request-1",
        feedback_input=feedback_input(),
    )
    repeated = await service.create(
        user_id=user_id,
        idempotency_key="feedback-request-1",
        feedback_input=feedback_input(),
    )

    assert first.reused is False
    assert repeated.reused is True
    assert repeated.feedback.id == first.feedback.id
    assert len(repository.feedback) == 1


async def test_feedback_rejects_idempotency_key_reuse_with_new_content() -> None:
    repository = FakeFeedbackRepository()
    service = FeedbackApplicationService(repository)  # type: ignore[arg-type]
    user_id = uuid4()
    await service.create(
        user_id=user_id,
        idempotency_key="feedback-request-1",
        feedback_input=feedback_input(),
    )

    with pytest.raises(FeedbackServiceError, match="IDEMPOTENCY_KEY_REUSED"):
        await service.create(
            user_id=user_id,
            idempotency_key="feedback-request-1",
            feedback_input=feedback_input(message="这是另一条完全不同的反馈内容。"),
        )


async def test_feedback_related_job_must_belong_to_the_user() -> None:
    repository = FakeFeedbackRepository()
    service = FeedbackApplicationService(repository)  # type: ignore[arg-type]

    with pytest.raises(FeedbackServiceError, match="RELATED_JOB_NOT_FOUND"):
        await service.create(
            user_id=uuid4(),
            idempotency_key="feedback-request-1",
            feedback_input=feedback_input(related_job_id=uuid4()),
        )


async def test_feedback_list_uses_stable_opaque_cursor_pages() -> None:
    repository = FakeFeedbackRepository()
    service = FeedbackApplicationService(repository)  # type: ignore[arg-type]
    user_id = uuid4()
    now = datetime.now(UTC)
    repository.feedback = [
        stored_feedback(user_id=user_id, created_at=now - timedelta(minutes=index))
        for index in range(5)
    ]
    repository.feedback.append(
        stored_feedback(user_id=uuid4(), created_at=now + timedelta(minutes=1))
    )

    first = await service.list_owned(user_id=user_id, limit=2)
    second = await service.list_owned(
        user_id=user_id,
        limit=2,
        cursor=first.next_cursor,
    )
    third = await service.list_owned(
        user_id=user_id,
        limit=2,
        cursor=second.next_cursor,
    )

    assert [len(first.items), len(second.items), len(third.items)] == [2, 2, 1]
    assert first.next_cursor is not None
    assert second.next_cursor is not None
    assert third.next_cursor is None
    assert len({item.id for page in (first, second, third) for item in page.items}) == 5


async def test_feedback_list_rejects_invalid_cursor() -> None:
    service = FeedbackApplicationService(FakeFeedbackRepository())  # type: ignore[arg-type]

    with pytest.raises(FeedbackServiceError, match="INVALID_FEEDBACK_CURSOR"):
        await service.list_owned(user_id=uuid4(), cursor="not-a-valid-cursor")


def test_feedback_message_is_normalized_and_not_blank() -> None:
    payload = CreateFeedbackRequest(
        category=FeedbackCategory.EXPERIENCE,
        message="  页面切换之后，任务状态  没有及时更新。 ",
    )

    assert payload.message == "页面切换之后，任务状态 没有及时更新。"

    with pytest.raises(ValueError, match="at least 10"):
        CreateFeedbackRequest(
            category=FeedbackCategory.OTHER,
            message="            ",
        )
