from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.evaluation.dataset_policy import (
    DatasetPolicyError,
    common_release_violations,
    private_reference_violations,
    tag_value,
)
from app.evaluation.dataset_policy import private_object_key as private_object_key
from app.evaluation.regression import (
    DIAGNOSIS_METRICS,
    RegressionReportError,
    RegressionThresholds,
    compare_reports,
    load_report,
    release_gate_failures,
    sample_field_assertion,
    validate_expected_model,
    write_report,
)
from app.modules.ai.contracts import StructuredVisionRequest
from app.modules.ai.gateway import AIGateway, AllProvidersFailedError
from app.modules.ai.openai_provider import OpenAIStructuredVisionProvider
from app.modules.ai.policies import diagnosis_policy
from app.modules.assets.storage import ObjectStorage, build_object_storage
from app.modules.diagnosis.prompt import STYLE_DIAGNOSIS_PROMPT
from app.modules.diagnosis.schema import DiagnosisOutput, InputQuality

EVALUATOR_VERSION = "style-diagnosis-evaluator-v1.0.0"
RELEASE_OCCASIONS = frozenset({"DAILY", "SCHOOL", "WORK", "INTERVIEW", "DATE", "TRAVEL"})
DIAGNOSIS_DIVERSITY_TAG_PREFIXES = (
    "body_type:",
    "skin_tone:",
    "lighting:",
    "camera_angle:",
    "background_complexity:",
)
Occasion = Literal[
    "DAILY",
    "SCHOOL",
    "WORK",
    "INTERVIEW",
    "DATE",
    "SOCIAL",
    "TRAVEL",
    "OTHER",
]


class ExpectedDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_acceptable: bool
    primary_issue_categories: list[str]
    must_preserve: list[str]
    review_notes: str | None = None


class EvaluationSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^sd_[a-z0-9_]+$")
    dataset_version: str = Field(pattern=r"^v[0-9]+\.[0-9]+$")
    split: Literal["tune", "validation", "regression"]
    asset_reference: str = Field(min_length=1)
    occasion: Occasion
    consent_reference: str = Field(
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$",
    )
    tags: list[str] = Field(default_factory=list)
    expected: ExpectedDiagnosis


class ReviewScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_issue_hit: int = Field(ge=1, le=5)
    strength_specificity: int = Field(ge=1, le=5)
    actionability: int = Field(ge=1, le=5)
    occasion_fit: int = Field(ge=1, le=5)
    minimal_change: int = Field(ge=1, le=5)
    respectful_expression: int = Field(ge=1, le=5)


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^sd_[a-z0-9_]+$")
    dataset_version: str
    evaluator_version: str
    reviewer_id: str = Field(min_length=3, max_length=80)
    scores: ReviewScores
    hard_failure_codes: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=1_000)


@dataclass(frozen=True, slots=True)
class AutomatedSampleResult:
    sample_id: str
    dataset_version: str
    split: str
    provider: str | None
    model: str | None
    prompt_version: str
    schema_version: str
    evaluator_version: str
    latency_ms: int
    estimated_cost_microunits: int | None
    provider_success: bool
    schema_valid: bool
    input_quality_match: bool
    primary_category_hit: bool | None
    automated_pass: bool
    failure_code: str | None


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    records: list[T] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                records.append(model.model_validate_json(stripped))
            except ValidationError as error:
                raise ValueError(f"invalid record at line {line_number}") from error
    return records


def validate_diagnosis_release_dataset(
    samples: list[EvaluationSample],
    *,
    release_split: str = "validation",
) -> dict[str, object]:
    selected = [sample for sample in samples if sample.split == release_split]
    violations = common_release_violations(
        domain="diagnosis",
        sample_ids=[sample.sample_id for sample in samples],
        dataset_versions=[sample.dataset_version for sample in samples],
        splits=[sample.split for sample in samples],
        consent_references=[sample.consent_reference for sample in samples],
        tag_sets=[sample.tags for sample in samples],
        release_split=release_split,
    )
    violations.extend(
        private_reference_violations(
            domain="diagnosis",
            references=[sample.asset_reference for sample in samples],
            require_unique=True,
        )
    )

    occasion_counts = Counter(sample.occasion for sample in selected)
    if not RELEASE_OCCASIONS.issubset(occasion_counts):
        violations.append("diagnosis.required_occasion_coverage")

    unacceptable_count = sum(not sample.expected.input_acceptable for sample in selected)
    if unacceptable_count < 10:
        violations.append("diagnosis.low_quality_sample_count_at_least_10")

    prompt_injection_count = sum("risk:prompt_injection" in sample.tags for sample in selected)
    if prompt_injection_count < 5:
        violations.append("diagnosis.prompt_injection_sample_count_at_least_5")

    diversity: dict[str, int] = {}
    for prefix in DIAGNOSIS_DIVERSITY_TAG_PREFIXES:
        values = [tag_value(sample.tags, prefix) for sample in selected]
        dimension = prefix.removesuffix(":")
        diversity[dimension] = len({value for value in values if value is not None})
        if any(value is None for value in values):
            violations.append(f"diagnosis.{dimension}_metadata_complete")
        if diversity[dimension] < 2:
            violations.append(f"diagnosis.{dimension}_at_least_2_values")

    if violations:
        raise DatasetPolicyError(violations)
    return {
        "status": "PASSED",
        "release_split": release_split,
        "sample_count": len(selected),
        "occasion_counts": dict(sorted(occasion_counts.items())),
        "low_quality_sample_count": unacceptable_count,
        "prompt_injection_sample_count": prompt_injection_count,
        "diversity_distinct_value_counts": diversity,
    }


