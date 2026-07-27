from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feedback.models import BetaFeedback, FeedbackCategory
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.service import FeedbackApplicationService
from app.modules.identity.models import User

pytestmark = pytest.mark.integration


async def test_feedback_cursor_pagination_is_stable_and_user_scoped(
    session: AsyncSession,
) -> None:
    owner = User(id=uuid4())
    bystander = User(id=uuid4())
    session.add_all([owner, bystander])
    await session.flush()

    now = datetime.now(UTC)
    owned = [
        BetaFeedback(
            id=uuid4(),
            user_id=owner.id,
            category=FeedbackCategory.EXPERIENCE,
            message=f"第 {index} 条用于验证真实数据库游标分页。",
            idempotency_key=f"feedback-{uuid4().hex}",
            request_hash=str(index) * 64,
            created_at=now - timedelta(minutes=index),
            updated_at=now - timedelta(minutes=index),
        )
        for index in range(5)
    ]
    session.add_all(
        [
            *owned,
            BetaFeedback(
                id=uuid4(),
                user_id=bystander.id,
                category=FeedbackCategory.OTHER,
                message="其他用户的反馈绝不能出现在当前用户列表。",
                idempotency_key=f"feedback-{uuid4().hex}",
                request_hash="b" * 64,
                created_at=now + timedelta(minutes=1),
                updated_at=now + timedelta(minutes=1),
            ),
        ]
    )
    await session.flush()

    service = FeedbackApplicationService(FeedbackRepository(session))
    first = await service.list_owned(user_id=owner.id, limit=2)
    second = await service.list_owned(
        user_id=owner.id,
        limit=2,
        cursor=first.next_cursor,
    )
    third = await service.list_owned(
        user_id=owner.id,
        limit=2,
        cursor=second.next_cursor,
    )

    assert [item.id for item in first.items] == [owned[0].id, owned[1].id]
    assert [item.id for item in second.items] == [owned[2].id, owned[3].id]
    assert [item.id for item in third.items] == [owned[4].id]
    assert third.next_cursor is None
