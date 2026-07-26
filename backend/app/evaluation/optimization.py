from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import Settings
from app.evaluation.diagnosis import private_object_key, read_jsonl
from app.modules.ai.contracts import StructuredVisionRequest
from app.modules.ai.gateway import AIGateway, AllProvidersFailedError
from app.modules.ai.openai_provider import OpenAIStructuredVisionProvider
from app.modules.ai.policies import optimization_critic_policy
from app.modules.assets.storage import ObjectStorage, build_object_storage
from app.modules.optimization.images import comparison_data_url
from app.modules.optimization.prompt import OPTIMIZATION_CRITIC_PROMPT
from app.modules.optimization.schema import (
    ChangeBudgetPlan,
    OptimizationCriticOutput,
)

EVALUATOR_VERSION = "style-optimization-evaluator-v1.0.0"
FailureDimension = Literal[
    "IDENTITY",
    "POSE_AND_COMPOSITION",
    "BACKGROUND_AND_LIGHTING",
    "UNMENTIONED_GARMENTS",
    "REQUESTED_CHANGES_ONLY",
    "VISUAL_ARTIFACT",
]


class ExpectedOptimizationGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["PASS", "REJECT"]
    failure_dimensions: list[FailureDimension] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_failure_dimensions(self) -> ExpectedOptimizationGate:
        if self.decision == "PASS" and self.failure_dimensions:
            raise ValueError("passing samples cannot declare failure dimensions")
        if self.decision == "REJECT" and not self.failure_dimensions:
            raise ValueError("rejected samples must declare failure dimensions")
        if len(self.failure_dimensions) != len(set(self.failure_dimensions)):
            raise ValueError("failure dimensions must be unique")
        return self


class OptimizationEvaluationSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^of_[a-z0-9_]+$")
    dataset_version: str = Field(pattern=r"^v[0-9]+\.[0-9]+$")
    split: Literal["tune", "validation", "regression"]
    source_reference: str = Field(min_length=1)
    candidate_reference: str = Field(min_length=1)
    consent_reference: str = Field(min_length=1)
    generation_attempt: int = Field(ge=1, le=3)
    exposed_to_user: bool = False
    production_latency_ms: int | None = Field(default=None, ge=1)
    production_cost_microunits: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)
    plan: ChangeBudgetPlan
    expected: ExpectedOptimizationGate


class OptimizationReviewScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_fidelity: int = Field(ge=1, le=5)
    body_fidelity: int = Field(ge=1, le=5)
    pose_composition_fidelity: int = Field(ge=1, le=5)
    background_lighting_fidelity: int = Field(ge=1, le=5)
    unmentioned_garment_fidelity: int = Field(ge=1, le=5)
    requested_change_quality: int = Field(ge=1, le=5)
    artifact_quality: int = Field(ge=1, le=5)


class OptimizationHumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^of_[a-z0-9_]+$")
    dataset_version: str
    evaluator_version: str
    reviewer_id: str = Field(min_length=3, max_length=80)
    scores: OptimizationReviewScores
    hard_failure_codes: list[FailureDimension] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=1_000)


@dataclass(frozen=True, slots=True)
class OptimizationSampleResult:
    sample_id: str
    dataset_version: str
    split: str
    provider: str | None
    model: str | None
    prompt_version: str
    schema_version: str
    evaluator_version: str
    generation_attempt: int
    exposed_to_user: bool
    production_latency_ms: int | None
    evaluation_critic_latency_ms: int
    production_cost_microunits: int | None
    evaluation_critic_cost_microunits: int | None
    provider_success: bool
    schema_valid: bool
    critic_passed: bool
    expected_gate_match: bool
    expected_dimension_recall: float | None
    automated_pass: bool
    failure_code: str | None


def detected_failure_dimensions(output: OptimizationCriticOutput) -> set[str]:
    failures: set[str] = set()
    checks = {
        "IDENTITY": output.identity_preserved,
        "POSE_AND_COMPOSITION": output.pose_and_composition_preserved,
        "BACKGROUND_AND_LIGHTING": output.background_and_lighting_preserved,
        "UNMENTIONED_GARMENTS": output.unmentioned_garments_preserved,
        "REQUESTED_CHANGES_ONLY": output.requested_changes_only,
    }
    failures.update(name for name, check in checks.items() if not check.passed)
    if output.artifacts:
        failures.add("VISUAL_ARTIFACT")
    return failures


