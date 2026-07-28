import argparse
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from app.evaluation import diagnosis, optimization
from app.evaluation.regression import (
    DIAGNOSIS_METRICS,
    OPTIMIZATION_METRICS,
    RegressionReportError,
    RegressionThresholds,
    compare_reports,
    load_report,
    release_gate_failures,
    sample_field_assertion,
    validate_expected_model,
    write_report,
)


def diagnosis_report() -> dict[str, object]:
    return {
        "versions": {"dataset_versions": ["v1.0"]},
        "automated_summary": {
            "provider_success_rate": 0.98,
            "schema_pass_rate": 1.0,
            "automated_pass_rate": 0.9,
            "input_quality_accuracy": 0.94,
            "primary_category_hit_rate": 0.86,
            "latency_ms": {"p95": 10_000},
            "cost_microunits": {"average": 5_000},
            "release_gates": {
                "sample_count_at_least_50": True,
                "schema_pass_100_percent": True,
            },
        },
        "samples": [
            {"sample_id": "sd_sample_001"},
            {"sample_id": "sd_sample_002"},
        ],
    }


def optimization_report() -> dict[str, object]:
    return {
        "versions": {"dataset_versions": ["v1.0"]},
        "automated_summary": {
            "provider_success_rate": 0.98,
            "schema_pass_rate": 1.0,
            "automated_pass_rate": 0.88,
            "expected_gate_accuracy": 0.92,
            "expected_failure_dimension_recall": 0.85,
            "critic_first_pass_rate": 0.8,
            "evaluation_critic_latency_ms": {"p95": 8_000},
            "evaluation_critic_cost_microunits": {"average": 4_000},
            "release_gates": {"schema_pass_100_percent": True},
        },
        "samples": [
            {"sample_id": "of_sample_001"},
            {"sample_id": "of_sample_002"},
        ],
    }


def comparison(
    current: dict[str, object],
    baseline: dict[str, object],
    *,
    optimization_metrics: bool = False,
) -> dict[str, object]:
    return compare_reports(
        current,
        baseline,
        metrics=OPTIMIZATION_METRICS if optimization_metrics else DIAGNOSIS_METRICS,
        thresholds=RegressionThresholds(),
        baseline_sha256="a" * 64,
    )


def summary(report: dict[str, object]) -> dict[str, object]:
    value = report["automated_summary"]
    assert isinstance(value, dict)
    return value


def failures(report: dict[str, object]) -> list[str]:
    value = report["failures"]
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return cast(list[str], value)


def test_diagnosis_comparison_passes_within_default_thresholds() -> None:
    baseline = diagnosis_report()
    current = deepcopy(baseline)
    current_summary = summary(current)
    current_summary["automated_pass_rate"] = 0.87
    current_summary["latency_ms"] = {"p95": 12_000}
    current_summary["cost_microunits"] = {"average": 6_000}

    result = comparison(current, baseline)

    assert result["status"] == "PASSED"
    assert result["failures"] == []
    assert result["baseline_sha256"] == "a" * 64


def test_diagnosis_comparison_blocks_quality_schema_latency_and_cost_regressions() -> None:
    baseline = diagnosis_report()
    current = deepcopy(baseline)
    current_summary = summary(current)
    current_summary["automated_pass_rate"] = 0.8699
    current_summary["schema_pass_rate"] = 0.99
    current_summary["latency_ms"] = {"p95": 12_001}
    current_summary["cost_microunits"] = {"average": 6_001}

    result = comparison(current, baseline)

    assert result["status"] == "FAILED"
    assert set(failures(result)) == {
        "METRIC_REGRESSION:schema_pass_rate",
        "METRIC_REGRESSION:automated_pass_rate",
        "METRIC_REGRESSION:latency_p95_ms",
        "METRIC_REGRESSION:average_cost_microunits",
    }


