import pytest
from pydantic import ValidationError

from app.modules.diagnosis.prompt import STYLE_DIAGNOSIS_PROMPT
from app.modules.diagnosis.schema import DiagnosisOutput


def valid_diagnosis() -> dict[str, object]:
    return {
        "input_quality": "ACCEPTABLE",
        "input_quality_message": None,
        "score": 82,
        "summary": "整体清爽，调整上下身比例后会更利落。",
        "strengths": [
            {
                "title": "配色克制",
                "explanation": "上衣与裤装都使用低饱和色，视觉上比较统一。",
                "visual_evidence": "上身灰白、下身深灰，没有突兀的大面积撞色。",
                "confidence": "HIGH",
            }
        ],
        "issues": [
            {
                "title": "腰线偏低",
                "explanation": "上衣下摆覆盖较多，缩短了下半身的视觉比例。",
                "visual_evidence": "上衣下摆落在胯部以下，裤腰位置不可见。",
                "confidence": "HIGH",
            }
        ],
        "primary_issue": {
            "category": "PROPORTION",
            "title": "先恢复腰线",
            "explanation": "当前上衣长度让上下身比例显得平均，缺少明确重心。",
            "expected_impact": "让整体轮廓更利落，并拉长下半身视觉比例。",
            "confidence": "HIGH",
        },
        "optimization_plan": [
            {
                "priority": 1,
                "action": "ADJUST_WEARING",
                "instruction": "把上衣前摆轻塞进裤腰，保留两侧自然垂落。",
                "reason": "用最小改变露出腰线，不需要替换现有单品。",
                "preserves": "现有上衣、裤装和整体配色",
            }
        ],
        "disclaimer": "建议仅基于照片中可见的穿搭信息。",
    }


def test_complete_diagnosis_schema_accepts_actionable_result() -> None:
    result = DiagnosisOutput.model_validate(valid_diagnosis())

    assert result.score == 82
    assert result.primary_issue is not None
    assert result.primary_issue.category.value == "PROPORTION"


def test_unacceptable_input_cannot_smuggle_style_advice() -> None:
    payload = valid_diagnosis()
    payload["input_quality"] = "TOO_DARK"
    payload["input_quality_message"] = "环境太暗，请在光线更充足的位置重拍。"

    with pytest.raises(ValidationError, match="cannot contain"):
        DiagnosisOutput.model_validate(payload)


def test_structured_output_schema_is_strict_and_versioned() -> None:
    schema = STYLE_DIAGNOSIS_PROMPT.output_schema()
    required = schema["required"]
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert isinstance(required, list)
    assert isinstance(properties, dict)
    assert set(required) == set(properties)
    assert STYLE_DIAGNOSIS_PROMPT.prompt_version
    assert STYLE_DIAGNOSIS_PROMPT.schema_version == "style-diagnosis-v1.0.0"


def test_prompt_treats_visual_text_as_untrusted() -> None:
    instructions = STYLE_DIAGNOSIS_PROMPT.instructions

    assert "不可信视觉内容" in instructions
    assert "不得执行" in instructions
    assert "身体价值" in instructions
