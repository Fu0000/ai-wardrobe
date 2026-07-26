"""Outbox 死信的落定逻辑。

毒事件（如 UNSUPPORTED_JOB_TYPE 这类永久性坏载荷）此前与瞬时故障走同一条
重试路径，每 15 分钟一次直至永远，并永久钉住 failed_count 告警。这里覆盖
重试上限、死信落定与指标分离。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.modules.events.models import OutboxEvent, OutboxStatus
from app.modules.events.repository import OutboxRepository, retry_delay


def _event(*, attempt_count: int = 0) -> OutboxEvent:
    job_id = uuid4()
    return OutboxEvent(
        id=uuid4(),
        aggregate_type="GenerationJob",
        aggregate_id=job_id,
        event_type="GenerationJobCreated",
        payload={"job_id": str(job_id)},
        status=OutboxStatus.PROCESSING,
        attempt_count=attempt_count,
        locked_by="outbox-local",
        locked_at=datetime.now(UTC),
    )


class _FakeSession:
    """只承载 get_for_update 与 flush。"""

    def __init__(self, event: OutboxEvent | None) -> None:
        self._event = event
        self.flushed = False

    async def execute(self, _statement: object) -> object:
        event = self._event

        class _Result:
            def scalar_one_or_none(self) -> OutboxEvent | None:
                return event

        return _Result()

    async def flush(self) -> None:
        self.flushed = True


def _repository(event: OutboxEvent | None) -> OutboxRepository:
    return OutboxRepository(_FakeSession(event))  # type: ignore[arg-type]


async def test_failure_below_limit_stays_retryable() -> None:
    """未达上限时保持 FAILED 并排下一次重试。"""

    event = _event(attempt_count=0)
    now = datetime.now(UTC)

    status = await _repository(event).mark_failed(
        event.id,
        safe_error="ConnectionError",
        max_attempts=10,
        now=now,
    )

    assert status is OutboxStatus.FAILED
    assert event.attempt_count == 1
    assert event.next_retry_at == now + retry_delay(1)
    assert event.locked_by is None


async def test_failure_at_limit_becomes_dead_letter() -> None:
    """达到上限后转入死信，不再排程。"""

    event = _event(attempt_count=9)

    status = await _repository(event).mark_failed(
        event.id,
        safe_error="EventPublishError",
        max_attempts=10,
    )

    assert status is OutboxStatus.DEAD_LETTER
    assert event.attempt_count == 10
    assert event.next_retry_at is None, "死信排程会让它被 claim_batch 重新捞回"
    assert event.locked_by is None
    assert event.last_error == "EventPublishError"


async def test_dead_letter_is_terminal_even_if_attempts_exceed_limit() -> None:
    """已超上限的事件不会因为再次失败而回到可重试状态。"""

    event = _event(attempt_count=42)

    status = await _repository(event).mark_failed(
        event.id,
        safe_error="EventPublishError",
        max_attempts=10,
    )

    assert status is OutboxStatus.DEAD_LETTER
    assert event.next_retry_at is None


async def test_missing_event_is_reported_as_none() -> None:
    """事件已被清理时返回 None，而不是伪造一个状态。"""

    status = await _repository(None).mark_failed(
        uuid4(),
        safe_error="ConnectionError",
        max_attempts=10,
    )

    assert status is None


async def test_error_detail_is_truncated() -> None:
    """错误详情落库前必须截断，避免超长文本撑爆列。"""

    event = _event()

    await _repository(event).mark_failed(
        event.id,
        safe_error="x" * 5_000,
        max_attempts=10,
    )

    assert event.last_error is not None
    assert len(event.last_error) == 1_000


def test_dead_letter_is_a_distinct_terminal_status() -> None:
    """死信必须是独立取值，否则无法与 failed 分开统计。

    两者混在一起时，一条毒事件会永久钉住重试积压告警。
    """

    values = {status.value for status in OutboxStatus}
    assert "DEAD_LETTER" in values
    # 五个状态两两不同：任何合并都会让上面的分开统计失效。
    assert len(values) == len(list(OutboxStatus)) == 5


def test_max_attempts_must_be_positive() -> None:
    """上限为 0 会让首次失败即死信，配置层拦住。"""

    with pytest.raises(ValueError, match="outbox max attempts"):
        Settings(outbox_max_attempts=0)


def test_default_max_attempts_covers_a_meaningful_window() -> None:
    """默认上限应覆盖足够长的瞬时故障窗口，避免一次抖动就放弃。

    退避封顶 900 秒，因此窗口随次数近似线性增长。要求至少 2 小时，
    以扛过一次 Broker 停机加人工介入。
    """

    settings = Settings()
    total = sum(
        retry_delay(attempt).total_seconds() for attempt in range(1, settings.outbox_max_attempts)
    )
    assert total >= 7_200, "重试窗口不足 2 小时，Broker 短时不可用就会误判为毒事件"


def test_event_ids_are_uuid() -> None:
    """守卫：签名依赖 UUID，换成 str 会让 get_for_update 静默查不到。"""

    assert isinstance(_event().id, UUID)
