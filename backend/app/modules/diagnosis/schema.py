from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputQuality(StrEnum):
    ACCEPTABLE = "ACCEPTABLE"
    TOO_DARK = "TOO_DARK"
    TOO_BLURRY = "TOO_BLURRY"
    PERSON_NOT_VISIBLE = "PERSON_NOT_VISIBLE"
    OUTFIT_OCCLUDED = "OUTFIT_OCCLUDED"
    MULTIPLE_PEOPLE = "MULTIPLE_PEOPLE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class StyleIssueCategory(StrEnum):
    PROPORTION = "PROPORTION"
    COLOR = "COLOR"
    FIT = "FIT"
    LAYERING = "LAYERING"
    OCCASION = "OCCASION"
    STYLING = "STYLING"
    ACCESSORY = "ACCESSORY"
    UNKNOWN = "UNKNOWN"


class OptimizationAction(StrEnum):
    ADJUST_WEARING = "ADJUST_WEARING"
    CHANGE_PROPORTION = "CHANGE_PROPORTION"
    CHANGE_COLOR_BALANCE = "CHANGE_COLOR_BALANCE"
    REPLACE_ONE_ITEM = "REPLACE_ONE_ITEM"
    ADD_OR_REMOVE_LAYER = "ADD_OR_REMOVE_LAYER"
    ADJUST_ACCESSORY = "ADJUST_ACCESSORY"
    UNKNOWN = "UNKNOWN"


class DiagnosisPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=40)
    explanation: str = Field(min_length=6, max_length=180)
    visual_evidence: str = Field(min_length=4, max_length=140)
    confidence: Confidence


class PrimaryIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: StyleIssueCategory
    title: str = Field(min_length=2, max_length=40)
    explanation: str = Field(min_length=8, max_length=220)
    expected_impact: str = Field(min_length=4, max_length=120)
    confidence: Confidence


class OptimizationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1, le=3)
    action: OptimizationAction
    instruction: str = Field(min_length=4, max_length=140)
    reason: str = Field(min_length=6, max_length=180)
    preserves: str = Field(min_length=2, max_length=100)


class DiagnosisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_quality: InputQuality
    input_quality_message: str | None = Field(max_length=160)
    score: int | None = Field(ge=0, le=100)
    summary: str | None = Field(max_length=220)
    strengths: list[DiagnosisPoint] = Field(max_length=3)
    issues: list[DiagnosisPoint] = Field(max_length=3)
    primary_issue: PrimaryIssue | None
    optimization_plan: list[OptimizationStep] = Field(max_length=3)
    disclaimer: str = Field(min_length=4, max_length=100)

    @model_validator(mode="after")
    def validate_quality_dependent_fields(self) -> "DiagnosisOutput":
        if self.input_quality != InputQuality.ACCEPTABLE:
            if not self.input_quality_message:
                raise ValueError("invalid input quality requires a user message")
            if (
                self.score is not None
                or self.summary is not None
                or self.strengths
                or self.issues
                or self.primary_issue is not None
                or self.optimization_plan
            ):
                raise ValueError("invalid input quality cannot contain a style diagnosis")
            return self

        if (
            self.score is None
            or self.summary is None
            or not self.strengths
            or not self.issues
            or self.primary_issue is None
            or not self.optimization_plan
        ):
            raise ValueError("acceptable input requires a complete style diagnosis")
        if self.input_quality_message is not None:
            raise ValueError("acceptable input cannot contain an input quality message")
        priorities = [step.priority for step in self.optimization_plan]
        if len(priorities) != len(set(priorities)):
            raise ValueError("optimization priorities must be unique")
        return self
