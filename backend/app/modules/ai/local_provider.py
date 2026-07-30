from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

from app.modules.ai.contracts import (
    AIProviderError,
    ImageEditRequest,
    ImageEditResponse,
    ProviderErrorCode,
    ProviderUsage,
    StructuredVisionRequest,
    StructuredVisionResponse,
)

LOCAL_STRUCTURED_PROVIDER_NAME = "local-demo"
LOCAL_IMAGE_PROVIDER_NAME = "local-demo-image"
LOCAL_DIAGNOSIS_MODEL = "local-demo-diagnosis-v1"
LOCAL_CRITIC_MODEL = "local-demo-critic-v1"
LOCAL_IMAGE_MODEL = "local-demo-image-v1"


def _diagnosis_output() -> dict[str, object]:
    return {
        "input_quality": "ACCEPTABLE",
        "input_quality_message": None,
        "score": 82,
        "summary": "整体配色协调、层次清楚；稍微调整上衣下摆，就能让比例更利落。",
        "strengths": [
            {
                "title": "配色协调",
                "explanation": "主体颜色彼此呼应，视觉上统一且适合日常通勤场景。",
                "visual_evidence": "上装与下装保持了克制、连贯的色彩关系。",
                "confidence": "MEDIUM",
            },
            {
                "title": "层次清楚",
                "explanation": "服装轮廓之间有明确层次，没有出现过度堆叠的感觉。",
                "visual_evidence": "上下装边界清晰，整体造型简洁。",
                "confidence": "MEDIUM",
            },
        ],
        "issues": [
            {
                "title": "比例可优化",
                "explanation": "上衣下摆目前略显平直，轻微调整穿法会让上下比例更清晰。",
                "visual_evidence": "腰线位置可以通过整理下摆得到更明确的表达。",
                "confidence": "MEDIUM",
            }
        ],
        "primary_issue": {
            "category": "PROPORTION",
            "title": "腰线不够明确",
            "explanation": "当前上衣下摆弱化了腰线，整理穿法即可改善整体纵向比例。",
            "expected_impact": "造型会更利落，也更容易突出已有单品的层次。",
            "confidence": "MEDIUM",
        },
        "optimization_plan": [
            {
                "priority": 1,
                "action": "ADJUST_WEARING",
                "instruction": "将上衣前侧下摆轻收或半塞，保留自然松量。",
                "reason": "这是无需更换单品就能明确腰线的最小调整。",
                "preserves": "人物、服装、背景与整体配色",
            }
        ],
        "disclaimer": "本地演示结果仅用于流程测试，不代表真实 AI 穿搭判断。",
    }


def _critic_output() -> dict[str, object]:
    check = {
        "passed": True,
        "confidence": "HIGH",
        "evidence": "本地演示模式保持原始构图，仅做轻量图像增强。",
    }
    return {
        "identity_preserved": check,
        "pose_and_composition_preserved": check,
        "background_and_lighting_preserved": check,
        "unmentioned_garments_preserved": check,
        "requested_changes_only": check,
        "artifacts": [],
        "overall_pass": True,
        "summary": "本地演示候选图通过确定性流程检查，仅用于验证产品链路。",
    }


class LocalStructuredVisionProvider:
    name = LOCAL_STRUCTURED_PROVIDER_NAME

    async def analyze(
        self,
        *,
        model: str,
        request: StructuredVisionRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> StructuredVisionResponse:
        properties = request.output_schema.get("properties")
        if not isinstance(properties, dict):
            raise self._unsupported_schema()
        if "input_quality" in properties:
            output = _diagnosis_output()
        elif "overall_pass" in properties:
            output = _critic_output()
        else:
            raise self._unsupported_schema()
        return StructuredVisionResponse(
            output=output,
            provider=self.name,
            model=model,
            usage=ProviderUsage(
                input_tokens=0,
                output_tokens=0,
                estimated_cost_microunits=0,
            ),
            provider_request_id=None,
        )

    async def close(self) -> None:
        return None

    @staticmethod
    def _unsupported_schema() -> AIProviderError:
        return AIProviderError(
            code=ProviderErrorCode.INVALID_INPUT,
            message="local demo provider received an unsupported schema",
            retryable=False,
        )


class LocalImageEditProvider:
    name = LOCAL_IMAGE_PROVIDER_NAME

    def __init__(self, *, max_output_bytes: int) -> None:
        self._max_output_bytes = max_output_bytes

    async def edit(
        self,
        *,
        model: str,
        request: ImageEditRequest,
        timeout_seconds: float,
        cost_ceiling_microunits: int,
    ) -> ImageEditResponse:
        if request.source_content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise self._invalid_image()
        try:
            with Image.open(BytesIO(request.source_image)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        except (UnidentifiedImageError, OSError) as error:
            raise self._invalid_image() from error
        if image.size != (request.source_width, request.source_height):
            raise self._invalid_image()

        width, height = image.size
        if width < 512 or height < 512:
            scale = max(512 / width, 512 / height)
            image = image.resize(
                (round(width * scale), round(height * scale)),
                Image.Resampling.LANCZOS,
            )
        image = ImageEnhance.Color(image).enhance(1.04)
        image = ImageEnhance.Contrast(image).enhance(1.03)
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        image_bytes = output.getvalue()
        if not image_bytes or len(image_bytes) > self._max_output_bytes:
            raise AIProviderError(
                code=ProviderErrorCode.INVALID_RESPONSE,
                message="local demo image exceeds the safe output limit",
                retryable=False,
            )
        return ImageEditResponse(
            image_bytes=image_bytes,
            content_type="image/jpeg",
            provider=self.name,
            model=model,
            usage=ProviderUsage(
                input_tokens=0,
                output_tokens=0,
                estimated_cost_microunits=0,
            ),
            provider_request_id=None,
        )

    async def close(self) -> None:
        return None

    @staticmethod
    def _invalid_image() -> AIProviderError:
        return AIProviderError(
            code=ProviderErrorCode.INVALID_INPUT,
            message="local demo provider received an invalid image",
            retryable=False,
        )