def test_comparison_requires_identical_sample_set_and_dataset_version() -> None:
    baseline = diagnosis_report()
    current = deepcopy(baseline)
    current["versions"] = {"dataset_versions": ["v1.1"]}
    current["samples"] = [{"sample_id": "sd_sample_003"}]

    result = comparison(current, baseline)

    assert result["status"] == "FAILED"
    assert failures(result)[:2] == [
        "SAMPLE_SET_MISMATCH",
        "DATASET_VERSION_MISMATCH",
    ]
    assert result["sample_set"] == {
        "baseline_count": 2,
        "current_count": 1,
        "match": False,
        "missing_sample_ids": ["sd_sample_001", "sd_sample_002"],
        "unexpected_sample_ids": ["sd_sample_003"],
    }


def test_optimization_comparison_uses_critic_latency_and_cost_metrics() -> None:
    baseline = optimization_report()
    current = deepcopy(baseline)
    current_summary = summary(current)
    current_summary["evaluation_critic_latency_ms"] = {"p95": 9_601}
    current_summary["evaluation_critic_cost_microunits"] = {"average": 4_801}

    result = comparison(current, baseline, optimization_metrics=True)

    assert result["status"] == "FAILED"
    assert set(failures(result)) == {
        "METRIC_REGRESSION:critic_latency_p95_ms",
        "METRIC_REGRESSION:average_critic_cost_microunits",
    }


def test_optional_metric_can_be_added_but_cannot_disappear() -> None:
    baseline_without_cost = diagnosis_report()
    summary(baseline_without_cost)["cost_microunits"] = {"average": None}
    current_with_cost = diagnosis_report()

    added = comparison(current_with_cost, baseline_without_cost)

    assert added["status"] == "PASSED"

    disappeared = comparison(baseline_without_cost, current_with_cost)

    assert disappeared["status"] == "FAILED"
    assert "METRIC_MISSING:average_cost_microunits" in failures(disappeared)


@pytest.mark.parametrize(
    "thresholds",
    [
        RegressionThresholds(max_quality_drop=-0.01),
        RegressionThresholds(max_latency_increase_percent=501),
        RegressionThresholds(max_cost_increase_percent=float("nan")),
    ],
)
def test_invalid_thresholds_are_rejected(thresholds: RegressionThresholds) -> None:
    with pytest.raises(RegressionReportError):
        thresholds.validate()


def test_release_gate_failures_are_fail_closed_and_sorted() -> None:
    report = diagnosis_report()
    summary(report)["release_gates"] = {
        "z_gate": False,
        "a_gate": True,
        "m_gate": "true",
    }

    assert release_gate_failures(report) == [
        "RELEASE_GATE_FAILED:m_gate",
        "RELEASE_GATE_FAILED:z_gate",
    ]
    assert release_gate_failures({"automated_summary": {}}) == ["RELEASE_GATES_MISSING"]


def test_report_round_trip_has_stable_hash_and_trailing_newline(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "reports" / "baseline.json"
    write_report(report_path, diagnosis_report())

    loaded, digest = load_report(report_path)

    assert loaded == diagnosis_report()
    assert len(digest) == 64
    assert report_path.read_bytes().endswith(b"\n")
    assert not report_path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("module", [diagnosis, optimization])
def test_cli_refuses_to_overwrite_its_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: ModuleType,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    original = b'{"sentinel":"preserve-me"}\n'
    baseline_path.write_bytes(original)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            manifest=tmp_path / "must-not-be-read.jsonl",
            output=baseline_path,
            reviews=None,
            split=None,
            max_samples=None,
            concurrency=2,
            baseline=baseline_path,
            max_quality_drop=0.03,
            max_latency_increase_percent=20.0,
            max_cost_increase_percent=20.0,
            expected_model=None,
            expected_critic_model=None,
            expected_production_model=None,
            enforce_release_gates=True,
        ),
    )

    assert module.main() == 2
    assert baseline_path.read_bytes() == original


