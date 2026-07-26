import asyncio
from contextlib import AbstractContextManager
from time import perf_counter
from types import TracebackType

import structlog
from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.core.config import Settings


class TelemetryRuntime:
    def __init__(
        self,
        trace_provider: TracerProvider | None,
        meter_provider: MeterProvider | None,
        *,
        owns_providers: bool,
    ) -> None:
        self._trace_provider = trace_provider
        self._meter_provider = meter_provider
        self._owns_providers = owns_providers

    async def close(self) -> None:
        if not self._owns_providers:
            return
        if self._meter_provider is not None:
            await asyncio.to_thread(self._meter_provider.shutdown)
        if self._trace_provider is not None:
            await asyncio.to_thread(self._trace_provider.shutdown)


_runtime: TelemetryRuntime | None = None
_TRACE_HEADER_ALLOWLIST = frozenset({"traceparent", "tracestate"})
_meter = metrics.get_meter("ai_wardrobe")
_http_requests = _meter.create_counter("aiw.http.server.requests", unit="{request}")
_http_duration = _meter.create_histogram("aiw.http.server.duration", unit="ms")
_worker_executions = _meter.create_counter("aiw.worker.executions", unit="{execution}")
_worker_duration = _meter.create_histogram("aiw.worker.duration", unit="ms")
_ai_provider_attempts = _meter.create_counter("aiw.ai.provider.attempts", unit="{attempt}")
_ai_provider_duration = _meter.create_histogram("aiw.ai.provider.duration", unit="ms")
_outbox_publishes = _meter.create_counter("aiw.outbox.publishes", unit="{event}")
_outbox_pending = _meter.create_gauge("aiw.outbox.pending", unit="{event}")
_outbox_failed = _meter.create_gauge("aiw.outbox.failed", unit="{event}")
_outbox_dead_letter = _meter.create_gauge("aiw.outbox.dead_letter", unit="{event}")
_outbox_oldest_pending_age = _meter.create_gauge(
    "aiw.outbox.oldest_pending_age",
    unit="s",
)
_product_actions = _meter.create_counter("aiw.product.actions", unit="{action}")

_WORKER_OUTCOMES = frozenset(
    {
        "completed",
        "failed_final",
        "retry_scheduled",
        "skipped_stale",
        "timed_out",
        "cancelled",
        "published",
        "success",
    }
)
_PRODUCT_ACTIONS = frozenset(
    {
        "diagnosis_requested",
        "optimization_requested",
        "share_requested",
        "vote_recorded",
        "continue_recorded",
        "feedback_submitted",
    }
)


def configure_telemetry(
    settings: Settings,
    *,
    service_name: str | None = None,
) -> TelemetryRuntime:
    global _runtime
    if not settings.otel_enabled:
        return TelemetryRuntime(None, None, owns_providers=False)
    if _runtime is not None:
        return TelemetryRuntime(None, None, owns_providers=False)

    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    base_endpoint = endpoint.removesuffix("/v1/traces").removesuffix("/v1/metrics").rstrip("/")
    resource = Resource.create(
        {
            "service.name": service_name or settings.otel_service_name,
            "service.version": __version__,
            "deployment.environment.name": settings.environment,
        }
    )
    trace_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{base_endpoint}/v1/traces",
                timeout=settings.otel_export_timeout_seconds,
            )
        )
    )
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=f"{base_endpoint}/v1/metrics",
            timeout=settings.otel_export_timeout_seconds,
        ),
        export_interval_millis=settings.otel_metrics_export_interval_seconds * 1_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    trace.set_tracer_provider(trace_provider)
    metrics.set_meter_provider(meter_provider)
    _runtime = TelemetryRuntime(
        trace_provider,
        meter_provider,
        owns_providers=True,
    )
    return _runtime


def current_trace_fields() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": trace.format_trace_id(context.trace_id),
        "span_id": trace.format_span_id(context.span_id),
    }


def inject_trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return {key: value for key, value in carrier.items() if key.lower() in _TRACE_HEADER_ALLOWLIST}


def record_ai_provider_attempt(
    *,
    operation: str,
    provider: str,
    model: str,
    outcome: str,
    duration_ms: int,
) -> None:
    attributes = {
        "gen_ai.operation.name": operation,
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "aiw.outcome": outcome,
    }
    _ai_provider_attempts.add(1, attributes)
    _ai_provider_duration.record(duration_ms, attributes)


def record_outbox_publish(*, event_type: str, outcome: str) -> None:
    _outbox_publishes.add(
        1,
        {
            "messaging.operation.name": "publish",
            "messaging.message.type": event_type,
            "aiw.outcome": outcome,
        },
    )


def record_outbox_backlog(
    *,
    pending_count: int,
    failed_count: int,
    dead_letter_count: int,
    oldest_pending_age_seconds: float,
) -> None:
    _outbox_pending.set(pending_count)
    _outbox_failed.set(failed_count)
    _outbox_dead_letter.set(dead_letter_count)
    _outbox_oldest_pending_age.set(oldest_pending_age_seconds)


def record_product_action(*, action: str, outcome: str) -> None:
    if action not in _PRODUCT_ACTIONS:
        raise ValueError("unsupported product action")
    if outcome not in {"created", "reused", "attributed", "unattributed"}:
        raise ValueError("unsupported product action outcome")
    _product_actions.add(
        1,
        {
            "aiw.product.action": action,
            "aiw.outcome": outcome,
        },
    )


class WorkerSpan(AbstractContextManager["WorkerSpan"]):
    def __init__(
        self,
        *,
        name: str,
        headers: object,
        job_id: str | None,
    ) -> None:
        carrier = (
            {
                str(key): str(value)
                for key, value in headers.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(headers, dict)
            else {}
        )
        parent = propagate.extract(carrier)
        attributes: dict[str, str] = {"messaging.system": "celery"}
        if job_id is not None:
            attributes["aiw.job.id"] = job_id
        self._manager = trace.get_tracer("ai_wardrobe.worker").start_as_current_span(
            name,
            context=parent,
            kind=SpanKind.CONSUMER,
            attributes=attributes,
        )
        self._name = name
        self._started_at = perf_counter()
        self._outcome = "success"

    def __enter__(self) -> "WorkerSpan":
        self._manager.__enter__()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**current_trace_fields())
        return self

    def set_outcome(self, outcome: str) -> None:
        normalized = outcome.strip().lower()
        self._outcome = normalized if normalized in _WORKER_OUTCOMES else "unknown"

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return self._manager.__exit__(exc_type, exc_value, traceback)
        finally:
            attributes = {
                "messaging.system": "celery",
                "messaging.operation.name": self._name,
                "aiw.outcome": "error" if exc_type is not None else self._outcome,
            }
            _worker_executions.add(1, attributes)
            _worker_duration.record(
                round((perf_counter() - self._started_at) * 1_000),
                attributes,
            )
            structlog.contextvars.clear_contextvars()


class RequestTelemetryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._tracer = trace.get_tracer("ai_wardrobe.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health/live":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        parent = propagate.extract(headers)
        method = str(scope["method"])
        status_code = 500
        started_at = perf_counter()

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        with self._tracer.start_as_current_span(
            f"HTTP {method}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method},
        ) as span:
            try:
                await self.app(scope, receive, capture_status)
            except BaseException as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = scope.get("route")
                route_path = getattr(route, "path", None)
                if isinstance(route_path, str):
                    span.update_name(f"{method} {route_path}")
                    span.set_attribute("http.route", route_path)
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                metric_attributes: dict[str, str | int] = {
                    "http.request.method": method,
                    "http.route": route_path if isinstance(route_path, str) else "UNMATCHED",
                    "http.response.status_code": status_code,
                }
                _http_requests.add(1, metric_attributes)
                _http_duration.record(
                    round((perf_counter() - started_at) * 1_000, 2),
                    metric_attributes,
                )
