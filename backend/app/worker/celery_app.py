import asyncio

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from app import __version__
from app.core.config import get_settings
from app.core.telemetry import TelemetryRuntime, configure_telemetry

settings = get_settings()
telemetry_runtime: TelemetryRuntime | None = None
celery_app = Celery(
    "ai_wardrobe",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=3_600,
    task_default_queue="ai_fast",
    task_routes={
        "ai_wardrobe.dispatch_outbox": {"queue": "maintenance"},
        "ai_wardrobe.run_style_diagnosis": {"queue": "ai_fast"},
        "ai_wardrobe.run_style_optimization": {"queue": "image_generation"},
        "ai_wardrobe.run_share_asset": {"queue": "media_generation"},
        "ai_wardrobe.run_deletion": {"queue": "maintenance"},
    },
    beat_schedule={
        "dispatch-outbox": {
            "task": "ai_wardrobe.dispatch_outbox",
            "schedule": settings.outbox_dispatch_interval_seconds,
        }
    },
)
# This is non-sensitive, pod-local recovery metadata stored on the dedicated
# emptyDir mounted at /tmp in Kubernetes.
celery_app.conf.worker_state_db = f"/tmp/.celery-state-{__version__}"  # noqa: S108


@worker_process_init.connect(weak=False)  # type: ignore[untyped-decorator]
def initialize_worker_telemetry(**_: object) -> None:
    global telemetry_runtime
    telemetry_runtime = configure_telemetry(
        settings,
        service_name=f"{settings.otel_service_name}-worker",
    )


@worker_process_shutdown.connect(weak=False)  # type: ignore[untyped-decorator]
def shutdown_worker_telemetry(**_: object) -> None:
    if telemetry_runtime is not None:
        asyncio.run(telemetry_runtime.close())
