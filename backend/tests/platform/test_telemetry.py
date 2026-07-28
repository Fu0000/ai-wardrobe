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


class _Gauge:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Mapping[str, object]]] = []

    def set(self, value: int, attributes: Mapping[str, object]) -> None:
        self.calls.append((value, attributes))


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


def test_orphan_cleanup_metric_uses_bounded_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _Counter()
    monkeypatch.setattr(telemetry, "_orphan_upload_cleanup", counter)

    telemetry.record_orphan_upload_cleanup(outcome="cleaned", count=3)
    telemetry.record_orphan_upload_cleanup(outcome="skipped_stale", count=0)

    assert counter.calls == [(3, {"aiw.outcome": "cleaned"})]
    with pytest.raises(ValueError, match="unsupported orphan"):
        telemetry.record_orphan_upload_cleanup(outcome="asset-id", count=1)
    with pytest.raises(ValueError, match="nonnegative"):
        telemetry.record_orphan_upload_cleanup(outcome="cleaned", count=-1)


def test_dependency_readiness_uses_bounded_names_and_numeric_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gauge = _Gauge()
    monkeypatch.setattr(telemetry, "_dependency_readiness", gauge)

    telemetry.record_dependency_readiness(
        {
            "database": "ok",
            "redis": "failed",
            "object_storage": "disabled",
            "user-supplied": "failed",
        }
    )

    assert gauge.calls == [
        (1, {"aiw.dependency.name": "database"}),
        (0, {"aiw.dependency.name": "redis"}),
        (1, {"aiw.dependency.name": "object_storage"}),
    ]
