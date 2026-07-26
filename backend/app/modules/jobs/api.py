from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.core.errors import AppError
from app.modules.identity.api import current_user
from app.modules.identity.models import User
from app.modules.jobs.models import JobStatus, JobTaskType
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.service import JobApplicationService, JobServiceError

router = APIRouter()


class JobResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    task_type: JobTaskType
    status: JobStatus
    progress: int
    retry_count: int
    error_code: str | None
    user_message: str | None
    result_reference_type: str | None
    result_reference_id: UUID | None
    created_at: datetime
    updated_at: datetime


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JobResponse:
    try:
        job = await JobApplicationService(JobRepository(session)).get_owned(
            job_id=job_id,
            user_id=user.id,
        )
    except JobServiceError as error:
        raise AppError(
            code="JOB_NOT_FOUND",
            message="任务不存在。",
            status_code=404,
        ) from error

    return JobResponse(
        id=job.id,
        task_type=job.task_type,
        status=job.status,
        progress=job.progress,
        retry_count=job.retry_count,
        error_code=job.error_code,
        user_message=job.user_message,
        result_reference_type=job.result_reference_type,
        result_reference_id=job.result_reference_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
