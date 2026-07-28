from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app import __version__
from app.core.config import Settings
from app.core.errors import AppError, ErrorResponse
from app.core.rate_limit import enforce_ip_rate_limit
from app.core.readiness import ReadinessProbe
from app.core.telemetry import record_dependency_readiness
from app.modules.assets.api import router as assets_router
from app.modules.diagnosis.api import router as diagnosis_router
from app.modules.feedback.api import router as feedback_router
from app.modules.governance.deletion_api import router as deletion_router
from app.modules.growth.api import router as growth_router
from app.modules.identity.api import router as identity_router
from app.modules.jobs.api import router as jobs_router
from app.modules.optimization.api import router as optimization_router

health_router = APIRouter(tags=["health"])
api_router = APIRouter(dependencies=[Depends(enforce_ip_rate_limit)])
api_router.include_router(identity_router, tags=["identity"])
api_router.include_router(assets_router, tags=["assets"])
api_router.include_router(diagnosis_router, tags=["diagnosis"])
api_router.include_router(optimization_router, tags=["optimization"])
api_router.include_router(growth_router, tags=["growth"])
api_router.include_router(deletion_router, tags=["governance"])
api_router.include_router(feedback_router, tags=["feedback"])
api_router.include_router(jobs_router, tags=["jobs"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "ready"]
    service: str
    version: str
    environment: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready"]
    service: str
    version: str
    environment: str
    timestamp: datetime
    dependencies: dict[str, Literal["ok", "disabled"]]


class ServiceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    environment: str


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@health_router.get("/health/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    settings = _settings(request)
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
    )


@health_router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse}},
)
async def readiness(request: Request) -> ReadinessResponse:
    settings = _settings(request)
    probe: ReadinessProbe = request.app.state.readiness_probe
    report = await probe.check()
    record_dependency_readiness(report.dependencies)
    if not report.ready:
        raise AppError(
            code="SERVICE_NOT_READY",
            message="服务依赖尚未就绪。",
            status_code=503,
            details=[
                {"dependency": name, "status": status}
                for name, status in report.dependencies.items()
            ],
            headers={"Retry-After": "3"},
        )
    safe_dependencies = {
        name: status for name, status in report.dependencies.items() if status in {"ok", "disabled"}
    }
    return ReadinessResponse(
        status="ready",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        timestamp=datetime.now(UTC),
        dependencies=safe_dependencies,
    )


@api_router.get("/meta", response_model=ServiceMetadata, tags=["meta"])
async def service_metadata(request: Request) -> ServiceMetadata:
    settings = _settings(request)
    return ServiceMetadata(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )
