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
        limit: int,
    ) -> list[BetaFeedback]:
        return [item for item in self.feedback if item.user_id == user_id][:limit]


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
