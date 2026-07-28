import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from scripts.performance.queue_recovery_audit import (
    AuditState,
    DatasetRecord,
    PendingJob,
    QueueRecoveryAuditError,
    QueueRecoveryAuditor,
    load_dataset,
    load_state,
    validate_staging_base_url,
    write_state,
)


def _write_dataset(path: Path, *, token: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "access_token": token,
                    "asset_id": str(uuid4()),
                    "occasion": "DAILY",
                }
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_dataset_requires_private_local_file_and_safe_staging_origin(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "queue.local.json"
    _write_dataset(dataset_path, token=f"test-{uuid4().hex}")

    records, digest = load_dataset(dataset_path)

    assert len(records) == 1
    assert len(digest) == 64
    dataset_path.chmod(0o644)
    with pytest.raises(QueueRecoveryAuditError, match="unsafe"):
        load_dataset(dataset_path)
    with pytest.raises(QueueRecoveryAuditError, match="plain HTTPS"):
        validate_staging_base_url("http://staging.example.com")
    with pytest.raises(QueueRecoveryAuditError, match="plain HTTPS"):
        validate_staging_base_url("https://user:pass@staging.example.com")


def test_audit_state_is_private_and_integrity_checked(tmp_path: Path) -> None:
    run_id = "a" * 32
    state = AuditState(
        version=1,
        run_id=run_id,
        expected_sha="b" * 40,
        dataset_sha256="c" * 64,
        created_count=1,
        idempotent_replay_count=1,
        paused_status_count=1,
        records=[
            PendingJob(
                index=0,
                diagnosis_id=uuid4(),
                job_id=uuid4(),
                idempotency_key=f"queue-recovery-{run_id}-0",
                created_at=datetime.now(UTC),
            )
        ],
    )
    state_path = tmp_path / "state.json"

    write_state(state_path, state)

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert load_state(state_path) == state
    state_path.chmod(0o644)
    with pytest.raises(QueueRecoveryAuditError, match="unsafe"):
        load_state(state_path)


@pytest.mark.asyncio
async def test_queue_recovery_passes_without_recording_sensitive_values() -> None:
    access_token = f"test-{uuid4().hex}"
    asset_id = uuid4()
    diagnosis_id = uuid4()
    job_id = uuid4()
    worker_restored = False
    post_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        assert request.headers["Authorization"] == f"Bearer {access_token}"
        headers = {"X-Request-ID": uuid4().hex, "X-Trace-ID": uuid4().hex}
        if request.method == "POST":
            post_calls += 1
            payload: dict[str, Any] = {
                "id": str(diagnosis_id),
                "job_id": str(job_id),
                "job_status": "PENDING",
                "reused": post_calls > 1,
                "quota_remaining": 2 if post_calls == 1 else None,
            }
            return httpx.Response(202, json=payload, headers=headers)
        return httpx.Response(
            200,
            json={
                "id": str(diagnosis_id),
                "job_id": str(job_id),
                "job_status": "COMPLETED" if worker_restored else "PENDING",
            },
            headers=headers,
        )

    async def no_wait(seconds: float) -> None:
        assert seconds >= 0

    auditor = QueueRecoveryAuditor(
        base_url="https://staging.example.com",
        dataset=[
            DatasetRecord(
                access_token=access_token,
                asset_id=asset_id,
                occasion="DAILY",
            )
        ],
        transport=httpx.MockTransport(handler),
        sleep=no_wait,
    )
    try:
        state = await auditor.create_pending_control(
            expected_sha="d" * 40,
            dataset_sha256="e" * 64,
            settle_seconds=15,
        )
        worker_restored = True
        report = await auditor.verify_recovery(
            state,
            restored_at=datetime.now(UTC),
            timeout_seconds=30,
            poll_interval_seconds=1,
            original_replicas=2,
        )
    finally:
        await auditor.close()

    serialized = json.dumps(report)
    recovery = report["recovery"]
    assert post_calls == 2
    assert report["status"] == "PASSED"
    assert isinstance(recovery, dict)
    assert recovery["completed"] == 1
    assert access_token not in serialized
    assert str(asset_id) not in serialized
    assert str(diagnosis_id) not in serialized
    assert str(job_id) not in serialized


@pytest.mark.asyncio
async def test_queue_recovery_fails_if_job_executes_while_worker_is_paused() -> None:
    diagnosis_id = uuid4()
    job_id = uuid4()
    post_calls = 0

    async def unsafe_handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        headers = {"X-Request-ID": uuid4().hex, "X-Trace-ID": uuid4().hex}
        if request.method == "POST":
            post_calls += 1
            return httpx.Response(
                202,
                json={
                    "id": str(diagnosis_id),
                    "job_id": str(job_id),
                    "job_status": "PENDING",
                    "reused": post_calls > 1,
                    "quota_remaining": 2 if post_calls == 1 else None,
                },
                headers=headers,
            )
        return httpx.Response(
            200,
            json={
                "id": str(diagnosis_id),
                "job_id": str(job_id),
                "job_status": "COMPLETED",
            },
            headers=headers,
        )

    async def no_wait(seconds: float) -> None:
        del seconds

    auditor = QueueRecoveryAuditor(
        base_url="https://staging.example.com",
        dataset=[
            DatasetRecord(
                access_token=f"test-{uuid4().hex}",
                asset_id=uuid4(),
                occasion="WORK",
            )
        ],
        transport=httpx.MockTransport(unsafe_handler),
        sleep=no_wait,
    )
    try:
        with pytest.raises(QueueRecoveryAuditError, match="executed while"):
            await auditor.create_pending_control(
                expected_sha="f" * 40,
                dataset_sha256="0" * 64,
                settle_seconds=15,
            )
    finally:
        await auditor.close()
