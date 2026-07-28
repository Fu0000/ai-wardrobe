from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

_CONFIRMATION = "I_ACCEPT_STAGING_WORKER_PAUSE_AND_REAL_AI_COST"
_CAPACITY_CONFIRMATION = "I_ACCEPT_STAGING_AI_COST_AND_ONCALL_WINDOW"
_MAX_DATASET_BYTES = 1024 * 1024
_STATE_VERSION = 1
_TERMINAL_STATUSES = {
    "COMPLETED",
    "FAILED_FINAL",
    "TIMED_OUT",
    "CANCELLED",
}
_PAUSED_STATUSES = {"PENDING", "QUEUED"}
_OCCASIONS = {
    "DAILY",
    "SCHOOL",
    "WORK",
    "INTERVIEW",
    "DATE",
    "SOCIAL",
    "FORMAL",
    "TRAVEL",
    "OTHER",
}


class QueueRecoveryAuditError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    access_token: str = field(repr=False)
    asset_id: UUID
    occasion: str


@dataclass(frozen=True, slots=True)
class PendingJob:
    index: int
    diagnosis_id: UUID
    job_id: UUID
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditState:
    version: int
    run_id: str
    expected_sha: str
    dataset_sha256: str
    created_count: int
    idempotent_replay_count: int
    paused_status_count: int
    records: list[PendingJob]

    @property
    def idempotency_key_count_matches(self) -> bool:
        expected = len(self.records)
        return (
            self.created_count == expected
            and self.idempotent_replay_count == expected
            and self.paused_status_count == expected
            and all(
                record.idempotency_key == f"queue-recovery-{self.run_id}-{record.index}"
                for record in self.records
            )
        )


def validate_staging_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise QueueRecoveryAuditError("Staging base URL must be a plain HTTPS origin")
    return value.rstrip("/")


def validate_expected_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise QueueRecoveryAuditError("expected application SHA must contain 40 hex characters")
    return normalized


def _read_private_json(
    path: Path,
    *,
    required_suffix: str | None,
    label: str,
) -> tuple[Any, bytes]:
    if required_suffix is not None and not path.name.endswith(required_suffix):
        raise QueueRecoveryAuditError(f"{label} file name is invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_DATASET_BYTES
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise QueueRecoveryAuditError(f"{label} file is unsafe")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(_MAX_DATASET_BYTES + 1)
        if len(raw) > _MAX_DATASET_BYTES:
            raise QueueRecoveryAuditError(f"{label} file is unsafe")
        return json.loads(raw), raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QueueRecoveryAuditError(f"{label} is unreadable or invalid JSON") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_dataset(path: Path) -> tuple[list[DatasetRecord], str]:
    payload, raw = _read_private_json(
        path,
        required_suffix=".local.json",
        label="dataset",
    )

    if not isinstance(payload, list) or not 1 <= len(payload) <= 50:
        raise QueueRecoveryAuditError("dataset must contain 1-50 records")

    records: list[DatasetRecord] = []
    seen_tokens: set[str] = set()
    for raw_record in payload:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "access_token",
            "asset_id",
            "occasion",
        }:
            raise QueueRecoveryAuditError("dataset record does not follow the contract")
        access_token = raw_record.get("access_token")
        asset_id_raw = raw_record.get("asset_id")
        occasion = raw_record.get("occasion")
        if (
            not isinstance(access_token, str)
            or len(access_token) < 16
            or access_token.startswith("<")
            or access_token in seen_tokens
        ):
            raise QueueRecoveryAuditError(
                "every dataset record requires a distinct non-placeholder token"
            )
        try:
            asset_id = UUID(str(asset_id_raw))
        except ValueError as error:
            raise QueueRecoveryAuditError("dataset asset ID must be a UUID") from error
        if asset_id.int == 0 or not isinstance(occasion, str) or occasion not in _OCCASIONS:
            raise QueueRecoveryAuditError("dataset asset or occasion is invalid")
        seen_tokens.add(access_token)
        records.append(
            DatasetRecord(
                access_token=access_token,
                asset_id=asset_id,
                occasion=occasion,
            )
        )
    return records, hashlib.sha256(raw).hexdigest()


