from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import AsyncClient, Timeout
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router, health_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    RequestBodyLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    TrustedProxyHeadersMiddleware,
)
from app.core.rate_limit import build_rate_limit_guard
from app.core.readiness import UninitializedReadinessProbe, build_readiness_probe
from app.core.telemetry import (
    RequestTelemetryMiddleware,
    TelemetryRuntime,
    configure_telemetry,
)
from app.database.session import Database
from app.modules.assets.storage import build_object_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        telemetry: TelemetryRuntime = configure_telemetry(app_settings)
        database = Database(app_settings)
        http_client = AsyncClient(
            timeout=Timeout(10),
            headers={"User-Agent": f"ai-wardrobe-api/{__version__}"},
        )
        lifespan_app.state.database = database
        lifespan_app.state.http_client = http_client
        lifespan_app.state.object_storage = build_object_storage(app_settings)
        lifespan_app.state.readiness_probe = build_readiness_probe(
            settings=app_settings,
            database=database,
            object_storage=lifespan_app.state.object_storage,
        )
        try:
            yield
        finally:
            await http_client.aclose()
            await lifespan_app.state.rate_limit_guard.close()
            await lifespan_app.state.readiness_probe.close()
            await database.dispose()
            await telemetry.close()

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        docs_url="/docs" if app_settings.expose_api_docs else None,
        redoc_url="/redoc" if app_settings.expose_api_docs else None,
        openapi_url="/openapi.json" if app_settings.expose_api_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.rate_limit_guard = build_rate_limit_guard(app_settings)
    app.state.readiness_probe = UninitializedReadinessProbe()
    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                app_settings.request_id_header,
            ],
            expose_headers=[
                app_settings.request_id_header,
                "X-Trace-ID",
                "Retry-After",
            ],
            max_age=600,
        )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_json_body_bytes=app_settings.max_json_body_bytes,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=app_settings.environment in {"staging", "production"},
    )
    app.add_middleware(
        RequestContextMiddleware,
        request_id_header=app_settings.request_id_header,
    )
    app.add_middleware(RequestTelemetryMiddleware)
    app.add_middleware(
        TrustedProxyHeadersMiddleware,
        trusted_proxy_cidrs=app_settings.trusted_proxy_cidrs,
    )
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=app_settings.api_v1_prefix)
    return app


app = create_app()
