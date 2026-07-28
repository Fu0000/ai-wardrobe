import json
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from pydantic import BaseModel

from app.evaluation.dataset_policy import DatasetPolicyError
from app.evaluation.diagnosis import (
    EvaluationSample,
    validate_diagnosis_release_dataset,
)
from app.evaluation.optimization import (
    OptimizationEvaluationSample,
    validate_optimization_release_dataset,
)
from scripts.eval_bundle import (
    EXPECTED_FILES,
    EvalBundleError,
    extract_eval_bundle,
)


def write_bundle(path: Path, files: dict[str, bytes]) -> None:
    with ZipFile(path, mode="w", compression=ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)


def valid_files() -> dict[str, bytes]:
    return {
        name: (b'{"samples":[]}\n' if name.endswith(".json") else b'{"sample_id":"x"}\n')
        for name in EXPECTED_FILES
    }


def release_diagnosis_manifest() -> bytes:
    occasions = ["DAILY", "SCHOOL", "WORK", "INTERVIEW", "DATE", "TRAVEL"]
    records: list[str] = []
    for index in range(50):
        tags = [
            f"body_type:{'straight' if index % 2 else 'curved'}",
            f"skin_tone:{'light' if index % 2 else 'deep'}",
            f"lighting:{'daylight' if index % 2 else 'indoor'}",
            f"camera_angle:{'front' if index % 2 else 'oblique'}",
            f"background_complexity:{'simple' if index % 2 else 'complex'}",
        ]
        if index < 5:
            tags.append("risk:prompt_injection")
        records.append(
            json.dumps(
                {
                    "sample_id": f"sd_release_{index:03d}",
                    "dataset_version": "v1.0",
                    "split": "validation",
                    "asset_reference": f"cos-private://evals/diagnosis/{index:03d}.jpg",
                    "occasion": occasions[index % len(occasions)],
                    "consent_reference": f"consent_{index:08d}",
                    "tags": tags,
                    "expected": {
                        "input_acceptable": index >= 10,
                        "primary_issue_categories": ["PROPORTION"],
                        "must_preserve": ["identity"],
                    },
                },
                separators=(",", ":"),
            )
        )
    return ("\n".join(records) + "\n").encode()


def release_optimization_manifest() -> bytes:
    bad_cases = [
        ("bad_case:identity_change", "IDENTITY"),
        ("bad_case:body_change", "IDENTITY"),
        ("bad_case:background_change", "BACKGROUND_AND_LIGHTING"),
        ("bad_case:pose_change", "POSE_AND_COMPOSITION"),
        ("bad_case:unmentioned_garment_change", "UNMENTIONED_GARMENTS"),
        ("bad_case:visual_artifact", "VISUAL_ARTIFACT"),
    ]
    records: list[str] = []
    for index in range(50):
        bad_case = bad_cases[index] if index < len(bad_cases) else None
        decision = "REJECT" if bad_case else "PASS"
        records.append(
            json.dumps(
                {
                    "sample_id": f"of_release_{index:03d}",
                    "dataset_version": "v1.0",
                    "split": "validation",
                    "source_reference": (
                        f"cos-private://evals/optimization/before-{index:03d}.jpg"
                    ),
                    "candidate_reference": (
                        f"cos-private://evals/optimization/after-{index:03d}.jpg"
                    ),
                    "consent_reference": f"consent_{index:08d}",
                    "production_image_model": "gpt-image-candidate",
                    "generation_attempt": 1,
                    "exposed_to_user": False,
                    "production_latency_ms": 20_000,
                    "production_cost_microunits": 100_000,
                    "tags": [bad_case[0]] if bad_case else ["normal_result"],
                    "plan": {
                        "level": 1,
                        "replacement_count": 0,
                        "changes": [
                            {
                                "priority": 1,
                                "action": "ADJUST_WEARING",
                                "instruction": "把上衣前摆轻收进裤腰。",
                                "reason": "只调整穿法即可抬高视觉腰线。",
                                "preserves": "原有上衣、裤子和配色",
                            }
                        ],
                        "preserve_invariants": [
                            "IDENTITY",
                            "FACE_HAIR_BODY",
                            "POSE_AND_COMPOSITION",
                            "BACKGROUND_AND_LIGHTING",
                            "UNMENTIONED_GARMENTS",
                            "LOGOS_AND_PATTERNS",
                        ],
                    },
                    "expected": {
                        "decision": decision,
                        "failure_dimensions": [bad_case[1]] if bad_case else [],
                    },
                },
                separators=(",", ":"),
            )
        )
    return ("\n".join(records) + "\n").encode()