def validate_capacity_stage(
    dataset: list[DatasetRecord],
    expected_size: int | None,
) -> None:
    if expected_size is not None and len(dataset) != expected_size:
        raise QueueRecoveryAuditError("dataset size does not match the approved capacity stage")


def _parse_object(response: httpx.Response, *, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise QueueRecoveryAuditError(f"{label} returned non-JSON") from error
    if not isinstance(payload, dict):
        raise QueueRecoveryAuditError(f"{label} response must be an object")
    return payload


def _parse_uuid(payload: dict[str, Any], key: str, *, label: str) -> UUID:
    try:
        return UUID(str(payload[key]))
    except (KeyError, ValueError) as error:
        raise QueueRecoveryAuditError(f"{label} response has an invalid {key}") from error


def _parse_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise QueueRecoveryAuditError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QueueRecoveryAuditError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise QueueRecoveryAuditError(f"{label} timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _atomic_json_write(path: Path, payload: dict[str, object], *, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise QueueRecoveryAuditError("output path already exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_state(path: Path, state: AuditState) -> None:
    _atomic_json_write(
        path,
        {
            "version": state.version,
            "run_id": state.run_id,
            "expected_sha": state.expected_sha,
            "dataset_sha256": state.dataset_sha256,
            "created_count": state.created_count,
            "idempotent_replay_count": state.idempotent_replay_count,
            "paused_status_count": state.paused_status_count,
            "records": [
                {
                    **asdict(record),
                    "diagnosis_id": str(record.diagnosis_id),
                    "job_id": str(record.job_id),
                    "created_at": record.created_at.isoformat(),
                }
                for record in state.records
            ],
        },
        mode=0o600,
    )


def load_state(path: Path) -> AuditState:
    payload, _ = _read_private_json(
        path,
        required_suffix=None,
        label="audit state",
    )
    if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
        raise QueueRecoveryAuditError("audit state version is invalid")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise QueueRecoveryAuditError("audit state records are invalid")
    records: list[PendingJob] = []
    for expected_index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict) or raw_record.get("index") != expected_index:
            raise QueueRecoveryAuditError("audit state record order is invalid")
        records.append(
            PendingJob(
                index=expected_index,
                diagnosis_id=_parse_uuid(raw_record, "diagnosis_id", label="audit state"),
                job_id=_parse_uuid(raw_record, "job_id", label="audit state"),
                idempotency_key=str(raw_record.get("idempotency_key", "")),
                created_at=_parse_datetime(
                    raw_record.get("created_at"),
                    label="audit state",
                ),
            )
        )
    try:
        state = AuditState(
            version=_STATE_VERSION,
            run_id=str(payload["run_id"]),
            expected_sha=validate_expected_sha(str(payload["expected_sha"])),
            dataset_sha256=str(payload["dataset_sha256"]),
            created_count=int(payload["created_count"]),
            idempotent_replay_count=int(payload["idempotent_replay_count"]),
            paused_status_count=int(payload["paused_status_count"]),
            records=records,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise QueueRecoveryAuditError("audit state metadata is invalid") from error
    if (
        not re.fullmatch(r"[0-9a-f]{32}", state.run_id)
        or not re.fullmatch(r"[0-9a-f]{64}", state.dataset_sha256)
        or not state.idempotency_key_count_matches
    ):
        raise QueueRecoveryAuditError("audit state integrity is invalid")
    return state


class QueueRecoveryAuditor:
    def __init__(
        self,
        *,
        base_url: str,
        dataset: list[DatasetRecord],
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._dataset = dataset
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=validate_staging_base_url(base_url),
            timeout=httpx.Timeout(30),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "ai-wardrobe-queue-recovery-audit/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        record: DatasetRecord,
        json_body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        expected_status: int,
        label: str,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {record.access_token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        response = await self._client.request(
            method,
            path,
            headers=headers,
            json=json_body,
        )
        if not response.headers.get("X-Request-ID") or not response.headers.get("X-Trace-ID"):
            raise QueueRecoveryAuditError(f"{label} is missing correlation headers")
        if response.status_code != expected_status:
            raise QueueRecoveryAuditError(f"{label} returned an unexpected status")
        return _parse_object(response, label=label)

    async def create_pending_control(
        self,
        *,
        expected_sha: str,
        dataset_sha256: str,
        settle_seconds: float,
    ) -> AuditState:
        run_id = uuid4().hex
        pending_jobs: list[PendingJob] = []
        for index, record in enumerate(self._dataset):
            idempotency_key = f"queue-recovery-{run_id}-{index}"
            created_at = datetime.now(UTC)
            payload = await self._request(
                "POST",
                "/api/v1/style-diagnoses",
                record=record,
                json_body={
                    "asset_id": str(record.asset_id),
                    "occasion": record.occasion,
                },
                idempotency_key=idempotency_key,
                expected_status=202,
                label=f"record {index} create",
            )
            diagnosis_id = _parse_uuid(payload, "id", label=f"record {index} create")
            job_id = _parse_uuid(payload, "job_id", label=f"record {index} create")
            if payload.get("reused") is not False or payload.get("job_status") not in (
                _PAUSED_STATUSES
            ):
                raise QueueRecoveryAuditError(f"record {index} was not newly queued")
            quota_remaining = payload.get("quota_remaining")
            if (
                not isinstance(quota_remaining, int)
                or isinstance(quota_remaining, bool)
                or quota_remaining < 0
            ):
                raise QueueRecoveryAuditError(f"record {index} has no verifiable quota reservation")
            replay = await self._request(
                "POST",
                "/api/v1/style-diagnoses",
                record=record,
                json_body={
                    "asset_id": str(record.asset_id),
                    "occasion": record.occasion,
                },
                idempotency_key=idempotency_key,
                expected_status=202,
                label=f"record {index} idempotency replay",
            )
            if (
                replay.get("reused") is not True
                or _parse_uuid(replay, "id", label=f"record {index} replay") != diagnosis_id
                or _parse_uuid(replay, "job_id", label=f"record {index} replay") != job_id
            ):
                raise QueueRecoveryAuditError(
                    f"record {index} idempotency replay created duplicate work"
                )
            pending_jobs.append(
                PendingJob(
                    index=index,
                    diagnosis_id=diagnosis_id,
                    job_id=job_id,
                    idempotency_key=idempotency_key,
                    created_at=created_at,
                )
            )

        await self._sleep(settle_seconds)
        for pending in pending_jobs:
            record = self._dataset[pending.index]
            payload = await self._request(
                "GET",
                f"/api/v1/style-diagnoses/{pending.diagnosis_id}",
                record=record,
                expected_status=200,
                label=f"record {pending.index} paused control",
            )
            if (
                _parse_uuid(payload, "job_id", label="paused control") != pending.job_id
                or payload.get("job_status") not in _PAUSED_STATUSES
            ):
                raise QueueRecoveryAuditError(
                    f"record {pending.index} executed while the Worker was paused"
                )
        return AuditState(
            version=_STATE_VERSION,
            run_id=run_id,
            expected_sha=validate_expected_sha(expected_sha),
            dataset_sha256=dataset_sha256,
            created_count=len(pending_jobs),
            idempotent_replay_count=len(pending_jobs),
            paused_status_count=len(pending_jobs),
            records=pending_jobs,
        )

    async def verify_recovery(
        self,
        state: AuditState,
        *,
        restored_at: datetime,
        timeout_seconds: float,
        poll_interval_seconds: float,
        original_replicas: int,
    ) -> dict[str, object]:
        if len(state.records) != len(self._dataset):
            raise QueueRecoveryAuditError("dataset and audit state sizes differ")
        pending = {record.index: record for record in state.records}
        terminal_statuses: dict[int, str] = {}
        recovery_seconds: dict[int, float] = {}
        deadline = monotonic() + timeout_seconds
        while pending and monotonic() < deadline:
            for index, pending_job in list(pending.items()):
                record = self._dataset[index]
                payload = await self._request(
                    "GET",
                    f"/api/v1/style-diagnoses/{pending_job.diagnosis_id}",
                    record=record,
                    expected_status=200,
                    label=f"record {index} recovery poll",
                )
                if (
                    _parse_uuid(payload, "id", label="recovery poll") != pending_job.diagnosis_id
                    or _parse_uuid(payload, "job_id", label="recovery poll") != pending_job.job_id
                ):
                    raise QueueRecoveryAuditError(f"record {index} changed Job identity")
                status_value = payload.get("job_status")
                if not isinstance(status_value, str):
                    raise QueueRecoveryAuditError(f"record {index} has no Job status")
                if status_value in _TERMINAL_STATUSES:
                    terminal_statuses[index] = status_value
                    recovery_seconds[index] = max(
                        0.0,
                        (datetime.now(UTC) - restored_at).total_seconds(),
                    )
                    pending.pop(index)
            if pending:
                await self._sleep(poll_interval_seconds)

        completed = sum(status == "COMPLETED" for status in terminal_statuses.values())
        failed_terminal = len(terminal_statuses) - completed
        stuck = len(pending)
        samples = list(recovery_seconds.values())
        queue_drain = max(samples, default=(datetime.now(UTC) - restored_at).total_seconds())
        passed = completed == len(state.records)
        return {
            "status": "PASSED" if passed else "FAILED",
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": state.run_id,
            "application_sha": state.expected_sha,
            "worker_deployment": "ai-wardrobe-worker-fast",
            "original_replicas": original_replicas,
            "dataset_size": len(state.records),
            "paused_control": {
                "created": state.created_count,
                "idempotent_replays": state.idempotent_replay_count,
                "pending_while_paused": state.paused_status_count,
                "correlation_headers_present": True,
            },
            "recovery": {
                "completed": completed,
                "terminal_failed": failed_terminal,
                "stuck": stuck,
                "success_rate": round(completed / len(state.records), 6),
                "queue_drain_seconds": round(max(0.0, queue_drain), 3),
                "p50_seconds": _percentile(samples, 50),
                "p90_seconds": _percentile(samples, 90),
                "p95_seconds": _percentile(samples, 95),
            },
            "sensitive_values_recorded": False,
        }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil((percentile / 100) * len(ordered)) - 1)
    return round(ordered[index], 3)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise QueueRecoveryAuditError(f"set {name}")
    return value


def _runtime_inputs(*, purpose: str = "queue_recovery") -> tuple[str, Path, str]:
    if purpose == "queue_recovery":
        confirmed = os.environ.get("AIW_QUEUE_RECOVERY_CONFIRMATION") == _CONFIRMATION
        error_message = "explicit Staging Worker pause confirmation is required"
    else:
        confirmed = os.environ.get("AIW_AI_CAPACITY_CONFIRMATION") == (_CAPACITY_CONFIRMATION)
        error_message = "explicit Staging AI cost and On-call confirmation is required"
    if not confirmed:
        raise QueueRecoveryAuditError(error_message)
    return (
        validate_staging_base_url(_required_env("STAGING_API_BASE_URL")),
        Path(_required_env("AIW_QUEUE_DATA_FILE")).expanduser().resolve(),
        validate_expected_sha(_required_env("STAGING_EXPECTED_SHA")),
    )


async def run_create(args: argparse.Namespace) -> dict[str, object]:
    base_url, dataset_path, expected_sha = _runtime_inputs()
    dataset, dataset_sha256 = load_dataset(dataset_path)
    auditor = QueueRecoveryAuditor(base_url=base_url, dataset=dataset)
    try:
        state = await auditor.create_pending_control(
            expected_sha=expected_sha,
            dataset_sha256=dataset_sha256,
            settle_seconds=args.settle_seconds,
        )
    finally:
        await auditor.close()
    write_state(args.state, state)
    return {
        "status": "PAUSED_CONTROL_PASSED",
        "dataset_size": len(state.records),
        "created": state.created_count,
        "idempotent_replays": state.idempotent_replay_count,
        "pending_while_paused": state.paused_status_count,
        "correlation_headers_present": True,
        "sensitive_values_recorded": False,
    }


def run_validate(args: argparse.Namespace) -> dict[str, object]:
    _, dataset_path, expected_sha = _runtime_inputs(purpose=args.purpose)
    dataset, _ = load_dataset(dataset_path)
    validate_capacity_stage(dataset, args.expected_size)
    return {
        "status": "VALIDATED",
        "application_sha": expected_sha,
        "dataset_size": len(dataset),
        "distinct_dedicated_users": len(dataset),
        "sensitive_values_recorded": False,
    }


async def run_verify(args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    base_url, dataset_path, expected_sha = _runtime_inputs()
    dataset, dataset_sha256 = load_dataset(dataset_path)
    state = load_state(args.state)
    if state.expected_sha != expected_sha or state.dataset_sha256 != dataset_sha256:
        raise QueueRecoveryAuditError("deployment or dataset changed during the audit")
    restored_at = _parse_datetime(
        _required_env("AIW_QUEUE_RESTORE_STARTED_AT"),
        label="Worker restore",
    )
    restore_age = (datetime.now(UTC) - restored_at).total_seconds()
    if restore_age < -5 or restore_age > 3_600:
        raise QueueRecoveryAuditError("Worker restore timestamp is outside the audit window")
    try:
        original_replicas = int(_required_env("AIW_QUEUE_ORIGINAL_REPLICAS"))
    except ValueError as error:
        raise QueueRecoveryAuditError("original Worker replica count is invalid") from error
    if not 1 <= original_replicas <= 10:
        raise QueueRecoveryAuditError("original Worker replica count is outside 1-10")

    auditor = QueueRecoveryAuditor(base_url=base_url, dataset=dataset)
    try:
        report = await auditor.verify_recovery(
            state,
            restored_at=restored_at,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            original_replicas=original_replicas,
        )
    finally:
        await auditor.close()
    _atomic_json_write(args.report, report, mode=0o600)
    return report, report["status"] == "PASSED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Staging Worker pause, backlog retention and queue recovery.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--expected-size", type=int, choices=(10, 30, 50))
    validate.add_argument(
        "--purpose",
        choices=("queue_recovery", "ai_capacity"),
        default="queue_recovery",
    )
    create = subparsers.add_parser("create")
    create.add_argument("--state", type=Path, required=True)
    create.add_argument("--settle-seconds", type=float, default=15)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--state", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--timeout-seconds", type=float, default=300)
    verify.add_argument("--poll-interval-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.command == "create" and not 5 <= args.settle_seconds <= 120:
        parser.error("--settle-seconds must be between 5 and 120")
    if args.command == "verify" and not 30 <= args.timeout_seconds <= 900:
        parser.error("--timeout-seconds must be between 30 and 900")
    if args.command == "verify" and not 1 <= args.poll_interval_seconds <= 10:
        parser.error("--poll-interval-seconds must be between 1 and 10")
    return args


def main() -> int:
    try:
        args = parse_args()
        if args.command == "validate":
            report, passed = run_validate(args), True
        elif args.command == "create":
            report, passed = asyncio.run(run_create(args)), True
        else:
            report, passed = asyncio.run(run_verify(args))
    except QueueRecoveryAuditError as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False))
        return 1
    except (httpx.HTTPError, OSError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": type(error).__name__},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
