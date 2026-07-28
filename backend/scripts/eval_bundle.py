from __future__ import annotations

import argparse
import json
import shutil
import stat
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZipInfo

from app.evaluation.dataset_policy import DatasetPolicyError
from app.evaluation.diagnosis import (
    EvaluationSample,
    HumanReview,
    read_jsonl,
    validate_diagnosis_release_dataset,
    validate_diagnosis_release_reviews,
)
from app.evaluation.optimization import (
    OptimizationEvaluationSample,
    OptimizationHumanReview,
    validate_optimization_release_dataset,
    validate_optimization_release_reviews,
)

EXPECTED_FILES = frozenset(
    {
        "diagnosis/baseline.json",
        "diagnosis/manifest.jsonl",
        "diagnosis/reviews.jsonl",
        "optimization/baseline.json",
        "optimization/manifest.jsonl",
        "optimization/reviews.jsonl",
    }
)
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024


class EvalBundleError(ValueError):
    pass


def _validate_entries(entries: list[ZipInfo]) -> None:
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise EvalBundleError("eval bundle contains duplicate paths")
    if set(names) != EXPECTED_FILES:
        raise EvalBundleError("eval bundle file set does not match the required contract")

    total_size = 0
    for entry in entries:
        file_type = (entry.external_attr >> 16) & 0o170000
        if entry.is_dir() or file_type == stat.S_IFLNK:
            raise EvalBundleError("eval bundle contains a directory or symbolic link")
        if entry.file_size > MAX_FILE_BYTES:
            raise EvalBundleError("eval bundle contains an oversized file")
        total_size += entry.file_size
    if total_size > MAX_TOTAL_BYTES:
        raise EvalBundleError("eval bundle uncompressed size exceeds the limit")


def _extract_file(bundle: ZipFile, entry: ZipInfo, destination: Path) -> int:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = 0
    with bundle.open(entry, mode="r") as source, destination.open(mode="xb") as target:
        while chunk := source.read(64 * 1024):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                raise EvalBundleError("eval bundle file exceeded its declared size limit")
            target.write(chunk)
    destination.chmod(0o600)
    return written


def _validate_release_policy(extracted: Path) -> None:
    try:
        diagnosis_samples = read_jsonl(
            extracted / "diagnosis/manifest.jsonl",
            EvaluationSample,
        )
        optimization_samples = read_jsonl(
            extracted / "optimization/manifest.jsonl",
            OptimizationEvaluationSample,
        )
        diagnosis_reviews = read_jsonl(
            extracted / "diagnosis/reviews.jsonl",
            HumanReview,
        )
        optimization_reviews = read_jsonl(
            extracted / "optimization/reviews.jsonl",
            OptimizationHumanReview,
        )
        validate_diagnosis_release_dataset(diagnosis_samples)
        validate_optimization_release_dataset(optimization_samples)
        validate_diagnosis_release_reviews(
            diagnosis_samples,
            diagnosis_reviews,
            expected_model=(diagnosis_reviews[0].model_version if diagnosis_reviews else None),
        )
        validate_optimization_release_reviews(
            optimization_samples,
            optimization_reviews,
        )
    except (DatasetPolicyError, OSError, ValueError) as error:
        raise EvalBundleError("eval bundle release dataset policy failed") from error


def extract_eval_bundle(
    archive: Path,
    output: Path,
    *,
    enforce_release_policy: bool = False,
) -> list[str]:
    if output.exists() or output.is_symlink():
        raise EvalBundleError("eval bundle output directory must not already exist")
    try:
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise EvalBundleError("eval bundle archive exceeds the size limit")
    except OSError as error:
        raise EvalBundleError("eval bundle archive is unreadable") from error

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".eval-bundle-",
            dir=output.parent,
        )
    )
    try:
        with ZipFile(archive) as bundle:
            entries = bundle.infolist()
            _validate_entries(entries)
            actual_size = 0
            for entry in entries:
                actual_size += _extract_file(bundle, entry, staging / entry.filename)
                if actual_size > MAX_TOTAL_BYTES:
                    raise EvalBundleError("eval bundle uncompressed size exceeds the limit")
            corrupt_path = bundle.testzip()
            if corrupt_path is not None:
                raise EvalBundleError("eval bundle checksum verification failed")
        if enforce_release_policy:
            _validate_release_policy(staging)
        staging.replace(output)
    except EvalBundleError:
        shutil.rmtree(staging)
        raise
    except (BadZipFile, OSError, RuntimeError) as error:
        shutil.rmtree(staging)
        raise EvalBundleError("eval bundle could not be safely extracted") from error
    return sorted(EXPECTED_FILES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and safely extract the private AI Eval bundle."
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--enforce-release-policy", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        extracted = extract_eval_bundle(
            args.archive,
            args.output,
            enforce_release_policy=args.enforce_release_policy,
        )
    except EvalBundleError as error:
        raise SystemExit(f"eval bundle rejected: {error}") from error
    print(json.dumps({"status": "PASSED", "files": extracted}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