def contract_checks(
    sample: OptimizationEvaluationSample,
    output: OptimizationCriticOutput,
) -> tuple[bool, float | None, bool]:
    expected_pass = sample.expected.decision == "PASS"
    gate_match = output.overall_pass == expected_pass
    expected_dimensions = set(sample.expected.failure_dimensions)
    dimension_recall = (
        len(expected_dimensions & detected_failure_dimensions(output)) / len(expected_dimensions)
        if expected_dimensions
        else None
    )
    passed = gate_match and (dimension_recall is None or dimension_recall == 1)
    return gate_match, dimension_recall, passed


def percentile(values: list[int], target: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((target / 100) * len(ordered)) - 1)
    return ordered[index]


def aggregate_automated(
    results: list[OptimizationSampleResult],
) -> dict[str, object]:
    total = len(results)
    denominator = max(total, 1)
    first_attempts = [result for result in results if result.generation_attempt == 1]
    production_latencies = [
        result.production_latency_ms
        for result in results
        if result.production_latency_ms is not None
    ]
    production_costs = [
        result.production_cost_microunits
        for result in results
        if result.production_cost_microunits is not None
    ]
    critic_latencies = [result.evaluation_critic_latency_ms for result in results]
    critic_costs = [
        result.evaluation_critic_cost_microunits
        for result in results
        if result.evaluation_critic_cost_microunits is not None
    ]
    recalls = [
        result.expected_dimension_recall
        for result in results
        if result.expected_dimension_recall is not None
    ]
    first_pass_rate = (
        sum(result.critic_passed for result in first_attempts) / len(first_attempts)
        if first_attempts
        else None
    )
    exposed_rejected = [
        result.sample_id
        for result in results
        if result.exposed_to_user and not result.critic_passed
    ]
    failures = Counter(result.failure_code for result in results if result.failure_code)
    report: dict[str, object] = {
        "sample_count": total,
        "provider_success_rate": round(
            sum(result.provider_success for result in results) / denominator,
            4,
        ),
        "schema_pass_rate": round(
            sum(result.schema_valid for result in results) / denominator,
            4,
        ),
        "automated_pass_rate": round(
            sum(result.automated_pass for result in results) / denominator,
            4,
        ),
        "expected_gate_accuracy": round(
            sum(result.expected_gate_match for result in results) / denominator,
            4,
        ),
        "expected_failure_dimension_recall": (
            round(sum(recalls) / len(recalls), 4) if recalls else None
        ),
        "critic_first_pass_rate": (
            round(first_pass_rate, 4) if first_pass_rate is not None else None
        ),
        "production_latency_ms": {
            "p50": percentile(production_latencies, 50),
            "p90": percentile(production_latencies, 90),
            "p95": percentile(production_latencies, 95),
            "coverage_rate": round(len(production_latencies) / denominator, 4),
        },
        "evaluation_critic_latency_ms": {
            "p50": percentile(critic_latencies, 50),
            "p90": percentile(critic_latencies, 90),
            "p95": percentile(critic_latencies, 95),
        },
        "production_cost_microunits": {
            "average": (
                round(sum(production_costs) / len(production_costs)) if production_costs else None
            ),
            "p95": percentile(production_costs, 95),
            "coverage_rate": round(len(production_costs) / denominator, 4),
        },
        "evaluation_critic_cost_microunits": {
            "average": round(sum(critic_costs) / len(critic_costs)) if critic_costs else None,
            "coverage_rate": round(len(critic_costs) / denominator, 4),
        },
        "exposed_rejected_sample_ids": exposed_rejected,
        "failures": dict(sorted(failures.items())),
    }
    report["release_gates"] = {
        "sample_count_at_least_50": total >= 50,
        "schema_pass_100_percent": all(result.schema_valid for result in results) and total > 0,
        "critic_first_pass_at_least_75_percent": (
            first_pass_rate is not None and first_pass_rate >= 0.75
        ),
        "production_p90_collected_and_under_60_seconds": (
            len(production_latencies) == total
            and percentile(production_latencies, 90) is not None
            and percentile(production_latencies, 90) < 60_000  # type: ignore[operator]
        ),
        "production_cost_100_percent_coverage": len(production_costs) == total and total > 0,
        "zero_rejected_results_exposed": not exposed_rejected,
    }
    return report


