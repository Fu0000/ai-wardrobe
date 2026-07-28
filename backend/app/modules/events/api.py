from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.config import Settings
from app.core.errors import AppError
from app.core.telemetry import current_trace_fields
from app.modules.events.schemas import ClientEventBatch, ClientEventReceipt
from app.modules.events.service import (
    ClientEventError,
    ClientEventService,
    EventIngestContext,
)
from app.modules.identity.api import current_user
from app.modules.identity.models import User

router = APIRouter()


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.post("/client-events", response_model=ClientEventReceipt)
async def ingest_client_events(
    payload: ClientEventBatch,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClientEventReceipt:
    request_id = str(getattr(request.state, "request_id", "unavailable"))
    trace_id = current_trace_fields().get("trace_id", "unavailable")
    try:
        receipt = await ClientEventService(session, _settings(request)).ingest(
            batch=payload,
            context=EventIngestContext(
                user_id=user.id,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
    except ClientEventError as error:
        raise AppError(
            code="CLIENT_EVENT_ENTITY_INVALID",
            message="埋点关联对象无效。",
            status_code=422,
        ) from error
    return ClientEventReceipt(
        accepted_count=receipt.accepted_count,
        duplicate_count=receipt.duplicate_count,
    )
