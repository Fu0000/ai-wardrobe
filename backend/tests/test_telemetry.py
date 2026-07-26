from collections.abc import Mapping

import pytest

from app.core import telemetry


class _Counter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Mapping[str, object]]] = []

    def add(self, value: int, attributes: Mapping[str, object]) -> None:
        self.calls.append((value, attributes))


class _Histogram:
    def record(self, value: int, attributes: Mapping[str, object]) -> None:
        del value, attributes


def test_worker_span_records_business_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _Counter()
    monkeypatch.setattr(telemetry, "_worker_executions", counter)
    monkeypatch.setattr(telemetry, "_worker_duration", _Histogram())

    with telemetry.WorkerSpan(
        name="celery run_deletion",
        headers={},
        job_id="job-1",
    ) as operation:
        operation.set_outcome("FAILED_FINAL")

    assert counter.calls == [
        (
            1,
            {
                "messaging.system": "celery",
                "messaging.operation.name": "celery run_deletion",
                "aiw.outcome": "failed_final",
            },
        )
    ]


def test_product_action_rejects_unbounded_labels() -> None:
    with pytest.raises(ValueError, match="unsupported product action"):
        telemetry.record_product_action(action="user_supplied", outcome="created")

    with pytest.raises(ValueError, match="unsupported product action outcome"):
        telemetry.record_product_action(
            action="diagnosis_requested",
            outcome="user_supplied",
        )
