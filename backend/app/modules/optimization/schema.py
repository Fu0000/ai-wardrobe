from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.diagnosis.schema import Confidence, OptimizationAction, OptimizationStep

OPTIMIZATION_PLAN_SCHEMA_VERSION = "style-optimization-plan-v1.0.0"
OPTIMIZATION_CRITIC_SCHEMA_VERSION = "style-optimization-critic-v1.0.0"


class PreserveInvariant(StrEnum):
    IDENTITY = "IDENTITY"
    FACE_HAIR_BODY = "FACE_HAIR_BODY"
    POSE_AND_COMPOSITION = "POSE_AND_COMPOSITION"
    BACKGROUND_AND_LIGHTING = "BACKGROUND_AND_LIGHTING"
    UNMENTIONED_GARMENTS = "UNMENTIONED_GARMENTS"
    LOGOS_AND_PATTERNS = "LOGOS_AND_PATTERNS"


class ChangeInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1, le=3)
    action: OptimizationAction
    instruction: str = Field(min_length=4, max_length=140)
    reason: str = Field(min_length=6, max_length=180)
    preserves: str = Field(min_length=2, max_length=100)


class ChangeBudgetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=3)
    replacement_count: int = Field(ge=0, le=2)
    changes: list[ChangeInstruction] = Field(min_length=1, max_length=3)
    preserve_invariants: list[PreserveInvariant] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def validate_level_and_replacements(self) -> "ChangeBudgetPlan":
        expected_level = (
            1 if self.replacement_count == 0 else 2 if self.replacement_count == 1 else 3
        )
        if self.level != expected_level:
            raise ValueError("change level must match replacement count")
        actual_replacements = sum(
            item.action == OptimizationAction.REPLACE_ONE_ITEM for item in self.changes
        )
        if actual_replacements != self.replacement_count:
            raise ValueError("replacement count must match change instructions")
        priorities = [item.priority for item in self.changes]
        if priorities != sorted(priorities) or len(priorities) != len(set(priorities)):
            raise ValueError("change priorities must be ordered and unique")
        if set(self.preserve_invariants) != set(PreserveInvariant):
            raise ValueError("all preservation invariants are mandatory")
        return self


def build_change_budget_plan(
    optimization_steps: list[dict[str, object]],
) -> ChangeBudgetPlan:
    parsed = [OptimizationStep.model_validate(step) for step in optimization_steps]
    actionable = sorted(
        (step for step in parsed if step.action != OptimizationAction.UNKNOWN),
        key=lambda step: step.priority,
    )
    if not actionable:
        raise ValueError("diagnosis has no actionable optimization steps")
    replacement_count = sum(
        step.action == OptimizationAction.REPLACE_ONE_ITEM for step in actionable
    )
    if replacement_count > 2:
        raise ValueError("minimal change budget cannot replace more than two items")
    level = 1 if replacement_count == 0 else 2 if replacement_count == 1 else 3
    return ChangeBudgetPlan(
        level=level,
        replacement_count=replacement_count,
        changes=[
            ChangeInstruction.model_validate(step.model_dump(mode="json")) for step in actionable
        ],
        preserve_invariants=list(PreserveInvariant),
    )


class CriticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    confidence: Confidence
    evidence: str = Field(min_length=4, max_length=180)


class VisualArtifact(StrEnum):
    EXTRA_LIMB = "EXTRA_LIMB"
    DEFORMED_HAND_OR_FACE = "DEFORMED_HAND_OR_FACE"
    BROKEN_GARMENT = "BROKEN_GARMENT"
    TEXT_OR_LOGO_CORRUPTION = "TEXT_OR_LOGO_CORRUPTION"
    BACKGROUND_WARP = "BACKGROUND_WARP"
    LIGHTING_MISMATCH = "LIGHTING_MISMATCH"
    OTHER = "OTHER"


class OptimizationCriticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_preserved: CriticCheck
    pose_and_composition_preserved: CriticCheck
    background_and_lighting_preserved: CriticCheck
    unmentioned_garments_preserved: CriticCheck
    requested_changes_only: CriticCheck
    artifacts: list[VisualArtifact] = Field(max_length=6)
    overall_pass: bool
    summary: str = Field(min_length=6, max_length=220)

    @model_validator(mode="after")
    def enforce_hard_gate(self) -> "OptimizationCriticOutput":
        checks = (
            self.identity_preserved,
            self.pose_and_composition_preserved,
            self.background_and_lighting_preserved,
            self.unmentioned_garments_preserved,
            self.requested_changes_only,
        )
        expected_pass = all(check.passed for check in checks) and not self.artifacts
        if self.overall_pass != expected_pass:
            raise ValueError("overall pass must equal all hard preservation checks")
        return self