@pytest.mark.parametrize(
    ("module", "dataset_validator"),
    [
        (diagnosis, "validate_diagnosis_release_dataset"),
        (optimization, "validate_optimization_release_dataset"),
    ],
)
def test_release_cli_requires_human_reviews_before_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: ModuleType,
    dataset_validator: str,
) -> None:
    output_path = tmp_path / f"{module.__name__}.json"
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            manifest=tmp_path / "manifest.jsonl",
            output=output_path,
            reviews=None,
            split=None,
            max_samples=None,
            concurrency=2,
            baseline=None,
            max_quality_drop=0.03,
            max_latency_increase_percent=20.0,
            max_cost_increase_percent=20.0,
            expected_model="candidate-model",
            expected_critic_model="candidate-model",
            expected_production_model=None,
            enforce_release_gates=True,
        ),
    )
    monkeypatch.setattr(module, "read_jsonl", lambda *_args: [object()])
    monkeypatch.setattr(module, dataset_validator, lambda _samples: {})
    monkeypatch.setattr(
        module,
        "Settings",
        lambda: (_ for _ in ()).throw(AssertionError("provider setup must not run")),
    )

    assert module.main() == 2
    report, _ = load_report(output_path)
    gate = cast(dict[str, object], report["command_gate"])
    assert gate["failures"] == ["RELEASE_HUMAN_REVIEW_POLICY_FAILED"]


def test_cli_writes_failure_report_then_returns_nonzero_for_regression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "candidate.json"
    write_report(baseline_path, diagnosis_report())
    current = diagnosis_report()
    summary(current)["automated_pass_rate"] = 0.5

    async def no_provider_call(
        *_args: object,
        **_kwargs: object,
    ) -> list[object]:
        return []

    monkeypatch.setattr(
        diagnosis,
        "parse_args",
        lambda: argparse.Namespace(
            manifest=tmp_path / "manifest.jsonl",
            output=output_path,
            reviews=None,
            split=None,
            max_samples=None,
            concurrency=2,
            baseline=baseline_path,
            max_quality_drop=0.03,
            max_latency_increase_percent=20.0,
            max_cost_increase_percent=20.0,
            expected_model="candidate-model",
            enforce_release_gates=False,
        ),
    )
    monkeypatch.setattr(diagnosis, "read_jsonl", lambda *_args: [object()])
    monkeypatch.setattr(diagnosis, "Settings", lambda: object())
    monkeypatch.setattr(diagnosis, "run_evaluation", no_provider_call)
    monkeypatch.setattr(
        diagnosis,
        "build_report",
        lambda _results, _reviews: current,
    )

    assert diagnosis.main() == 1
    written, _ = load_report(output_path)
    command_gate = written["command_gate"]
    regression = written["regression_comparison"]
    assert isinstance(command_gate, dict)
    assert isinstance(regression, dict)
    assert command_gate["status"] == "FAILED"
    assert regression["status"] == "FAILED"
    assertions = written["model_assertions"]
    assert isinstance(assertions, list)
    first_assertion = assertions[0]
    assert isinstance(first_assertion, dict)
    assert first_assertion["status"] == "FAILED"


def test_model_assertion_detects_fallback_and_invalid_identifiers() -> None:
    report = diagnosis_report()
    samples = report["samples"]
    assert isinstance(samples, list)
    typed_samples = cast(list[dict[str, object]], samples)
    typed_samples[0]["model"] = "candidate-model"
    typed_samples[1]["model"] = "fallback-model"

    assertion = sample_field_assertion(
        report,
        field="model",
        expected="candidate-model",
    )

    assert assertion["status"] == "FAILED"
    assert assertion["mismatch_sample_ids"] == ["sd_sample_002"]
    with pytest.raises(RegressionReportError):
        validate_expected_model("model with spaces")


def test_load_report_rejects_non_object_json(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(RegressionReportError):
        load_report(baseline_path)
