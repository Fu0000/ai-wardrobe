from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings
from app.database import models as database_models  # noqa: F401  # 注册全部模型
from app.database.session import Database
from app.modules.identity.models import User, UserProfile, UserStatus
from app.modules.identity.security import AccessTokenService

_FIXTURE_DISPLAY_NAME = "__aiw_local_performance_fixture__"
_MAX_INPUT_BYTES = 10 * 1024 * 1024


class PerformanceToolError(ValueError):
    pass


def validate_local_fixture_settings(settings: Settings) -> None:
    if settings.environment not in {"local", "test"}:
        raise PerformanceToolError("performance fixtures are restricted to local/test")
    hostname = urlsplit(settings.database_url.replace("+psycopg", "")).hostname
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PerformanceToolError("performance fixtures require a loopback database")


def _atomic_json_write(path: Path, payload: dict[str, object], *, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise PerformanceToolError("output path already exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_text_write(path: Path, content: str, *, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise PerformanceToolError("output path already exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_fixture_file(path: Path, *, user_id: UUID, access_token: str) -> None:
    _atomic_json_write(
        path,
        {
            "access_token": access_token,
            "fixture_type": "local_api_performance",
            "user_id": str(user_id),
        },
        mode=0o600,
    )


def _path_exists_or_is_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_INPUT_BYTES:
            raise PerformanceToolError("input file is unsafe or oversized")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PerformanceToolError("input file is unreadable or invalid JSON") from error
    if not isinstance(payload, dict):
        raise PerformanceToolError("input JSON must be an object")
    return payload


async def create_fixture(settings: Settings, output: Path) -> UUID:
    validate_local_fixture_settings(settings)
    if await asyncio.to_thread(_path_exists_or_is_symlink, output):
        raise PerformanceToolError("fixture output path already exists")

    database = Database(settings)
    user_id = uuid4()
    committed = False
    try:
        async with database.session_factory() as session:
            user = User(id=user_id, status=UserStatus.ACTIVE)
            user.profile = UserProfile(
                display_name=_FIXTURE_DISPLAY_NAME,
                has_ai_processing_consent=False,
            )
            session.add(user)
            await session.commit()
            committed = True

        token = AccessTokenService(settings).issue(user_id).value
        try:
            write_fixture_file(output, user_id=user_id, access_token=token)
        except BaseException:
            if committed:
                await _delete_fixture_user(database, user_id)
            raise
    finally:
        await database.dispose()
    return user_id


async def _delete_fixture_user(database: Database, user_id: UUID) -> bool:
    async with database.session_factory() as session:
        result = await session.execute(
            select(User)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(
                User.id == user_id,
                UserProfile.display_name == _FIXTURE_DISPLAY_NAME,
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            return False
        await session.delete(user)
        await session.commit()
        return True


async def delete_fixture(settings: Settings, fixture_path: Path) -> bool:
    validate_local_fixture_settings(settings)
    payload = _load_json_object(fixture_path)
    if payload.get("fixture_type") != "local_api_performance":
        raise PerformanceToolError("fixture type is invalid")
    try:
        user_id = UUID(str(payload["user_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise PerformanceToolError("fixture user ID is invalid") from error

    database = Database(settings)
    try:
        return await _delete_fixture_user(database, user_id)
    finally:
        await database.dispose()


def _metric_values(summary: dict[str, Any], name: str) -> dict[str, float | int]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        raise PerformanceToolError("k6 summary has no metrics object")
    metric = metrics.get(name)
    if not isinstance(metric, dict):
        raise PerformanceToolError(f"k6 summary is missing metric: {name}")
    nested_values = metric.get("values")
    values = nested_values if isinstance(nested_values, dict) else metric
    result = {
        str(key): value
        for key, value in values.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    if "rate" not in result and "value" in result:
        result["rate"] = result["value"]
    return result


def _number(values: dict[str, float | int], name: str) -> float:
    value = values.get(name)
    if value is None:
        raise PerformanceToolError(f"k6 metric is missing value: {name}")
    return float(value)


def build_api_report(
    summary: dict[str, Any],
    *,
    git_sha: str,
    k6_image: str,
    k6_exit_code: int,
    start_rate: int,
    target_rate: int,
    duration: str,
) -> str:
    requests = _metric_values(summary, "http_reqs")
    failures = _metric_values(summary, "http_req_failed{scenario:api_reads}")
    latency = _metric_values(summary, "http_req_duration{scenario:api_reads}")
    business = _metric_values(summary, "business_success")
    status = "PASS" if k6_exit_code == 0 else "FAIL"
    generated_at = datetime.now(UTC).isoformat()

    return "\n".join(
        [
            "# Local API performance baseline",
            "",
            f"- Conclusion: **{status}**",
            f"- Generated at: `{generated_at}`",
            f"- Application SHA: `{git_sha}`",
            f"- Load generator: `{k6_image}`",
            "- Target: local loopback API and local PostgreSQL/Redis only",
            f"- Traffic: `{start_rate}` → `{target_rate}` iterations/s for `{duration}`",
            f"- k6 exit code: `{k6_exit_code}`",
            "",
            "## Results",
            "",
            "| Metric | Result | Gate |",
            "|:---|---:|:---|",
            f"| Requests | {_number(requests, 'count'):.0f} | informational |",
            f"| Throughput | {_number(requests, 'rate'):.2f}/s | informational |",
            f"| Business success | {_number(business, 'rate') * 100:.3f}% | >99% |",
            f"| HTTP failure rate | {_number(failures, 'rate') * 100:.3f}% | <1% |",
            f"| P50 latency | {_number(latency, 'med'):.2f} ms | informational |",
            f"| P90 latency | {_number(latency, 'p(90)'):.2f} ms | informational |",
            f"| P95 latency | {_number(latency, 'p(95)'):.2f} ms | <500 ms |",
            f"| P99 latency | {_number(latency, 'p(99)'):.2f} ms | <1000 ms |",
            f"| Max latency | {_number(latency, 'max'):.2f} ms | informational |",
            "",
            "This report contains no access token, user identifier, or private asset reference.",
            (
                "It validates the local API baseline only and does not replace "
                "Staging AI/COS capacity evidence."
            ),
            "",
        ]
    )


def write_api_report(
    summary_path: Path,
    output: Path,
    **metadata: str | int,
) -> None:
    summary = _load_json_object(summary_path)
    report = build_api_report(
        summary,
        git_sha=str(metadata["git_sha"]),
        k6_image=str(metadata["k6_image"]),
        k6_exit_code=int(metadata["k6_exit_code"]),
        start_rate=int(metadata["start_rate"]),
        target_rate=int(metadata["target_rate"]),
        duration=str(metadata["duration"]),
    )
    _atomic_text_write(output, report, mode=0o644)


def build_ai_capacity_report(
    summary: dict[str, Any],
    *,
    git_sha: str,
    k6_image: str,
    k6_exit_code: int,
    stage: int,
    vus: int,
    api_replicas: int,
    worker_replicas: int,
) -> tuple[str, bool]:
    if not 1 <= vus <= 50 or not 1 <= api_replicas <= 10 or not 1 <= worker_replicas <= 10:
        raise PerformanceToolError("AI capacity execution metadata is outside safe bounds")
    iterations = _metric_values(summary, "iterations")
    requests = _metric_values(summary, "http_reqs")
    failures = _metric_values(summary, "http_req_failed{scenario:authorized_ai_jobs}")
    success = _metric_values(summary, "diagnosis_success")
    correlation = _metric_values(summary, "correlation_headers")
    latency = _metric_values(summary, "diagnosis_total_latency")
    iteration_count = _number(iterations, "count")
    success_rate = _number(success, "rate")
    correlation_rate = _number(correlation, "rate")
    failure_rate = _number(failures, "rate")
    p90 = _number(latency, "p(90)")
    p95 = _number(latency, "p(95)")
    passed = (
        k6_exit_code == 0
        and iteration_count == stage
        and success_rate > 0.94
        and correlation_rate == 1
        and failure_rate < 0.02
        and p90 < 20_000
        and p95 < 30_000
    )

    return (
        "\n".join(
            [
                "# Staging AI capacity stage",
                "",
                f"- Conclusion: **{'PASS' if passed else 'FAIL'}**",
                f"- Generated at: `{datetime.now(UTC).isoformat()}`",
                f"- Application SHA: `{git_sha}`",
                f"- Load generator: `{k6_image}`",
                f"- Approved stage: `{stage}` distinct dedicated users / Jobs",
                f"- Virtual users: `{vus}`",
                f"- API / ai_fast replicas: `{api_replicas}` / `{worker_replicas}`",
                f"- k6 exit code: `{k6_exit_code}`",
                "",
                "## Results",
                "",
                "| Metric | Result | Gate |",
                "|:---|---:|:---|",
                f"| Iterations | {iteration_count:.0f} | ={stage} |",
                f"| HTTP requests | {_number(requests, 'count'):.0f} | informational |",
                f"| Diagnosis success | {success_rate * 100:.3f}% | ≥95% |",
                f"| Correlation headers | {correlation_rate * 100:.3f}% | 100% |",
                f"| HTTP failure rate | {failure_rate * 100:.3f}% | <2% |",
                f"| Diagnosis P90 | {p90:.2f} ms | <20000 ms |",
                f"| Diagnosis P95 | {p95:.2f} ms | <30000 ms |",
                "",
                (
                    "The report contains no access token, user identifier, "
                    "Job/Asset ID, private URL, photo content, or Provider response."
                ),
                (
                    "Pass this stage before increasing 10 → 30 → 50. Queue recovery, "
                    "resource watermarks, database/COS/Quota reconciliation and device "
                    "evidence remain separate release requirements."
                ),
                "",
            ]
        ),
        passed,
    )


def write_ai_capacity_report(
    summary_path: Path,
    output: Path,
    *,
    git_sha: str,
    k6_image: str,
    k6_exit_code: int,
    stage: int,
    vus: int,
    api_replicas: int,
    worker_replicas: int,
) -> bool:
    summary = _load_json_object(summary_path)
    report, passed = build_ai_capacity_report(
        summary,
        git_sha=git_sha,
        k6_image=k6_image,
        k6_exit_code=k6_exit_code,
        stage=stage,
        vus=vus,
        api_replicas=api_replicas,
        worker_replicas=worker_replicas,
    )
    _atomic_text_write(output, report, mode=0o600)
    return passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage safe local performance fixtures.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, required=True)

    delete = subparsers.add_parser("delete")
    delete.add_argument("--fixture", type=Path, required=True)

    report = subparsers.add_parser("report")
    report.add_argument("--summary", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--git-sha", required=True)
    report.add_argument("--k6-image", required=True)
    report.add_argument("--k6-exit-code", type=int, required=True)
    report.add_argument("--start-rate", type=int, required=True)
    report.add_argument("--target-rate", type=int, required=True)
    report.add_argument("--duration", required=True)

    ai_report = subparsers.add_parser("ai-report")
    ai_report.add_argument("--summary", type=Path, required=True)
    ai_report.add_argument("--output", type=Path, required=True)
    ai_report.add_argument("--git-sha", required=True)
    ai_report.add_argument("--k6-image", required=True)
    ai_report.add_argument("--k6-exit-code", type=int, required=True)
    ai_report.add_argument("--stage", type=int, choices=(10, 30, 50), required=True)
    ai_report.add_argument("--vus", type=int, required=True)
    ai_report.add_argument("--api-replicas", type=int, required=True)
    ai_report.add_argument("--worker-replicas", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings()
    try:
        if args.command == "create":
            user_id = asyncio.run(create_fixture(settings, args.output))
            print(json.dumps({"status": "created", "user_id": str(user_id)}))
        elif args.command == "delete":
            deleted = asyncio.run(delete_fixture(settings, args.fixture))
            print(json.dumps({"deleted": deleted, "status": "cleaned"}))
        elif args.command == "report":
            write_api_report(
                args.summary,
                args.output,
                git_sha=args.git_sha,
                k6_image=args.k6_image,
                k6_exit_code=args.k6_exit_code,
                start_rate=args.start_rate,
                target_rate=args.target_rate,
                duration=args.duration,
            )
            print(json.dumps({"report": str(args.output), "status": "written"}))
        else:
            passed = write_ai_capacity_report(
                args.summary,
                args.output,
                git_sha=args.git_sha,
                k6_image=args.k6_image,
                k6_exit_code=args.k6_exit_code,
                stage=args.stage,
                vus=args.vus,
                api_replicas=args.api_replicas,
                worker_replicas=args.worker_replicas,
            )
            print(json.dumps({"report": str(args.output), "status": "written"}))
            if not passed:
                return 1
    except PerformanceToolError as error:
        raise SystemExit(f"performance tool rejected the request: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
