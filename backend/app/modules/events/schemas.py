from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientEventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_version: Literal[1]
    occurred_at: datetime
    session_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    client_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9._+-]+$",
    )
    platform: Literal["mp-weixin"]
    app_channel: Literal["wechat"]

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        normalized = value.astimezone(UTC)
        now = datetime.now(UTC)
        if normalized > now + timedelta(minutes=10):
            raise ValueError("occurred_at is too far in the future")
        if normalized < now - timedelta(days=30):
            raise ValueError("occurred_at is older than the retention window")
        return normalized


class AssetUploadStartedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    size_bucket: Literal["lt_1mb", "1_to_5mb", "5_to_10mb", "10_to_20mb"]


class AssetUploadStartedEvent(ClientEventBase):
    event_name: Literal["asset.upload.started"]
    properties: AssetUploadStartedProperties


class AssetUploadInterruptedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    reason: Literal["network", "timeout", "cancelled", "unknown"]
    progress_bucket: Literal["0_to_24", "25_to_49", "50_to_74", "75_to_99"]


class AssetUploadInterruptedEvent(ClientEventBase):
    event_name: Literal["asset.upload.interrupted"]
    properties: AssetUploadInterruptedProperties


class DiagnosisResultViewedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis_id: UUID
    score_bucket: Literal["unknown", "0_to_59", "60_to_79", "80_to_100"]


class DiagnosisResultViewedEvent(ClientEventBase):
    event_name: Literal["diagnosis.result.viewed"]
    properties: DiagnosisResultViewedProperties


class DiagnosisOptimizationClickedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis_id: UUID


class DiagnosisOptimizationClickedEvent(ClientEventBase):
    event_name: Literal["diagnosis.optimization.clicked"]
    properties: DiagnosisOptimizationClickedProperties


class OptimizationBeforeAfterViewedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    optimization_id: UUID


class OptimizationBeforeAfterViewedEvent(ClientEventBase):
    event_name: Literal["optimization.before_after.viewed"]
    properties: OptimizationBeforeAfterViewedProperties


ClientEvent = Annotated[
    AssetUploadStartedEvent
    | AssetUploadInterruptedEvent
    | DiagnosisResultViewedEvent
    | DiagnosisOptimizationClickedEvent
    | OptimizationBeforeAfterViewedEvent,
    Field(discriminator="event_name"),
]


class ClientEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ClientEvent] = Field(min_length=1, max_length=20)


class ClientEventReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted_count: int
    duplicate_count: int