def contract_checks(
    sample: EvaluationSample,
    output: DiagnosisOutput,
) -> tuple[bool, bool | None, bool]:
    expected_acceptable = sample.expected.input_acceptable
    quality_match = (output.input_quality == InputQuality.ACCEPTABLE) == expected_acceptable
    category_hit: bool | None = None
    if expected_acceptable:
        category_hit = bool(
            output.primary_issue
            and output.primary_issue.category.value in sample.expected.primary_issue_categories
        )
    passed = quality_match and (category_hit is not False)
    return quality_match, category_hit, passed


def percentile(values: list[int], target: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((target / 100) * len(ordered)) - 1)
    return ordered[index]


def aggregate_automated(
    results: list[AutomatedSampleResult],
) -> dict[str, object]:
    total = len(results)
    safe_denominator = max(total, 1)
    latencies = [result.latency_ms for result in results]
    costs = [
        result.estimated_cost_microunits
        for result in results
        if result.estimated_cost_microunits is not None
    ]
    category_results = [
        result.primary_category_hit for result in results if result.primary_category_hit is not None
    ]
    failures = Counter(result.failure_code for result in results if result.failure_code is not None)
    provider_success_rate = sum(result.provider_success for result in results) / safe_denominator
    schema_pass_rate = sum(result.schema_valid for result in results) / safe_denominator
    automated_pass_rate = sum(result.automated_pass for result in results) / safe_denominator
    report: dict[str, object] = {
        "sample_count": total,
        "provider_success_rate": round(provider_success_rate, 4),
        "schema_pass_rate": round(schema_pass_rate, 4),
        "automated_pass_rate": round(automated_pass_rate, 4),
        "input_quality_accuracy": round(
            sum(result.input_quality_match for result in results) / safe_denominator,
            4,
        ),
        "primary_category_hit_rate": (
            round(sum(bool(value) for value in category_results) / len(category_results), 4)
            if category_results
            else None
        ),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p95": percentile(latencies, 95),
        },
        "cost_microunits": {
            "average": round(sum(costs) / len(costs)) if costs else None,
            "p95": percentile(costs, 95),
            "total": sum(costs) if costs else None,
            "coverage_rate": round(len(costs) / safe_denominator, 4),
        },
        "failures": dict(sorted(failures.items())),
    }
    report["release_gates"] = {
        "sample_count_at_least_50": total >= 50,
        "provider_success_at_least_95_percent": provider_success_rate >= 0.95,
        "schema_pass_100_percent": schema_pass_rate == 1,
        "p95_under_30_seconds": bool(
            latencies
            and percentile(latencies, 95) is not None
            and percentile(latencies, 95) < 30_000  # type: ignore[operator]
        ),
    }
    return report


def review_passes(review: HumanReview) -> bool:
    values = list(review.scores.model_dump().values())
    return (
        not review.hard_failure_codes
        and (sum(values) / len(values)) >= 4
        and review.scores.primary_issue_hit >= 4
        and review.scores.actionability >= 4
    )


def aggregate_reviews(reviews: list[HumanReview]) -> dict[str, object]:
    dimensions: dict[str, list[int]] = defaultdict(list)
    reviews_by_sample: dict[str, list[HumanReview]] = defaultdict(list)
    for review in reviews:
        reviews_by_sample[review.sample_id].append(review)
        for dimension, score in review.scores.model_dump().items():
            dimensions[dimension].append(score)

    disagreement_samples: list[str] = []
    for sample_id, sample_reviews in reviews_by_sample.items():
        for dimension in ReviewScores.model_fields:
            values = [getattr(review.scores, dimension) for review in sample_reviews]
            if values and max(values) - min(values) >= 2:
                disagreement_samples.append(sample_id)
                break

    dimension_report = {
        dimension: {
            "count": len(scores),
            "average": round(sum(scores) / len(scores), 3) if scores else None,
        }
        for dimension, scores in dimensions.items()
    }
    under_reviewed = sorted(
        sample_id
        for sample_id, sample_reviews in reviews_by_sample.items()
        if len({review.reviewer_id for review in sample_reviews}) < 2
    )
    return {
        "review_count": len(reviews),
        "sample_count": len(reviews_by_sample),
        "pass_rate": (
            round(sum(review_passes(review) for review in reviews) / len(reviews), 4)
            if reviews
            else 0
        ),
        "hard_failure_review_count": sum(bool(review.hard_failure_codes) for review in reviews),
        "dimensions": dimension_report,
        "coverage_gate_20_per_dimension": bool(dimensions)
        and all(len(scores) >= 20 for scores in dimensions.values())
        and len(dimensions) == len(ReviewScores.model_fields),
        "samples_with_fewer_than_two_reviewers": under_reviewed,
        "samples_requiring_arbitration": sorted(set(disagreement_samples)),
    }


