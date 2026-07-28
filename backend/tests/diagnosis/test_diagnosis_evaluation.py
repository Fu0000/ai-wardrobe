from app.evaluation.diagnosis import (
    AutomatedSampleResult,
    EvaluationSample,
    HumanReview,
    aggregate_automated,
    aggregate_reviews,
    contract_checks,
    private_object_key,
)
from app.modules.diagnosis.schema import DiagnosisOutput


def sample() -> EvaluationSample:
    return EvaluationSample.model_validate(
        {
            "sample_id": "sd_commute_001",
            "dataset_version": "v0.1",
            "split": "validation",
            "asset_reference": "cos-private://evals/diagnosis/commute-001.jpg",
            "occasion": "WORK",
            "consent_reference": "consent-001",
            "tags": ["daylight"],
            "expected": {
                "input_acceptable": True,
                "primary_issue_categories": ["PROPORTION"],
                "must_preserve": ["identity"],
            },
        }
    )


def output() -> DiagnosisOutput:
    return DiagnosisOutput.model_validate(
        {
            "input_quality": "ACCEPTABLE",
            "input_quality_message": None,
            "score": 82,
            "summary": "层次清楚，先调整上衣下摆会更利落。",
            "strengths": [
                {
                    "title": "色彩克制",
                    "explanation": "主色与鞋子呼应，整体没有多余冲突。",
                    "visual_evidence": "上衣与鞋子都使用低饱和中性色。",
                    "confidence": "HIGH",
                }
            ],
            "issues": [
                {
                    "title": "比例略长",
                    "explanation": "上衣下摆覆盖较多，会压低当前视觉重心。",
                    "visual_evidence": "下摆明显低于腰线并遮住裤腰。",
                    "confidence": "HIGH",
                }
            ],
            "primary_issue": {
                "category": "PROPORTION",
                "title": "抬高视觉腰线",
                "explanation": "当前下摆位置让上下比例偏平均，缺少清晰重心。",
                "expected_impact": "让通勤造型看起来更利落有精神。",
                "confidence": "HIGH",
            },
            "optimization_plan": [
                {
                    "priority": 1,
                    "action": "ADJUST_WEARING",
                    "instruction": "把上衣前摆轻收进裤腰。",
                    "reason": "露出腰线即可改善比例，不必替换现有单品。",
                    "preserves": "现有上衣、裤子和整体配色",
                }
            ],
            "disclaimer": "AI 建议仅供参考，请以你的舒适感受为准。",
        }
    )


def result(index: int, *, passed: bool = True) -> AutomatedSampleResult:
    return AutomatedSampleResult(
        sample_id=f"sd_sample_{index:03d}",
        dataset_version="v0.1",
        split="validation",
        provider="openai",
        model="gpt-5.6-terra",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        evaluator_version="eval-v1",
        latency_ms=1_000 + index,
        estimated_cost_microunits=5_000,
        provider_success=passed,
        schema_valid=passed,
        input_quality_match=passed,
        primary_category_hit=passed,
        automated_pass=passed,
        failure_code=None if passed else "SCHEMA_INVALID",
    )


def test_private_asset_reference_never_accepts_public_or_traversal_urls() -> None:
    assert (
        private_object_key("cos-private://evals/diagnosis/photo.jpg") == "evals/diagnosis/photo.jpg"
    )
    for unsafe in [
        "https://public.example/photo.jpg",
        "cos-private://evals/../private/photo.jpg",
        "cos-private://evals/photo.jpg?token=secret",
    ]:
        try:
            private_object_key(unsafe)
        except ValueError:
            continue
        raise AssertionError(f"unsafe reference was accepted: {unsafe}")


def test_contract_checks_expected_quality_and_primary_issue() -> None:
    assert contract_checks(sample(), output()) == (True, True, True)


def test_automated_summary_applies_release_gates() -> None:
    report = aggregate_automated([result(index) for index in range(50)])

    assert report["sample_count"] == 50
    assert report["schema_pass_rate"] == 1
    assert report["release_gates"] == {
        "sample_count_at_least_50": True,
        "provider_success_at_least_95_percent": True,
        "schema_pass_100_percent": True,
        "p95_under_30_seconds": True,
    }


def test_human_review_summary_flags_under_reviewed_samples() -> None:
    review = HumanReview.model_validate(
        {
            "sample_id": "sd_commute_001",
            "dataset_version": "v0.1",
            "evaluator_version": "human-v1",
            "model_version": "gpt-5.6-terra",
            "prompt_version": "style-diagnosis-2026-07-26.1",
            "schema_version": "style-diagnosis-v1.0.0",
            "reviewer_id": "reviewer-a",
            "scores": {
                "primary_issue_hit": 5,
                "strength_specificity": 4,
                "actionability": 5,
                "occasion_fit": 4,
                "minimal_change": 5,
                "respectful_expression": 5,
            },
            "hard_failure_codes": [],
        }
    )

    report = aggregate_reviews([review])

    assert report["pass_rate"] == 1
    assert report["coverage_gate_20_per_dimension"] is False
    assert report["samples_with_fewer_than_two_reviewers"] == ["sd_commute_001"]