def review_passes(review: OptimizationHumanReview) -> bool:
    values = list(review.scores.model_dump().values())
    preservation_scores = [
        review.scores.identity_fidelity,
        review.scores.body_fidelity,
        review.scores.pose_composition_fidelity,
        review.scores.background_lighting_fidelity,
        review.scores.unmentioned_garment_fidelity,
    ]
    return (
        not review.hard_failure_codes
        and sum(values) / len(values) >= 4
        and min(preservation_scores) >= 4
        and review.scores.requested_change_quality >= 4
        and review.scores.artifact_quality >= 4
    )


def aggregate_reviews(
    reviews: list[OptimizationHumanReview],
) -> dict[str, object]:
    dimensions: dict[str, list[int]] = defaultdict(list)
    reviews_by_sample: dict[str, list[OptimizationHumanReview]] = defaultdict(list)
    for review in reviews:
        reviews_by_sample[review.sample_id].append(review)
        for dimension, score in review.scores.model_dump().items():
            dimensions[dimension].append(score)

    disagreements: list[str] = []
    for sample_id, sample_reviews in reviews_by_sample.items():
        if any(
            max(getattr(review.scores, dimension) for review in sample_reviews)
            - min(getattr(review.scores, dimension) for review in sample_reviews)
            >= 2
            for dimension in OptimizationReviewScores.model_fields
        ):
            disagreements.append(sample_id)

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
        "dimensions": {
            dimension: {
                "count": len(scores),
                "average": round(sum(scores) / len(scores), 3) if scores else None,
            }
            for dimension, scores in dimensions.items()
        },
        "coverage_gate_20_per_dimension": bool(dimensions)
        and len(dimensions) == len(OptimizationReviewScores.model_fields)
        and all(len(scores) >= 20 for scores in dimensions.values()),
        "samples_with_fewer_than_two_reviewers": under_reviewed,
        "samples_requiring_arbitration": sorted(disagreements),
    }