def release_files() -> dict[str, bytes]:
    files = valid_files()
    files["diagnosis/manifest.jsonl"] = release_diagnosis_manifest()
    files["optimization/manifest.jsonl"] = release_optimization_manifest()
    return files


def parse_records[T: BaseModel](content: bytes, model: type[T]) -> list[T]:
    return [model.model_validate_json(line) for line in content.decode().splitlines()]


def test_extract_eval_bundle_accepts_only_the_contract_files(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    write_bundle(archive, files)

    extracted = extract_eval_bundle(archive, output)

    assert extracted == sorted(EXPECTED_FILES)
    for name, content in files.items():
        destination = output / name
        assert destination.read_bytes() == content
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_extract_eval_bundle_enforces_release_dataset_policy(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    write_bundle(archive, release_files())

    extract_eval_bundle(archive, output, enforce_release_policy=True)

    assert output.is_dir()


def test_extract_eval_bundle_rejects_policy_failure_atomically(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    write_bundle(archive, valid_files())

    with pytest.raises(EvalBundleError, match="release dataset policy failed"):
        extract_eval_bundle(archive, output, enforce_release_policy=True)

    assert not output.exists()


def test_diagnosis_policy_reports_only_machine_safe_rule_ids() -> None:
    samples = parse_records(release_diagnosis_manifest(), EvaluationSample)
    samples[0] = samples[0].model_copy(update={"consent_reference": "consent_example_only"})

    with pytest.raises(DatasetPolicyError) as captured:
        validate_diagnosis_release_dataset(samples)

    assert captured.value.violations == ("diagnosis.authorization_references_valid",)
    assert "consent_example_only" not in str(captured.value)


def test_diagnosis_policy_requires_complete_distribution_contract() -> None:
    samples = parse_records(release_diagnosis_manifest(), EvaluationSample)
    for index, sample in enumerate(samples):
        samples[index] = sample.model_copy(
            update={
                "occasion": "DAILY",
                "tags": [
                    tag
                    for tag in sample.tags
                    if tag != "risk:prompt_injection" and not tag.startswith("body_type:")
                ],
            }
        )

    with pytest.raises(DatasetPolicyError) as captured:
        validate_diagnosis_release_dataset(samples)

    assert {
        "diagnosis.required_occasion_coverage",
        "diagnosis.prompt_injection_sample_count_at_least_5",
        "diagnosis.body_type_metadata_complete",
        "diagnosis.body_type_at_least_2_values",
    }.issubset(captured.value.violations)


def test_optimization_policy_requires_all_fidelity_bad_cases() -> None:
    samples = parse_records(
        release_optimization_manifest(),
        OptimizationEvaluationSample,
    )
    body_sample = samples[1]
    samples[1] = body_sample.model_copy(update={"tags": ["normal_result"]})

    with pytest.raises(DatasetPolicyError) as captured:
        validate_optimization_release_dataset(samples)

    assert captured.value.violations == ("optimization.bad_case_body_change_present",)


def test_optimization_policy_rejects_missing_production_evidence() -> None:
    samples = parse_records(
        release_optimization_manifest(),
        OptimizationEvaluationSample,
    )
    samples[10] = samples[10].model_copy(update={"production_latency_ms": None})

    with pytest.raises(DatasetPolicyError) as captured:
        validate_optimization_release_dataset(samples)

    assert captured.value.violations == ("optimization.production_metrics_complete",)


@pytest.mark.parametrize(
    "unexpected_name",
    [
        "../outside.json",
        "/absolute.json",
        "diagnosis/extra.json",
    ],
)
def test_extract_eval_bundle_rejects_unexpected_paths(
    tmp_path: Path,
    unexpected_name: str,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    files[unexpected_name] = b"unsafe"
    write_bundle(archive, files)

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert not output.exists()


def test_extract_eval_bundle_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    files = valid_files()
    symlink_name = "diagnosis/manifest.jsonl"
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            if name != symlink_name:
                bundle.writestr(name, content)
        symlink = ZipInfo(symlink_name)
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(symlink, b"../../outside")

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert not output.exists()


def test_extract_eval_bundle_does_not_merge_into_existing_directory(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "evals.zip"
    output = tmp_path / "extracted"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    write_bundle(archive, valid_files())

    with pytest.raises(EvalBundleError):
        extract_eval_bundle(archive, output)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
