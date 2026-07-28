import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


class RegressionReportError(ValueError):
    pass


_MODEL_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}")


@dataclass(frozen=True, slots=True)
class RegressionThresholds:
    max_quality_drop: float = 0.03
    max_latency_increase_percent: float = 20.0
    max_cost_increase_percent: float = 20.0

    def validate(self) -> None:
        if not 0 <= self.max_quality_drop <= 1:
            raise RegressionReportError("max quality drop must be between 0 and 1")
        if not 0 <= self.max_latency_increase_percent <= 500:
            raise RegressionReportError("max latency increase percent must be between 0 and 500")
        if not 0 <= self.max_cost_increase_percent <= 500:
            raise RegressionReportError("max cost increase percent must be between 0 and 500")


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    path: tuple[str, ...]
    direction: Literal["higher", "lower"]
    threshold: Literal[
        "quality",
        "quality_zero_drop",
        "latency",
        "cost",
    ]
    required: bool = True


DIAGNOSIS_METRICS = (
    MetricSpec(
        "provider_success_rate",
        ("automated_summary", "provider_success_rate"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "schema_pass_rate",
        ("automated_summary", "schema_pass_rate"),
        "higher",
        "quality_zero_drop",
    ),
    MetricSpec(
        "automated_pass_rate",
        ("automated_summary", "automated_pass_rate"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "input_quality_accuracy",
        ("automated_summary", "input_quality_accuracy"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "primary_category_hit_rate",
        ("automated_summary", "primary_category_hit_rate"),
        "higher",
        "quality",
        required=False,
    ),
    MetricSpec(
        "latency_p95_ms",
        ("automated_summary", "latency_ms", "p95"),
        "lower",
        "latency",
    ),
    MetricSpec(
        "average_cost_microunits",
        ("automated_summary", "cost_microunits", "average"),
        "lower",
        "cost",
        required=False,
    ),
)

OPTIMIZATION_METRICS = (
    MetricSpec(
        "provider_success_rate",
        ("automated_summary", "provider_success_rate"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "schema_pass_rate",
        ("automated_summary", "schema_pass_rate"),
        "higher",
        "quality_zero_drop",
    ),
    MetricSpec(
        "automated_pass_rate",
        ("automated_summary", "automated_pass_rate"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "expected_gate_accuracy",
        ("automated_summary", "expected_gate_accuracy"),
        "higher",
        "quality",
    ),
    MetricSpec(
        "expected_failure_dimension_recall",
        ("automated_summary", "expected_failure_dimension_recall"),
        "higher",
        "quality",
        required=False,
    ),
    MetricSpec(
        "critic_first_pass_rate",
        ("automated_summary", "critic_first_pass_rate"),
        "higher",
        "quality",
        required=False,
    ),
    MetricSpec(
        "critic_latency_p95_ms",
        ("automated_summary", "evaluation_critic_latency_ms", "p95"),
        "lower",
        "latency",
    ),
    MetricSpec(
        "average_critic_cost_microunits",
        (
            "automated_summary",
            "evaluation_critic_cost_microunits",
            "average",
        ),
        "lower",
        "cost",
        required=False,
    ),
)


def load_report(path: Path) -> tuple[dict[str, object], str]:
    try:
        content = path.read_bytes()
        payload = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise RegressionReportError("baseline report is unreadable or invalid") from error
    if not isinstance(payload, dict):
        raise RegressionReportError("baseline report root must be an object")
    return payload, hashlib.sha256(content).hexdigest()


def _nested(report: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = report
    for segment in path:
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _sample_ids(report: dict[str, object]) -> set[str]:
    samples = report.get("samples")
    if not isinstance(samples, list):
        raise RegressionReportError("evaluation report samples must be an array")
    sample_ids: set[str] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            raise RegressionReportError("evaluation report contains an invalid sample")
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise RegressionReportError("evaluation sample ID is missing")
        if sample_id in sample_ids:
            raise RegressionReportError("evaluation report contains duplicate sample IDs")
        sample_ids.add(sample_id)
    return sample_ids


def _dataset_versions(report: dict[str, object]) -> tuple[str, ...]:
    raw = _nested(report, ("versions", "dataset_versions"))
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise RegressionReportError("dataset versions are missing from evaluation report")
    return tuple(sorted(raw))


def _allowed_boundary(
    baseline: float,
    *,
    spec: MetricSpec,
    thresholds: RegressionThresholds,
) -> float:
    if spec.threshold == "quality_zero_drop":
        return baseline
    if spec.threshold == "quality":
        return baseline - thresholds.max_quality_drop
    increase = (
        thresholds.max_latency_increase_percent
        if spec.threshold == "latency"
        else thresholds.max_cost_increase_percent
    )
    return baseline * (1 + increase / 100)


def compare_reports(
    current: dict[str, object],
    baseline: dict[str, object],
    *,
    metrics: tuple[MetricSpec, ...],
    thresholds: RegressionThresholds,
    baseline_sha256: str,
) -> dict[str, object]:
    thresholds.validate()
    current_samples = _sample_ids(current)
    baseline_samples = _sample_ids(baseline)
    current_versions = _dataset_versions(current)
    baseline_versions = _dataset_versions(baseline)
    failures: list[str] = []
    if current_samples != baseline_samples:
        failures.append("SAMPLE_SET_MISMATCH")
    if current_versions != baseline_versions:
        failures.append("DATASET_VERSION_MISMATCH")

    metric_results: list[dict[str, object]] = []
    for spec in metrics:
        current_value = _number(_nested(current, spec.path))
        baseline_value = _number(_nested(baseline, spec.path))
        if baseline_value is None and current_value is None and not spec.required:
            metric_results.append(
                {
                    "metric": spec.name,
                    "status": "NOT_AVAILABLE",
                    "passed": True,
                    "baseline": None,
                    "current": None,
                }
            )
            continue
        if baseline_value is None or current_value is None:
            passed = not spec.required and baseline_value is None
            metric_results.append(
                {
                    "metric": spec.name,
                    "status": "MISSING_VALUE",
                    "passed": passed,
                    "baseline": baseline_value,
                    "current": current_value,
                }
            )
            if not passed:
                failures.append(f"METRIC_MISSING:{spec.name}")
            continue

        boundary = _allowed_boundary(
            baseline_value,
            spec=spec,
            thresholds=thresholds,
        )
        passed = (
            current_value >= boundary if spec.direction == "higher" else current_value <= boundary
        )
        metric_results.append(
            {
                "metric": spec.name,
                "status": "COMPARED",
                "passed": passed,
                "direction": spec.direction,
                "baseline": baseline_value,
                "current": current_value,
                "delta": round(current_value - baseline_value, 6),
                "allowed_boundary": round(boundary, 6),
            }
        )
        if not passed:
            failures.append(f"METRIC_REGRESSION:{spec.name}")

    return {
        "schema_version": 1,
        "status": "PASSED" if not failures else "FAILED",
        "baseline_sha256": baseline_sha256,
        "thresholds": asdict(thresholds),
        "sample_set": {
            "baseline_count": len(baseline_samples),
            "current_count": len(current_samples),
            "match": current_samples == baseline_samples,
            "missing_sample_ids": sorted(baseline_samples - current_samples),
            "unexpected_sample_ids": sorted(current_samples - baseline_samples),
        },
        "dataset_versions": {
            "baseline": list(baseline_versions),
            "current": list(current_versions),
            "match": current_versions == baseline_versions,
        },
        "metrics": metric_results,
        "failures": failures,
    }


def release_gate_failures(report: dict[str, object]) -> list[str]:
    gates = _nested(report, ("automated_summary", "release_gates"))
    if not isinstance(gates, dict) or not gates:
        return ["RELEASE_GATES_MISSING"]
    return [
        f"RELEASE_GATE_FAILED:{name}"
        for name, passed in sorted(gates.items())
        if passed is not True
    ]


def validate_expected_model(value: str | None) -> None:
    if value is not None and _MODEL_IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise RegressionReportError("expected model identifier is invalid")


def sample_field_assertion(
    report: dict[str, object],
    *,
    field: str,
    expected: str,
) -> dict[str, object]:
    validate_expected_model(expected)
    _sample_ids(report)
    samples = report["samples"]
    if not isinstance(samples, list):
        raise RegressionReportError("evaluation report samples must be an array")
    mismatches = sorted(
        sample["sample_id"]
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("sample_id"), str)
        and sample.get(field) != expected
    )
    return {
        "field": field,
        "expected": expected,
        "status": "PASSED" if not mismatches else "FAILED",
        "sample_count": len(samples),
        "mismatch_count": len(mismatches),
        "mismatch_sample_ids": mismatches,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    content = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