class OptimizationEvaluationRunner:
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
        self._gateway = AIGateway(structured_vision_providers=(self._provider,))

    async def close(self) -> None:
        await self._provider.close()

    async def evaluate(
        self,
        sample: OptimizationEvaluationSample,
    ) -> OptimizationSampleResult:
        started_at = perf_counter()
        provider: str | None = None
        model: str | None = None
        estimated_cost: int | None = None
        provider_succeeded = False
        try:
            source, candidate = await asyncio.gather(
                self._storage.read_object(
                    object_key=private_object_key(sample.source_reference),
                    max_bytes=self._settings.max_upload_bytes,
                ),
                self._storage.read_object(
                    object_key=private_object_key(sample.candidate_reference),
                    max_bytes=self._settings.max_upload_bytes,
                ),
            )
            response = await self._gateway.structured_vision(
                request=StructuredVisionRequest(
                    image_url=comparison_data_url(source, candidate),
                    prompt=OPTIMIZATION_CRITIC_PROMPT.instructions(),
                    output_schema=OPTIMIZATION_CRITIC_PROMPT.output_schema(),
                    metadata={"user_context": OPTIMIZATION_CRITIC_PROMPT.user_context(sample.plan)},
                ),
                policy=optimization_critic_policy(self._settings),
            )
            provider = response.provider
            model = response.model
            estimated_cost = response.usage.estimated_cost_microunits
            provider_succeeded = True
            output = OptimizationCriticOutput.model_validate(response.output)
            gate_match, dimension_recall, passed = contract_checks(sample, output)
            return OptimizationSampleResult(
                sample_id=sample.sample_id,
                dataset_version=sample.dataset_version,
                split=sample.split,
                provider=provider,
                model=model,
                prompt_version=OPTIMIZATION_CRITIC_PROMPT.prompt_version,
                schema_version=OPTIMIZATION_CRITIC_PROMPT.schema_version,
                evaluator_version=EVALUATOR_VERSION,
                generation_attempt=sample.generation_attempt,
                exposed_to_user=sample.exposed_to_user,
                production_latency_ms=sample.production_latency_ms,
                evaluation_critic_latency_ms=round((perf_counter() - started_at) * 1_000),
                production_cost_microunits=sample.production_cost_microunits,
                evaluation_critic_cost_microunits=estimated_cost,
                provider_success=True,
                schema_valid=True,
                critic_passed=output.overall_pass,
                expected_gate_match=gate_match,
                expected_dimension_recall=dimension_recall,
                automated_pass=passed,
                failure_code=None if passed else "EXPECTED_GATE_MISMATCH",
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
        return OptimizationSampleResult(
            sample_id=sample.sample_id,
            dataset_version=sample.dataset_version,
            split=sample.split,
            provider=provider,
            model=model,
            prompt_version=OPTIMIZATION_CRITIC_PROMPT.prompt_version,
            schema_version=OPTIMIZATION_CRITIC_PROMPT.schema_version,
            evaluator_version=EVALUATOR_VERSION,
            generation_attempt=sample.generation_attempt,
            exposed_to_user=sample.exposed_to_user,
            production_latency_ms=sample.production_latency_ms,
            evaluation_critic_latency_ms=round((perf_counter() - started_at) * 1_000),
            production_cost_microunits=sample.production_cost_microunits,
            evaluation_critic_cost_microunits=estimated_cost,
            provider_success=provider_succeeded,
            schema_valid=False,
            critic_passed=False,
            expected_gate_match=False,
            expected_dimension_recall=None,
            automated_pass=False,
            failure_code=failure_code,
        )


async def run_evaluation(
    samples: list[OptimizationEvaluationSample],
    *,
    settings: Settings,
    concurrency: int,
) -> list[OptimizationSampleResult]:
    runner = OptimizationEvaluationRunner(
        settings=settings,
        storage=build_object_storage(settings),
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(
        sample: OptimizationEvaluationSample,
    ) -> OptimizationSampleResult:
        async with semaphore:
            return await runner.evaluate(sample)

    try:
        return list(await asyncio.gather(*(evaluate_one(sample) for sample in samples)))
    finally:
        await runner.close()


def build_report(
    results: list[OptimizationSampleResult],
    reviews: list[OptimizationHumanReview] | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "versions": {
            "dataset_versions": sorted({result.dataset_version for result in results}),
            "model_versions": sorted(
                {result.model for result in results if result.model is not None}
            ),
            "prompt_version": OPTIMIZATION_CRITIC_PROMPT.prompt_version,
            "schema_version": OPTIMIZATION_CRITIC_PROMPT.schema_version,
            "evaluator_version": EVALUATOR_VERSION,
        },
        "automated_summary": aggregate_automated(results),
        "samples": [asdict(result) for result in results],
    }
    if reviews is not None:
        report["human_review_summary"] = aggregate_reviews(reviews)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run style optimization evaluation.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument(
        "--split",
        choices=["tune", "validation", "regression"],
    )
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.concurrency < 1 or args.concurrency > 8:
        raise SystemExit("--concurrency must be between 1 and 8")
    samples = read_jsonl(args.manifest, OptimizationEvaluationSample)
    if args.split:
        samples = [sample for sample in samples if sample.split == args.split]
    if args.max_samples is not None:
        samples = samples[: max(0, args.max_samples)]
    if not samples:
        raise SystemExit("manifest contains no selected samples")
    reviews = (
        read_jsonl(args.reviews, OptimizationHumanReview) if args.reviews is not None else None
    )
    results = asyncio.run(
        run_evaluation(
            samples,
            settings=Settings(),
            concurrency=args.concurrency,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_report(results, reviews), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
