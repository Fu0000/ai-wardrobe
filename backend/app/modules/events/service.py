import hashlib
import hmac
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.assets.models import UserAsset
from app.modules.diagnosis.models import StyleDiagnosis, StyleOptimizationResult
from app.modules.events.schemas import (
    AssetUploadInterruptedEvent,
    AssetUploadStartedEvent,
    ClientEvent,
    ClientEventBatch,
    DiagnosisOptimizationClickedEvent,
    DiagnosisResultViewedEvent,
    OptimizationBeforeAfterViewedEvent,
)
from app.modules.growth.models import UserEvent


@dataclass(frozen=True, slots=True)
class EventIngestContext:
    user_id: UUID
    request_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class EventIngestReceipt:
    accepted_count: int
    duplicate_count: int


class ClientEventError(Exception):
    pass


def privacy_safe_user_hash(settings: Settings, user_id: UUID) -> str:
    return hmac.new(
        settings.identity_hmac_key.get_secret_value().encode(),
        f"user-event:{user_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _entity(event: ClientEvent) -> tuple[str, UUID]:
    if isinstance(event, (AssetUploadStartedEvent, AssetUploadInterruptedEvent)):
        return "UserAsset", event.properties.asset_id
    if isinstance(event, (DiagnosisResultViewedEvent, DiagnosisOptimizationClickedEvent)):
        return "StyleDiagnosis", event.properties.diagnosis_id
    if isinstance(event, OptimizationBeforeAfterViewedEvent):
        return "StyleOptimizationResult", event.properties.optimization_id
    raise TypeError("unsupported client event")


def build_event_rows(
    *,
    settings: Settings,
    batch: ClientEventBatch,
    context: EventIngestContext,
) -> list[dict[str, object]]:
    user_hash = privacy_safe_user_hash(settings, context.user_id)
    rows: list[dict[str, object]] = []
    for event in batch.events:
        entity_type, entity_id = _entity(event)
        rows.append(
            {
                "id": event.event_id,
                "user_id": context.user_id,
                "event_name": event.event_name,
                "event_version": event.event_version,
                "occurred_at": event.occurred_at,
                "environment": settings.environment,
                "trace_id": context.trace_id,
                "request_id": context.request_id,
                "user_id_hash": user_hash,
                "session_id": event.session_id,
                "client_version": event.client_version,
                "platform": event.platform,
                "app_channel": event.app_channel,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "dedupe_key": f"client:{event.event_id}",
                "properties": event.properties.model_dump(mode="json"),
            }
        )
    return rows


class ClientEventService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def ingest(
        self,
        *,
        batch: ClientEventBatch,
        context: EventIngestContext,
    ) -> EventIngestReceipt:
        rows = build_event_rows(
            settings=self._settings,
            batch=batch,
            context=context,
        )
        await self._assert_entity_ownership(
            rows=rows,
            user_id=context.user_id,
        )
        statement = insert(UserEvent).values(rows).on_conflict_do_nothing().returning(UserEvent.id)
        result = await self._session.execute(statement)
        accepted_count = len(list(result.scalars()))
        await self._session.flush()
        return EventIngestReceipt(
            accepted_count=accepted_count,
            duplicate_count=len(rows) - accepted_count,
        )

    async def _assert_entity_ownership(
        self,
        *,
        rows: list[dict[str, object]],
        user_id: UUID,
    ) -> None:
        assets = {cast(UUID, row["entity_id"]) for row in rows if row["entity_type"] == "UserAsset"}
        diagnoses = {
            cast(UUID, row["entity_id"]) for row in rows if row["entity_type"] == "StyleDiagnosis"
        }
        optimizations = {
            cast(UUID, row["entity_id"])
            for row in rows
            if row["entity_type"] == "StyleOptimizationResult"
        }

        owned_assets: set[UUID] = set()
        if assets:
            result = await self._session.execute(
                select(UserAsset.id).where(
                    UserAsset.user_id == user_id,
                    UserAsset.id.in_(assets),
                )
            )
            owned_assets = set(result.scalars())

        owned_diagnoses: set[UUID] = set()
        if diagnoses:
            result = await self._session.execute(
                select(StyleDiagnosis.id).where(
                    StyleDiagnosis.user_id == user_id,
                    StyleDiagnosis.id.in_(diagnoses),
                )
            )
            owned_diagnoses = set(result.scalars())

        owned_optimizations: set[UUID] = set()
        if optimizations:
            result = await self._session.execute(
                select(StyleOptimizationResult.id).where(
                    StyleOptimizationResult.user_id == user_id,
                    StyleOptimizationResult.id.in_(optimizations),
                )
            )
            owned_optimizations = set(result.scalars())

        if (
            owned_assets != assets
            or owned_diagnoses != diagnoses
            or owned_optimizations != optimizations
        ):
            raise ClientEventError("CLIENT_EVENT_ENTITY_INVALID")