class DiagnosisEvaluationRunner:
    def __init__(self, *, settings: Settings, storage: ObjectStorage) -> None:
        if not settings.openai_enabled:
            raise ValueError("OpenAI must be enabled for live evaluation")
        if not settings.cos_enabled:
            raise ValueError("COS must be enabled for live evaluation")
        self._settings = settings
        self._storage = storage
        self._provider = OpenAIStructuredVisionProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )
        self._gateway = AIGateway(
            structured_vision_providers=(self._provider,),
        )

    async def close(self) -> None:
        await self._provider.close()

    async def evaluate(self, sample: EvaluationSample) -> AutomatedSampleResult:
        started_at = perf_counter()
        provider: str | None = None
        model: str | None = None
        estimated_cost: int | None = None
        provider_succeeded = False
        try:
            image_url = await self._storage.create_download_url(
                object_key=private_object_key(sample.asset_reference),
                expires_in_seconds=self._settings.cos_download_url_ttl_seconds,
            )
            response = await self._gateway.structured_vision(
                request=StructuredVisionRequest(
                    image_url=image_url,
                    prompt=STYLE_DIAGNOSIS_PROMPT.instructions,
                    output_schema=STYLE_DIAGNOSIS_PROMPT.output_schema(),
                    metadata={"user_context": STYLE_DIAGNOSIS_PROMPT.user_context(sample.occasion)},
                ),
                policy=diagnosis_policy(self._settings),
            )
            provider = response.provider
            model = response.model
            estimated_cost = response.usage.estimated_cost_microunits
            provider_succeeded = True
            output = DiagnosisOutput.model_validate(response.output)
            quality_match, category_hit, passed = contract_checks(sample, output)
            return AutomatedSampleResult(
                sample_id=sample.sample_id,
                dataset_version=sample.dataset_version,
                split=sample.split,
                provider=provider,
                model=model,
                prompt_version=STYLE_DIAGNOSIS_PROMPT.prompt_version,
                schema_version=STYLE_DIAGNOSIS_PROMPT.schema_version,
                evaluator_version=EVALUATOR_VERSION,
                latency_ms=round((perf_counter() - started_at) * 1_000),
                estimated_cost_microunits=estimated_cost,
                provider_success=True,
                schema_valid=True,
                input_quality_match=quality_match,
                primary_category_hit=category_hit,
                automated_pass=passed,
                failure_code=None if passed else "EXPECTED_OUTPUT_MISMATCH",
            )
        except AllProvidersFailedError as error:
            failure_code = (
                f"PROVIDER_{error.attempts[-1].error_code.value}"
                if error.attempts
                else "PROVIDER_UNAVAILABLE"
            )
        except ValidationError:
            failure_code = "SCHEMA_INVALID"
        except ValueError:
            failure_code = "INVALID_EVAL_ASSET_REFERENCE"
        except Exception:
            failure_code = "EVALUATION_INTERNAL_ERROR"
        return AutomatedSampleResult(
            sample_id=sample.sample_id,
            dataset_version=sample.dataset_version,
            split=sample.split,
            provider=provider,
            model=model,
            prompt_version=STYLE_DIAGNOSIS_PROMPT.prompt_version,
            schema_version=STYLE_DIAGNOSIS_PROMPT.schema_version,
            evaluator_version=EVALUATOR_VERSION,
            latency_ms=round((perf_counter() - started_at) * 1_000),
            estimated_cost_microunits=estimated_cost,
            provider_success=provider_succeeded,
            schema_valid=False,
            input_quality_match=False,
            primary_category_hit=None,
            automated_pass=False,
            failure_code=failure_code,
        )


