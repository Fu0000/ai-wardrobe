from dataclasses import replace
from typing import cast

from app.evaluation.optimization import (
    OptimizationEvaluationSample,
    OptimizationHumanReview,
    OptimizationSampleResult,
    aggregate_automated,
    aggregate_reviews,
    contract_checks,
)
from app.modules.optimization.schema import OptimizationCriticOutput


def sample(*, decision: str = "REJECT") -> OptimizationEvaluationSample:
    return OptimizationEvaluationSample.model_validate(
        {
            "sample_id": "of_background_001",
            "dataset_version": "v0.1",
            "split": "validation",
            "source_reference": "cos-private://evals/optimization/before-001.jpg",
            "candidate_reference": "cos-private://evals/optimization/after-001.jpg",
            "consent_reference": "consent-001",
            "generation_attempt": 1,
            "exposed_to_user": False,
            "production_latency_ms": 20_000,
            "production_cost_microunits": 100_000,
            "tags": ["deliberate_background_warp"],
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
                "failure_dimensions": (["BACKGROUND_AND_LIGHTING"] if decision == "REJECT" else []),
            },
        }
    )


def critic_output(*, background_passed: bool) -> OptimizationCriticOutput:
    return OptimizationCriticOutput.model_validate(
        {
            "identity_preserved": {
                "passed": True,
                "confidence": "HIGH",
                "evidence": "同一人物且面部和身体轮廓一致。",
            },
            "pose_and_composition_preserved": {
                "passed": True,
                "confidence": "HIGH",
                "evidence": "姿态、镜头和画幅保持一致。",
            },
            "background_and_lighting_preserved": {
                "passed": background_passed,
                "confidence": "HIGH",
                "evidence": "背景右侧墙线出现明显弯曲变化。",
            },
            "unmentioned_garments_preserved": {
                "passed": True,
                "confidence": "HIGH",
                "evidence": "未提及衣物颜色与图案保持。",
            },
            "requested_changes_only": {
                "passed": True,
                "confidence": "HIGH",
                "evidence": "仅执行了上衣前摆调整。",
            },
            "artifacts": [],
            "overall_pass": background_passed,
            "summary": "背景未保持时必须拒绝该候选图。",
        }
    )


def result(index: int, *, passed: bool = True) -> OptimizationSampleResult:
    return OptimizationSampleResult(
        sample_id=f"of_sample_{index:03d}",
        dataset_version="v0.1",
        split="validation",
        provider="openai",
        model="gpt-5.6-terra",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        evaluator_version="eval-v1",
        generation_attempt=1,
        exposed_to_user=False,
        production_latency_ms=20_000 + index,
        evaluation_critic_latency_ms=1_000 + index,
        production_cost_microunits=100_000,
        evaluation_critic_cost_microunits=5_000,
        provider_success=True,
        schema_valid=True,
        critic_passed=passed,
        expected_gate_match=passed,
        expected_dimension_recall=1.0,
        automated_pass=passed,
        failure_code=None if passed else "EXPECTED_GATE_MISMATCH",
    )


def test_contract_checks_detect_expected_preservation_failure() -> None:
    assert contract_checks(sample(), critic_output(background_passed=False)) == (
        True,
        1.0,
        True,
    )


def test_manifest_rejects_exposed_failure_without_failure_dimension() -> None:
    payload = sample().model_dump(mode="json")
    payload["expected"] = {"decision": "REJECT", "failure_dimensions": []}
    try:
        OptimizationEvaluationSample.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("rejected sample without a failure dimension was accepted")


def test_automated_summary_applies_optimization_release_gates() -> None:
    report = aggregate_automated([result(index) for index in range(50)])

    assert report["critic_first_pass_rate"] == 1
    assert report["release_gates"] == {
        "sample_count_at_least_50": True,
        "schema_pass_100_percent": True,
        "critic_first_pass_at_least_75_percent": True,
        "production_p90_collected_and_under_60_seconds": True,
        "production_cost_100_percent_coverage": True,
        "zero_rejected_results_exposed": True,
    }


def test_automated_summary_flags_rejected_result_exposure() -> None:
    unsafe = replace(result(1, passed=False), exposed_to_user=True)

    report = aggregate_automated([unsafe])
    release_gates = cast(dict[str, bool], report["release_gates"])

    assert report["exposed_rejected_sample_ids"] == ["of_sample_001"]
    assert release_gates["zero_rejected_results_exposed"] is False


def test_human_review_requires_two_independent_reviewers() -> None:
    review = OptimizationHumanReview.model_validate(
        {
            "sample_id": "of_background_001",
            "dataset_version": "v0.1",
            "evaluator_version": "human-v1",
            "reviewer_id": "reviewer-a",
            "scores": {
                "identity_fidelity": 5,
                "body_fidelity": 5,
                "pose_composition_fidelity": 4,
                "background_lighting_fidelity": 4,
                "unmentioned_garment_fidelity": 5,
                "requested_change_quality": 4,
                "artifact_quality": 5,
            },
            "hard_failure_codes": [],
        }
    )

    report = aggregate_reviews([review])

    assert report["pass_rate"] == 1
    assert report["coverage_gate_20_per_dimension"] is False
    assert report["samples_with_fewer_than_two_reviewers"] == ["of_background_001"]
