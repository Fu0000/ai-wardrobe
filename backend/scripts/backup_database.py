import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
COUNT_SQL = """
SELECT json_build_object(
  'users', (SELECT count(*) FROM users),
  'assets', (SELECT count(*) FROM user_assets),
  'jobs', (SELECT count(*) FROM generation_jobs),
  'invocations', (SELECT count(*) FROM ai_invocations),
  'deletions', (SELECT count(*) FROM deletion_jobs),
  'feedback', (SELECT count(*) FROM beta_feedback)
)::text;
"""
IDENTITY_SQL = """
SELECT json_build_object(
  'database_name', current_database(),
  'migration_version', COALESCE(
    (SELECT version_num FROM alembic_version LIMIT 1),
    'missing'
  )
)::text;
"""


class BackupError(Exception):
    pass


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BackupError(f"required executable is unavailable: {name}")
    return path


def service_name() -> str:
    value = os.environ.get("AIW_BACKUP_PGSERVICE", "")
    if not SERVICE_PATTERN.fullmatch(value):
        raise BackupError("AIW_BACKUP_PGSERVICE must be a valid libpq service name")
    return value


def secure_output_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise BackupError("backup output directory must not be accessible by group or others")
    return path


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
        raise BackupError("database metadata query returned an invalid payload")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def create_backup(*, output_dir: Path, service: str) -> dict[str, object]:
    psql = executable("psql")
    pg_dump = executable("pg_dump")
    identity = run_json_query(psql, service, IDENTITY_SQL)
    counts = run_json_query(psql, service, COUNT_SQL)
    created_at = datetime.now(UTC)
    stem = f"ai-wardrobe-{created_at:%Y%m%dT%H%M%SZ}-{token_hex(4)}"
    backup_path = output_dir / f"{stem}.dump"
    manifest_path = output_dir / f"{stem}.manifest.json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{stem}.",
        suffix=".dump",
        dir=output_dir,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        subprocess.run(  # noqa: S603
            [
                pg_dump,
                "--dbname",
                f"service={service}",
                "--format=custom",
                "--compress=zstd:6",
                "--no-owner",
                "--no-acl",
                "--file",
                str(temporary_path),
            ],
            check=True,
        )
        temporary_path.chmod(0o600)
        checksum = sha256_file(temporary_path)
        size_bytes = temporary_path.stat().st_size
        if size_bytes <= 0:
            raise BackupError("pg_dump produced an empty backup")
        temporary_path.replace(backup_path)
        manifest: dict[str, object] = {
            "format_version": 1,
            "created_at": created_at.isoformat(),
            "backup_file": backup_path.name,
            "sha256": checksum,
            "size_bytes": size_bytes,
            "database_name": identity.get("database_name"),
            "migration_version": identity.get("migration_version"),
            "row_counts": counts,
        }
        atomic_json(manifest_path, manifest)
        return {
            "status": "PASSED",
            "backup": str(backup_path),
            "manifest": str(manifest_path),
            "sha256": checksum,
            "size_bytes": size_bytes,
        }
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        backup_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise BackupError(type(error).__name__) from error
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a checksummed logical backup using a libpq service.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Encrypted, access-controlled backup directory",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        report = create_backup(
            output_dir=secure_output_directory(args.output_dir),
            service=service_name(),
        )
    except BackupError as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