async def run_evaluation(
    samples: list[EvaluationSample],
    *,
    settings: Settings,
    concurrency: int,
) -> list[AutomatedSampleResult]:
    runner = DiagnosisEvaluationRunner(
        settings=settings,
        storage=build_object_storage(settings),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(sample: EvaluationSample) -> AutomatedSampleResult:
        async with semaphore:
            return await runner.evaluate(sample)

    try:
        return list(await asyncio.gather(*(evaluate_one(sample) for sample in samples)))
    finally:
        await runner.close()


def build_report(
    results: list[AutomatedSampleResult],
    reviews: list[HumanReview] | None = None,
) -> dict[str, object]:
    versions = {
        "dataset_versions": sorted({result.dataset_version for result in results}),
        "model_versions": sorted({result.model for result in results if result.model is not None}),
        "prompt_version": STYLE_DIAGNOSIS_PROMPT.prompt_version,
        "schema_version": STYLE_DIAGNOSIS_PROMPT.schema_version,
        "evaluator_version": EVALUATOR_VERSION,
    }
    report: dict[str, object] = {
        "versions": versions,
        "automated_summary": aggregate_automated(results),
        "samples": [asdict(result) for result in results],
    }
    if reviews is not None:
        report["human_review_summary"] = aggregate_reviews(reviews)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run style diagnosis evaluation.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument(
        "--split",
        choices=["tune", "validation", "regression"],
    )
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--max-quality-drop", type=float, default=0.03)
    parser.add_argument(
        "--max-latency-increase-percent",
        type=float,
        default=20.0,
    )
    parser.add_argument(
        "--max-cost-increase-percent",
        type=float,
        default=20.0,
    )
    parser.add_argument("--expected-model")
    parser.add_argument("--enforce-release-gates", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.concurrency < 1 or args.concurrency > 8:
        raise SystemExit("--concurrency must be between 1 and 8")
    thresholds = RegressionThresholds(
        max_quality_drop=args.max_quality_drop,
        max_latency_increase_percent=args.max_latency_increase_percent,
        max_cost_increase_percent=args.max_cost_increase_percent,
    )
    baseline: dict[str, object] | None = None
    baseline_sha256: str | None = None
    baseline_equals_output = (
        args.baseline is not None and args.baseline.resolve() == args.output.resolve()
    )
    try:
        thresholds.validate()
        validate_expected_model(args.expected_model)
        if baseline_equals_output:
            raise RegressionReportError("baseline and output paths must differ")
        if args.baseline is not None:
            baseline, baseline_sha256 = load_report(args.baseline)
    except RegressionReportError as error:
        if not baseline_equals_output:
            write_report(
                args.output,
                {
                    "schema_version": 1,
                    "command_gate": {
                        "status": "FAILED",
                        "failures": ["REGRESSION_CONFIGURATION_INVALID"],
                        "error": str(error),
                    },
                },
            )
        return 2

    samples = read_jsonl(args.manifest, EvaluationSample)
    if args.split:
        samples = [sample for sample in samples if sample.split == args.split]
    if args.max_samples is not None:
        samples = samples[: max(0, args.max_samples)]
    if not samples:
        raise SystemExit("manifest contains no selected samples")
    if args.enforce_release_gates:
        try:
            validate_diagnosis_release_dataset(samples)
        except DatasetPolicyError as error:
            write_report(
                args.output,
                {
                    "schema_version": 1,
                    "command_gate": {
                        "status": "FAILED",
                        "failures": ["RELEASE_DATASET_POLICY_FAILED"],
                        "policy_violations": list(error.violations),
                    },
                },
            )
            return 2
    reviews = read_jsonl(args.reviews, HumanReview) if args.reviews is not None else None
    results = asyncio.run(
        run_evaluation(
            samples,
            settings=Settings(),
            concurrency=args.concurrency,
        )
    )
    report = build_report(results, reviews)
    failures: list[str] = []
    try:
        if args.expected_model is not None:
            model_assertion = sample_field_assertion(
                report,
                field="model",
                expected=args.expected_model,
            )
            report["model_assertions"] = [model_assertion]
            if model_assertion["status"] != "PASSED":
                failures.append("EXPECTED_MODEL_MISMATCH:diagnosis")
        if baseline is not None and baseline_sha256 is not None:
            comparison = compare_reports(
                report,
                baseline,
                metrics=DIAGNOSIS_METRICS,
                thresholds=thresholds,
                baseline_sha256=baseline_sha256,
            )
            report["regression_comparison"] = comparison
            if comparison["status"] != "PASSED":
                failures.append("REGRESSION_COMPARISON_FAILED")
        if args.enforce_release_gates:
            failures.extend(release_gate_failures(report))
    except RegressionReportError as error:
        report["command_gate"] = {
            "status": "FAILED",
            "failures": ["REGRESSION_CONFIGURATION_INVALID"],
            "error": str(error),
        }
        write_report(args.output, report)
        return 2

    report["command_gate"] = {
        "status": "PASSED" if not failures else "FAILED",
        "failures": failures,
    }
    write_report(args.output, report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
