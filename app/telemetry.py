"""OpenTelemetry 初始化。

视频生成是长耗时任务，必须能在 Trace 里看到每个阶段（提交/采样/解码）的耗时。
- 设了 WAN_OTEL_EXPORTER_OTLP_ENDPOINT → 走 OTLP/HTTP 上报到 Collector（生产）
- 没设 → ConsoleSpanExporter 打到 stdout（本地联调，docker logs 直接看）
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

from .config import settings

_initialized = False


def init_telemetry() -> None:
    global _initialized
    if _initialized:
        return
    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _initialized = True


def instrument_app(app) -> None:
    """对 FastAPI 路由和出站 httpx 调用做自动埋点。"""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
