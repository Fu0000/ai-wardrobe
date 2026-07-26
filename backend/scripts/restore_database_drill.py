import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ISOLATED_NAME_PATTERN = re.compile(r"(restore|drill|sandbox)", re.IGNORECASE)
REQUIRED_CONFIRMATION = "RESTORE_TO_ISOLATED_DATABASE"
IDENTITY_SQL = """
SELECT json_build_object(
  'database_name', current_database(),
  'public_table_count', (
    SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
  )
)::text;
"""
VERIFY_SQL = """
SELECT json_build_object(
  'database_name', current_database(),
  'migration_version', COALESCE(
    (SELECT version_num FROM alembic_version LIMIT 1),
    'missing'
  ),
  'row_counts', json_build_object(
    'users', (SELECT count(*) FROM users),
    'assets', (SELECT count(*) FROM user_assets),
    'jobs', (SELECT count(*) FROM generation_jobs),
    'invocations', (SELECT count(*) FROM ai_invocations),
    'deletions', (SELECT count(*) FROM deletion_jobs),
    'feedback', (SELECT count(*) FROM beta_feedback)
  ),
  'invariant_failures', json_build_object(
    'source_photo_without_asset', (
      SELECT count(*)
      FROM source_photos source
      LEFT JOIN user_assets asset ON asset.id = source.asset_id
      WHERE asset.id IS NULL
    ),
    'diagnosis_without_job', (
      SELECT count(*)
      FROM style_diagnoses diagnosis
      LEFT JOIN generation_jobs job ON job.id = diagnosis.job_id
      WHERE job.id IS NULL
    ),
    'optimization_without_diagnosis', (
      SELECT count(*)
      FROM style_optimization_results optimization
      LEFT JOIN style_diagnoses diagnosis ON diagnosis.id = optimization.diagnosis_id
      WHERE diagnosis.id IS NULL
    ),
    'invocation_without_job', (
      SELECT count(*)
      FROM ai_invocations invocation
      LEFT JOIN generation_jobs job ON job.id = invocation.job_id
      WHERE job.id IS NULL
    )
  )
)::text;
"""


class RestoreDrillError(Exception):
    pass


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RestoreDrillError(f"required executable is unavailable: {name}")
    return path


def restore_service_name() -> str:
    value = os.environ.get("AIW_RESTORE_PGSERVICE", "")
    if not SERVICE_PATTERN.fullmatch(value):
        raise RestoreDrillError("AIW_RESTORE_PGSERVICE must be a valid libpq service name")
    if not ISOLATED_NAME_PATTERN.search(value):
        raise RestoreDrillError("restore service name must identify a restore/drill/sandbox target")
    if os.environ.get("AIW_RESTORE_CONFIRM") != REQUIRED_CONFIRMATION:
        raise RestoreDrillError(f"set AIW_RESTORE_CONFIRM={REQUIRED_CONFIRMATION}")
    return value


def run_json_query(psql: str, service: str, sql: str) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        [
            psql,
            "--dbname",
            f"service={service}",
            "--no-align",
            "--tuples-only",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout.strip())
    if not isinstance(payload, dict):
        raise RestoreDrillError("database verification returned an invalid payload")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RestoreDrillError("backup manifest is invalid") from error
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise RestoreDrillError("backup manifest format is unsupported")
    return payload


def validate_source(backup: Path, manifest: dict[str, Any]) -> None:
    if not backup.is_file():
        raise RestoreDrillError("backup file does not exist")
    if backup.name != manifest.get("backup_file"):
        raise RestoreDrillError("backup filename does not match the manifest")
    expected_checksum = manifest.get("sha256")
    if not isinstance(expected_checksum, str) or len(expected_checksum) != 64:
        raise RestoreDrillError("backup manifest checksum is invalid")
    if sha256_file(backup) != expected_checksum:
        raise RestoreDrillError("backup checksum mismatch")


def restore_and_verify(
    *,
    backup: Path,
    manifest: dict[str, Any],
    service: str,
    rto_seconds: int,
    rpo_seconds: int,
) -> dict[str, object]:
    psql = executable("psql")
    pg_restore = executable("pg_restore")
    target = run_json_query(psql, service, IDENTITY_SQL)
    database_name = target.get("database_name")
    if not isinstance(database_name, str) or not ISOLATED_NAME_PATTERN.search(database_name):
        raise RestoreDrillError("target database name is not an isolated restore database")
    if target.get("public_table_count") != 0:
        raise RestoreDrillError("restore target must have an empty public schema")

    started_at = datetime.now(UTC)
    started = monotonic()
    subprocess.run(  # noqa: S603
        [
            pg_restore,
            "--dbname",
            f"service={service}",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            str(backup),
        ],
        check=True,
    )
    duration_seconds = round(monotonic() - started, 3)
    verification = run_json_query(psql, service, VERIFY_SQL)
    invariant_failures = verification.get("invariant_failures")
    if not isinstance(invariant_failures, dict) or any(
        value != 0 for value in invariant_failures.values()
    ):
        raise RestoreDrillError("restored database invariant verification failed")
    if verification.get("migration_version") != manifest.get("migration_version"):
        raise RestoreDrillError("restored migration version does not match the backup")
    if verification.get("row_counts") != manifest.get("row_counts"):
        raise RestoreDrillError("restored row counts do not match the backup")

    created_at_raw = manifest.get("created_at")
    if not isinstance(created_at_raw, str):
        raise RestoreDrillError("backup creation time is missing")
    backup_created_at = datetime.fromisoformat(created_at_raw)
    if backup_created_at.tzinfo is None:
        raise RestoreDrillError("backup creation time must include a timezone")
    backup_age_seconds = round((started_at - backup_created_at).total_seconds(), 3)
    return {
        "status": (
            "PASSED"
            if duration_seconds <= rto_seconds and backup_age_seconds <= rpo_seconds
            else "FAILED"
        ),
        "started_at": started_at.isoformat(),
        "database_name": database_name,
        "backup_file": backup.name,
        "migration_version": verification.get("migration_version"),
        "duration_seconds": duration_seconds,
        "rto_target_seconds": rto_seconds,
        "backup_age_seconds": backup_age_seconds,
        "rpo_target_seconds": rpo_seconds,
        "row_counts": verification.get("row_counts"),
        "invariant_failures": invariant_failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore a logical backup into an empty isolated database and verify it.",
    )
    parser.add_argument("--backup", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--rto-seconds", type=int, default=14_400)
    parser.add_argument("--rpo-seconds", type=int, default=86_400)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        backup = Path(args.backup).expanduser().resolve()
        manifest_path = Path(args.manifest).expanduser().resolve()
        report_path = Path(args.report).expanduser().resolve()
        if report_path.exists():
            raise RestoreDrillError("report path already exists")
        if args.rto_seconds <= 0 or args.rpo_seconds <= 0:
            raise RestoreDrillError("RTO and RPO targets must be positive")
        manifest = load_manifest(manifest_path)
        validate_source(backup, manifest)
        report = restore_and_verify(
            backup=backup,
            manifest=manifest,
            service=restore_service_name(),
            rto_seconds=args.rto_seconds,
            rpo_seconds=args.rpo_seconds,
        )
        report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_path.chmod(0o600)
    except (RestoreDrillError, OSError, subprocess.CalledProcessError) as error:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": (
                        str(error) if isinstance(error, RestoreDrillError) else type(error).__name__
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
